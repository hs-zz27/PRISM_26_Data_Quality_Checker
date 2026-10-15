import json
from functools import partial
from unittest.mock import Mock

import httpx
import pytest
from pydantic import ValidationError

from backend.app.core.config import OllamaSettings, load_ollama_settings
from backend.app.inference.ollama_client import OllamaClientError, propose_rules
from backend.app.models.rule import RuleProposal
from backend.app.services import rule_extraction
from backend.app.storage.repository import RulebookRepository


GUIDELINES = "Allowed labels are person and vehicle."
PROPOSAL = {
    "rule_id": "LBL-1",
    "description": "Use an allowed label.",
    "check_name": "allowed_labels",
    "parameters": {"allowed_labels": ["person", "vehicle"]},
    "source_sentence": GUIDELINES,
}


@pytest.fixture
def fake_server():
    state = {
        "requests": [],
        "show_status": 200,
        "show": {"model_info": {"general.architecture": "qwen3vl"}},
        "chat_status": 200,
        "chat": {"done": True, "done_reason": "stop", "message": {
            "content": json.dumps({"rules": [PROPOSAL]})
        }},
    }

    def handle(request):
        state["requests"].append((request.url.path, json.loads(request.content)))
        assert request.url.host == "127.0.0.1"
        stage = request.url.path.removeprefix("/api/")
        if stage not in ("show", "chat"):
            raise AssertionError("Unexpected API endpoint, such as an automatic download.")
        if state.get("error_stage") == stage:
            raise state["error_type"]("simulated failure", request=request)
        if "raw_" + stage in state:
            return httpx.Response(state[stage + "_status"], content=state["raw_" + stage])
        return httpx.Response(state[stage + "_status"], json=state[stage])

    return state, httpx.MockTransport(handle)


def test_valid_proposals_preserve_content_and_request_uses_cpu_schema(fake_server):
    state, transport = fake_server
    proposals = propose_rules(GUIDELINES, transport=transport)
    assert len(proposals) == 1
    assert type(proposals[0]) is RuleProposal
    assert proposals[0].model_dump() == PROPOSAL
    assert [path for path, _ in state["requests"]] == ["/api/show", "/api/chat"]
    request = state["requests"][1][1]
    assert request["model"] == load_ollama_settings().model and request["stream"] is False
    assert request["options"]["num_gpu"] == 0 and request["options"]["temperature"] == 0
    assert request["keep_alive"] == 0
    assert request["messages"][1]["content"] == GUIDELINES
    schema = request["format"]
    rule_definition = schema["properties"]["rules"]["items"]["$ref"].rsplit("/", 1)[1]
    rule_schema = schema["$defs"][rule_definition]
    assert "origin" not in rule_schema["properties"]
    assert "confirmation_state" not in rule_schema["properties"]
    assert "source_sentence" in rule_schema["required"]
    assert rule_schema["additionalProperties"] is False
    assert GUIDELINES not in json.dumps(state["requests"][0][1])


def test_empty_proposals_are_valid(fake_server):
    state, transport = fake_server
    state["chat"]["message"]["content"] = '{"rules": []}'
    assert propose_rules(GUIDELINES, transport=transport) == []


@pytest.mark.parametrize("field, value", [
    ("origin", "extracted"),
    ("origin", "reviewer"),
    ("confirmation_state", "draft"),
    ("confirmation_state", "confirmed"),
    ("confirmation_state", "rejected"),
])
def test_model_cannot_supply_application_owned_metadata(fake_server, field, value):
    state, transport = fake_server
    state["chat"]["message"]["content"] = json.dumps({"rules": [{**PROPOSAL, field: value}]})
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "invalid_rule_proposals"


def test_unknown_requirement_is_retained_without_check(fake_server):
    state, transport = fake_server
    state["chat"]["message"]["content"] = json.dumps({"rules": [{**PROPOSAL, "check_name": None}]})
    assert propose_rules(GUIDELINES, transport=transport)[0].check_name is None


def test_missing_model_never_sends_guidelines(fake_server):
    state, transport = fake_server
    state["show_status"] = 404
    state["show"] = {"error": "model not found"}
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "model_unavailable"
    assert len(state["requests"]) == 1


@pytest.mark.parametrize("field", ["remote_host", "remote_model"])
def test_remote_models_are_blocked_before_sending_guidelines(fake_server, field):
    state, transport = fake_server
    state["show"][field] = "remote"
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "remote_model" and len(state["requests"]) == 1


def test_missing_local_model_information_is_blocked(fake_server):
    state, transport = fake_server
    state["show"] = {}
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "invalid_model_metadata"


@pytest.mark.parametrize("stage", ["show", "chat"])
@pytest.mark.parametrize("error_type, reason", [
    (httpx.ReadTimeout, "request_timeout"), (httpx.ConnectError, "server_unavailable")
])
def test_transport_failures_have_clear_reasons(fake_server, stage, error_type, reason):
    state, transport = fake_server
    state.update(error_stage=stage, error_type=error_type)
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == reason


def test_api_failure_is_reported(fake_server):
    state, transport = fake_server
    state["chat_status"] = 500
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "request_failed"


@pytest.mark.parametrize("response", [b"not JSON", b"[]", b'{"done":true,"message":{}}'])
def test_malformed_api_responses_are_rejected(fake_server, response):
    state, transport = fake_server
    state["raw_chat"] = response
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "invalid_response"


@pytest.mark.parametrize("changes", [{"done": False}, {"done_reason": "length"}])
def test_incomplete_generation_is_not_accepted(fake_server, changes):
    state, transport = fake_server
    state["chat"].update(changes)
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "incomplete_response"


@pytest.mark.parametrize("content", [
    "not JSON", '{}', '{"rules": "wrong"}',
    json.dumps({"rules": [{**PROPOSAL, "source_sentence": None}]}),
    json.dumps({"rules": [{key: value for key, value in PROPOSAL.items() if key != "source_sentence"}]}),
    json.dumps({"rules": [{**PROPOSAL, "source_sentence": " "}]}),
])
def test_invalid_structured_model_output_is_rejected(fake_server, content):
    state, transport = fake_server
    state["chat"]["message"]["content"] = content
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "invalid_rule_proposals"


@pytest.mark.parametrize("proposals, error", [
    ([PROPOSAL, PROPOSAL], "Rule IDs must be unique"),
    ([{**PROPOSAL, "source_sentence": "Invented citation."}], "text absent from the guidelines"),
])
def test_invalid_rulebook_content_from_model_is_never_saved(
    fake_server, monkeypatch, proposals, error
):
    state, transport = fake_server
    state["chat"]["message"]["content"] = json.dumps({"rules": proposals})
    monkeypatch.setattr(rule_extraction, "propose_rules", partial(propose_rules, transport=transport))
    repository = Mock(spec_set=RulebookRepository)

    with pytest.raises(ValidationError, match=error):
        rule_extraction.extract_and_save_rulebook(GUIDELINES, repository=repository)

    repository.save_rulebook.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"guideline_text": " "}, {"guideline_text": None}, {"model": ""},
    {"host": "https://ollama.com"}, {"host": "http://192.168.1.2:11434"},
    {"host": "http://user:password@127.0.0.1:11434"},
    {"host": "http://127.0.0.1:11434/other"}, {"host": "http://127.0.0.1:0"},
    {"timeout": 0}, {"timeout": float("nan")}, {"timeout": True},
])
def test_invalid_configuration_never_makes_requests(fake_server, changes):
    state, transport = fake_server
    arguments = {"guideline_text": GUIDELINES, "transport": transport, **changes}
    with pytest.raises(ValueError):
        propose_rules(**arguments)
    assert state["requests"] == []


def test_redirects_are_not_followed(fake_server):
    state, transport = fake_server
    state["show_status"] = 307
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "request_failed" and len(state["requests"]) == 1


def test_custom_settings_select_model_and_generation_limits(fake_server):
    state, transport = fake_server
    settings = OllamaSettings(
        model="another-local-model:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=30.0,
        context_length=4096,
        max_output_tokens=512,
    )
    propose_rules(GUIDELINES, settings=settings, transport=transport)
    assert state["requests"][0][1]["model"] == settings.model
    chat = state["requests"][1][1]
    assert chat["model"] == settings.model
    assert chat["options"]["num_ctx"] == 4096
    assert chat["options"]["num_predict"] == 512
    assert chat["options"]["num_gpu"] == 0


def test_per_call_override_does_not_change_shared_settings(fake_server):
    state, transport = fake_server
    settings = load_ollama_settings()
    propose_rules(GUIDELINES, settings=settings, model="temporary-model:latest", transport=transport)
    assert state["requests"][0][1]["model"] == "temporary-model:latest"
    assert state["requests"][1][1]["model"] == "temporary-model:latest"
    assert settings.model == load_ollama_settings().model


def test_file_changes_are_used_by_subsequent_calls(fake_server, tmp_path, monkeypatch):
    from backend.app.core import config

    path = tmp_path / "ollama.toml"
    original = config.OLLAMA_CONFIG_PATH.read_text()
    path.write_text(original)
    monkeypatch.setattr(config, "OLLAMA_CONFIG_PATH", path)
    state, transport = fake_server
    propose_rules(GUIDELINES, transport=transport)
    path.write_text(original.replace(load_ollama_settings().model, "replacement-model:latest"))
    propose_rules(GUIDELINES, transport=transport)
    assert state["requests"][2][1]["model"] == "replacement-model:latest"
    assert state["requests"][3][1]["model"] == "replacement-model:latest"
