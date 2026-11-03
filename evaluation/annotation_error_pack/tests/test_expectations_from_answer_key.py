"""tools/expectations_from_answer_key.py reads the real answer key; it never touches a model or an archive."""
import importlib.util
import json
from pathlib import Path

import pytest

PACK = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('expectations_from_answer_key', PACK / 'tools' / 'expectations_from_answer_key.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

RULEBOOK = {'rules': [
    {'rule_id': 'RULE-3', 'check_name': 'annotation_guideline', 'description': '6. The box includes the feet and lower legs.'},
    {'rule_id': 'RULE-7', 'check_name': 'annotation_guideline', 'description': '10. A person polygon must follow the visible person.'},
    {'rule_id': 'RULE-6', 'check_name': None, 'description': '9. Boxes may extend at most 3 pixels.'}]}
KEY = json.loads((PACK / 'answer_key.json').read_text(encoding='utf-8'))


def test_a_box_case_maps_its_primary_guideline_to_the_rule_and_its_target_to_the_packaged_key():
    expectations, stem, annotation_format = tool.build(KEY, RULEBOOK, 'C01')
    assert (stem, annotation_format) == ('000000199055', 'coco')
    assert expectations == {'baseline': {'RULE-3': {'/annotations/0': 'pass'}}, 'altered': {'RULE-3': {'/annotations/0': 'fail'}}}


def test_the_labelme_case_used_for_the_real_model_comparison_targets_the_shifted_polygon_only():
    expectations, stem, annotation_format = tool.build(KEY, RULEBOOK, 'L01')
    assert (stem, annotation_format) == ('258421', 'labelme')
    assert expectations == {'baseline': {'RULE-7': {'/shapes/0': 'pass'}}, 'altered': {'RULE-7': {'/shapes/0': 'fail'}}}


def test_a_case_whose_guideline_is_not_a_supported_visual_rule_is_refused_not_guessed():
    with pytest.raises(ValueError, match='no confirmed visual rule for guideline 9'):
        tool.build(KEY, RULEBOOK, 'C03')                 # guideline 9 is the unsupported tolerance (check_name None)
    with pytest.raises(ValueError, match='no implemented'):
        tool.build(KEY, RULEBOOK, 'C99')


def test_the_tool_never_overwrites_and_reports_the_case(tmp_path, capsys):
    rulebook = tmp_path / 'rulebook.json'
    rulebook.write_text(json.dumps(RULEBOOK))
    out = tmp_path / 'expected.json'
    assert tool.main(['--case', 'L01', '--rulebook', str(rulebook), '--out', str(out)]) == 0
    assert 'sample-stem=258421' in capsys.readouterr().out
    before = out.read_bytes()
    assert tool.main(['--case', 'L01', '--rulebook', str(rulebook), '--out', str(out)]) == 2
    assert out.read_bytes() == before
    assert tool.main(['--case', 'C03', '--rulebook', str(rulebook), '--out', str(tmp_path / 'other.json')]) == 1
