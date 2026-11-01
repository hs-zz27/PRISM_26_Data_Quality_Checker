"""Lossless wire compaction and bounded, non-truncating v2 requests."""
from copy import deepcopy
import json


def compact_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


def compact_rule(rule):
    """Keep every criterion/parameter, any citation not already in its description, and
    the rule's applicable guideline context (scope, definitions, exceptions, uncertainty)."""
    value = {key: getattr(rule, key) for key in ('rule_id', 'description', 'check_name', 'parameters')}
    if rule.source_sentence is not None and rule.source_sentence not in rule.description:
        value['source_sentence'] = rule.source_sentence
    if rule.context_sentences:
        value['context_sentences'] = list(rule.context_sentences)
    return value


def compact_schema(schema):
    """Remove JSON Schema display titles only; retain every validation constraint."""
    result = deepcopy(schema)
    def visit(node):
        if isinstance(node, dict):
            node.pop('title', None)
            for key, value in node.items():
                if key in ('properties', '$defs', 'definitions'):
                    for child in value.values():
                        visit(child)
                elif key in ('items', 'additionalProperties', 'anyOf', 'allOf', 'oneOf', 'not'):
                    visit(value)
        elif isinstance(node, list):
            for child in node:
                visit(child)
    visit(result)
    return result


def text_fits(system, content, schema, execution, image_count):
    """Coarse byte preflight; Ollama v0.33.3+ enforces the actual token limit.

    V2 requires truncate=false and shift=false on the wire. Oversized real inputs
    must be rejected by the server, never silently shortened. Completion checks
    independently require a verified prompt count with room for the pinned output.
    Keep a finite text bound plus image, output and chat-template reservations.
    This is not a claim that a byte estimate is an exact vision tokenizer.
    """
    allowance = max(0, execution.num_ctx - execution.num_predict - 1024 * image_count - 512)
    return len((system + content + compact_json(schema)).encode()) <= allowance


OBJECT_COLUMNS = ['object_key', 'object_id', 'label', 'supplied_bbox_xyxy', 'polygons', 'group_id']


def correction_rows(objects):
    """Retain every vertex and identity, with shared column/coordinate definitions."""
    return [[obj.get('source_key', f"penn_fudan:{obj['object_id']}"), obj['object_id'], obj['label'],
             obj.get('bbox') if obj.get('bbox_origin') != 'derived_from_polygon' else None,
             obj.get('polygons', []), obj.get('group_id')] for obj in objects]
