import json
from functools import partial
from unittest.mock import Mock

import httpx
import pytest

from backend.app.checks.registry import CHECK_REGISTRY, extraction_check_catalog
from backend.app.core.config import OllamaSettings, load_ollama_settings
from backend.app.inference.ollama_client import OllamaClientError, propose_rules
from backend.app.inference.rule_proposals import compact_response_schema, extraction_catalog
from backend.app.models.rule import RuleProposal
from backend.app.services import rule_extraction
from backend.app.storage.repository import RulebookRepository


GUIDELINES = "Allowed labels are person and vehicle."
PROPOSAL = {
    "rule_id": "RULE-1",
    "description": GUIDELINES,
    "check_name": "allowed_labels",
    "parameters": {"allowed_labels": ["person", "vehicle"]},
    "source_sentence": GUIDELINES,
}


def check_id(name):
    return next(entry['check_id'] for entry in extraction_catalog() if entry['check_name'] == name)


WIRE_PROPOSAL = {
    'source_id': 1, 'context_ids': [], 'check_id': check_id('allowed_labels'),
    'parameters': {'allowed_labels': ['person', 'vehicle']},
}


LINE_1 = [{'source_id': 1, 'role': 'requirement'}]


def wire(rules, lines=LINE_1):
    """A compact model response: every line classified, then the rule mappings."""
    return json.dumps({'lines': lines, 'rules': rules})


@pytest.fixture(autouse=True)
def isolated_ollama_configuration(tmp_path, monkeypatch):
    from backend.app.core import config

    for name in ("OLLAMA_MODEL", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config, "DEFAULT_ENV_PATH", tmp_path / ".env")


@pytest.fixture
def fake_server():
    state = {
        "requests": [],
        "show_status": 200,
        "show": {"model_info": {"general.architecture": "qwen3vl"}},
        "chat_status": 200,
        "chat": {"done": True, "done_reason": "stop", "message": {
            "content": wire([WIRE_PROPOSAL])
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
    assert request["keep_alive"] == 300
    assert json.loads(request["messages"][1]["content"]) == {
        'sources': [{'source_id': 1, 'text': GUIDELINES}],
    }
    schema = request["format"]
    rule_schema = schema['properties']['rules']['items']
    assert set(rule_schema['properties']) == {'source_id', 'context_ids', 'check_id', 'parameters'}
    assert rule_schema['additionalProperties'] is False
    assert GUIDELINES not in json.dumps(state["requests"][0][1])
    instructions = request["messages"][0]["content"]
    for entry in extraction_check_catalog():
        assert entry["check_name"] in instructions
        assert entry["meaning"] in instructions
        assert entry["scope"] in instructions
        assert json.dumps(entry["parameters_schema"], sort_keys=True) in instructions
    assert "5%" in instructions and "0.05" in instructions
    assert "plain language" in instructions


def test_empty_proposals_are_valid(fake_server):
    state, transport = fake_server
    state["chat"]["message"]["content"] = wire([], lines=[{'source_id': 1, 'role': 'other'}])
    assert propose_rules(GUIDELINES, transport=transport) == []


@pytest.mark.parametrize("field, value", [
    ("origin", "extracted"),
    ("origin", "reviewer"),
    ("confirmation_state", "draft"),
    ("confirmation_state", "confirmed"),
    ("confirmation_state", "rejected"),
    ('rule_id', 'invented'),
    ('description', 'Invented requirement.'),
    ('source_sentence', 'Invented citation.'),
])
def test_model_cannot_supply_text_or_application_owned_metadata(fake_server, field, value):
    state, transport = fake_server
    state["chat"]["message"]["content"] = wire([{**WIRE_PROPOSAL, field: value}])
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "invalid_rule_proposals"


def test_ambiguous_requirement_is_retained_without_check(fake_server):
    state, transport = fake_server
    state["chat"]["message"]["content"] = wire([{**WIRE_PROPOSAL, "check_id": 0, "parameters": {}}])
    proposal = propose_rules(GUIDELINES, transport=transport)[0]
    assert proposal.check_name is None and proposal.parameters == {}


def test_unknown_non_null_check_is_rejected(fake_server):
    state, transport = fake_server
    state["chat"]["message"]["content"] = wire(
        [{**WIRE_PROPOSAL, "check_id": len(extraction_catalog()) + 1, "parameters": {}}])
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "invalid_rule_proposals"


def test_fraction_requirement_mapping_is_preserved_and_validated(fake_server):
    state, transport = fake_server
    source = "At most 5% of the mask may be outside the box."
    state["chat"]["message"]["content"] = wire([{
        "source_id": 1, "context_ids": [], "check_id": check_id('mask_outside_box'),
        "parameters": {"max_outside_fraction": 0.05},
    }])
    proposal = propose_rules(source, transport=transport)[0]
    assert proposal.check_name == "mask_outside_box"
    assert proposal.parameters == {"max_outside_fraction": 0.05}


@pytest.mark.parametrize("changes", [
    {"check_id": check_id('mask_outside_box'), "parameters": {}},
    {"check_id": check_id('mask_outside_box'), "parameters": {"max_outside_fraction": 1.1}},
    {"check_id": check_id('mask_outside_box'), "parameters": {"max_outside_fraction": "0.05"}},
    {"check_id": check_id('box_validity'), "parameters": {"unexpected": True}},
    {"check_id": check_id('box_validity'), "parameters": {"allowed_labels": ['person']}},
    {"check_id": 0, "parameters": {"max_outside_fraction": 0.05}},
])
def test_known_check_mappings_are_validated_against_the_registry(fake_server, changes):
    state, transport = fake_server
    state["chat"]["message"]["content"] = wire([{**WIRE_PROPOSAL, **changes}])
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "invalid_rule_proposals"


def test_registry_catalog_is_complete():
    catalog = extraction_check_catalog()
    assert [entry["check_name"] for entry in catalog] == sorted(CHECK_REGISTRY)
    assert all(entry["meaning"] for entry in catalog)
    assert all(entry["check_name"] == CHECK_REGISTRY[entry["check_name"]].name for entry in catalog)
    assert {entry["scope"] for entry in catalog} == {
        definition.scope for definition in CHECK_REGISTRY.values()
    }


def test_source_context_is_retained_verbatim_without_model_copying(fake_server):
    state, transport = fake_server
    lines = [
        '1. Scope: only person annotations are assessed.',
        '  2. A person means a visible human, not a person on a poster.',
        '3. A fully visible person must have their feet inside the box.',
        '4. For an occluded person, annotate only the visible portion.',
    ]
    text = '\r\n' + '\r\n'.join(lines) + '\r\n\r\n'
    state['chat']['message']['content'] = wire([{
        'source_id': 3, 'context_ids': [1, 2, 4],
        'check_id': check_id('annotation_guideline'), 'parameters': {},
    }], lines=[{'source_id': 1, 'role': 'context'}, {'source_id': 2, 'role': 'context'},
               {'source_id': 3, 'role': 'requirement'}, {'source_id': 4, 'role': 'requirement'}])
    proposals = propose_rules(text, transport=transport)
    proposal = proposals[0]
    assert proposal.source_sentence == lines[2] and proposal.source_sentence in text
    # Context is structural now: the description is the requirement alone.
    assert proposal.description == lines[2]
    assert proposal.context_sentences == [lines[0], lines[1], lines[3]]
    supplied = json.loads(state['requests'][1][1]['messages'][1]['content'])['sources']
    assert supplied == [{'source_id': i, 'text': line} for i, line in enumerate(lines, start=1)]
    assert proposal.check_name == 'annotation_guideline' and proposal.parameters == {}


def test_distinct_checks_can_reference_the_same_one_line_paragraph(fake_server):
    state, transport = fake_server
    text = GUIDELINES + ' Every bounding box must be inside the image.'
    state['chat']['message']['content'] = wire([
        WIRE_PROPOSAL,
        {'source_id': 1, 'context_ids': [], 'check_id': check_id('box_validity'), 'parameters': {}},
    ])
    proposals = propose_rules(text, transport=transport)
    assert [p.rule_id for p in proposals] == ['RULE-1', 'RULE-2']
    assert [p.check_name for p in proposals] == ['allowed_labels', 'box_validity']
    assert all(p.source_sentence == text and p.description == text for p in proposals)


@pytest.mark.parametrize('change', [
    {'source_id': 0}, {'source_id': True}, {'source_id': 1.0}, {'source_id': '1'},
    {'source_id': 3}, {'context_ids': [0]}, {'context_ids': [True]},
    {'context_ids': [1]}, {'context_ids': [2, 2]}, {'context_ids': [3]},
    {'check_id': -1}, {'check_id': True}, {'check_id': 1.0},
])
def test_invalid_source_and_check_references_are_rejected(fake_server, change):
    state, transport = fake_server
    state['chat']['message']['content'] = wire([{**WIRE_PROPOSAL, **change}])
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES + '\nOnly visible objects are in scope.', transport=transport)
    assert caught.value.reason == 'invalid_rule_proposals'


def test_unmapped_requirement_keeps_original_text_and_context(fake_server):
    state, transport = fake_server
    lines = ['Only people are in scope.', 'A person must appear in the next video frame.']
    state['chat']['message']['content'] = wire([{
        'source_id': 2, 'context_ids': [1], 'check_id': 0, 'parameters': {},
    }], lines=[{'source_id': 1, 'role': 'context'}, {'source_id': 2, 'role': 'requirement'}])
    proposal = propose_rules('\n'.join(lines), transport=transport)[0]
    assert proposal.check_name is None
    assert proposal.source_sentence == lines[1]
    assert proposal.description == lines[1] and proposal.context_sentences == [lines[0]]


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
    wire([{**WIRE_PROPOSAL, "source_id": None}]),
    wire([{key: value for key, value in WIRE_PROPOSAL.items() if key != "source_id"}]),
    wire([{**WIRE_PROPOSAL, "source_sentence": "Invented citation."}]),
])
def test_invalid_structured_model_output_is_rejected(fake_server, content):
    state, transport = fake_server
    state["chat"]["message"]["content"] = content
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "invalid_rule_proposals"


@pytest.mark.parametrize("proposals", [
    [WIRE_PROPOSAL, WIRE_PROPOSAL],
    [{**WIRE_PROPOSAL, "source_id": 999}],
])
def test_invalid_rulebook_content_from_model_is_never_saved(
    fake_server, monkeypatch, proposals
):
    state, transport = fake_server
    state["chat"]["message"]["content"] = wire(proposals)
    monkeypatch.setattr(rule_extraction, "propose_rules", partial(propose_rules, transport=transport))
    repository = Mock(spec_set=RulebookRepository)

    with pytest.raises(OllamaClientError) as caught:
        rule_extraction.extract_and_save_rulebook(GUIDELINES, repository=repository)
    assert caught.value.reason == 'invalid_rule_proposals'
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


@pytest.mark.parametrize("threads", [None, 10])
def test_custom_settings_select_model_and_generation_limits(fake_server, threads):
    state, transport = fake_server
    settings = OllamaSettings(
        model="another-local-model:latest",
        host="http://127.0.0.1:11434",
        timeout_seconds=30.0,
        context_length=4096,
        max_output_tokens=512,
        cpu_threads=threads,
    )
    propose_rules(GUIDELINES, settings=settings, transport=transport)
    assert state["requests"][0][1]["model"] == settings.model
    chat = state["requests"][1][1]
    assert chat["model"] == settings.model
    assert chat["options"]["num_ctx"] == 4096
    assert chat["options"]["num_predict"] == 512
    assert chat["options"]["num_gpu"] == 0
    assert chat["options"].get("num_thread") == threads
    assert ("num_thread" in chat["options"]) == (threads is not None)


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


@pytest.mark.parametrize("per_call", [False, True])
def test_dotenv_model_and_port_reach_requests_with_per_call_precedence(fake_server, per_call):
    from backend.app.core import config

    config.DEFAULT_ENV_PATH.write_text(
        "OLLAMA_MODEL=dotenv-model:latest\n"
        "OLLAMA_BASE_URL=http://127.0.0.1:11435\n",
        encoding="utf-8",
    )
    state, transport = fake_server
    urls = []

    def handle(request):
        urls.append(request.url)
        return transport.handle_request(request)

    overrides = {"model": "per-call:latest", "host": "http://127.0.0.1:11436"} if per_call else {}
    propose_rules(GUIDELINES, transport=httpx.MockTransport(handle), **overrides)
    assert [url.port for url in urls] == [11436 if per_call else 11435] * 2
    expected_model = "per-call:latest" if per_call else "dotenv-model:latest"
    assert [body["model"] for _, body in state["requests"]] == [expected_model] * 2


def test_generation_schema_rejects_schema_keywords_in_place_of_label_values():
    from jsonschema import Draft202012Validator
    validator = Draft202012Validator(compact_response_schema(1))
    assert not list(validator.iter_errors({"lines": LINE_1, "rules": [WIRE_PROPOSAL]}))
    assert list(validator.iter_errors({"rules": [WIRE_PROPOSAL]}))          # lines are required
    assert list(validator.iter_errors({"lines": [{"source_id": 1, "role": "definition"}], "rules": []}))
    malformed = {**WIRE_PROPOSAL, "parameters": {"allowed_labels": {
        "items": ["person", "PASperson"], "minItems": 2,
    }}}
    assert list(validator.iter_errors({"lines": LINE_1, "rules": [malformed]}))
    assert list(validator.iter_errors({"lines": LINE_1, 'rules': [{**WIRE_PROPOSAL, 'source_id': 2}]}))
    assert not list(validator.iter_errors({"lines": LINE_1, 'rules': [{**WIRE_PROPOSAL, 'check_id': 0, 'parameters': {}}]}))
    assert list(validator.iter_errors({"lines": LINE_1, 'rules': [{key: value for key, value in WIRE_PROPOSAL.items() if key != 'parameters'}]}))


def test_runtime_still_rejects_schema_keywords_instead_of_a_label_list(fake_server):
    state, transport = fake_server
    state["chat"]["message"]["content"] = wire([{**WIRE_PROPOSAL, "parameters": {
        "allowed_labels": {"items": ["person", "PASperson"], "minItems": 2},
    }}])
    with pytest.raises(OllamaClientError) as caught:
        propose_rules(GUIDELINES, transport=transport)
    assert caught.value.reason == "invalid_rule_proposals"
