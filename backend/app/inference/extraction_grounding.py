"""Conservative evidence checks for extracted numeric parameters and label lists.

These helpers interpret source text for validation only. Original citations are
never rewritten. Ambiguous statements remain unsupported for reviewer mapping.
"""
import re
from decimal import Decimal

_SMALL = dict(zip(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split(),
    range(20),
))
_TENS = dict(zip("twenty thirty forty fifty sixty seventy eighty ninety".split(), range(20, 100, 10)))
_WORD = "(?:" + "|".join([*_SMALL, *_TENS, "hundred", "thousand"]) + ")"
_WORD_NUMBER = re.compile(r"\b" + _WORD + r"(?:[\s-]+(?:and[\s-]+)?" + _WORD + r")*\b", re.I)
_LIST_MARKER = re.compile(r"^\s*(?:(?:\d+[.)]|\(\d+\)|\[\d+\])\s+|[-*•]\s+)")
_UPPER = r"(?:at most|no more than|not more than|no greater than|up to|must not exceed|cannot exceed|maximum(?: of)?|<=|≤)"
_LOWER = r"(?:at least|no less than|not less than|minimum(?: of)?|>=|≥)"
_VALUE = r"(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>%|percent\b|per cent\b|pixels?\b|px\b)?"
_BOUND = re.compile(r"(?P<bound>" + _UPPER + "|" + _LOWER + r")\s*" + _VALUE, re.I)
_MEASUREMENT = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:px\b|pixels?\b|%|percent\b|per cent\b|degrees?\b|°|"
    r"mm\b|cm\b|millimet(?:er|re)s?\b|centimet(?:er|re)s?\b|met(?:er|re)s?\b|"
    r"milliseconds?\b|seconds?\b|ms\b|fps\b|frames?\b)", re.I,
)


def _under_hundred(words):
    if len(words) == 1:
        return _SMALL.get(words[0], _TENS.get(words[0]))
    if len(words) == 2 and words[0] in _TENS and words[1] in _SMALL and 0 < _SMALL[words[1]] < 10:
        return _TENS[words[0]] + _SMALL[words[1]]
    return None


def _number_words(words):
    words = [w for w in words if w != "and"]
    if not words:
        return None
    if "thousand" in words:
        if words.count("thousand") != 1:
            return None
        i = words.index("thousand")
        high = _number_words(words[:i]) if i else 1
        low = _number_words(words[i + 1:]) if words[i + 1:] else 0
        return high * 1000 + low if high is not None and low is not None and 0 < high < 1000 and low < 1000 else None
    if "hundred" in words:
        i = words.index("hundred")
        if words.count("hundred") != 1 or i not in (0, 1):
            return None
        high = _SMALL.get(words[0]) if i else 1
        low = _under_hundred(words[i + 1:]) if words[i + 1:] else 0
        return high * 100 + low if high is not None and 0 < high < 10 and low is not None else None
    return _under_hundred(words)


def normalized_numbers(text):
    """Remove list numbering and normalize unambiguous English integer words."""
    text = "\n".join(_LIST_MARKER.sub("", line) for line in text.splitlines())
    def replace(match):
        value = _number_words(re.split(r"[\s-]+", match.group().lower()))
        return match.group() if value is None else str(value)
    return _WORD_NUMBER.sub(replace, text)


def _clauses(text):
    # Decimal points and class-name commas are not sentence boundaries.
    return [s.strip() for s in re.split(
        r";|\n|\.(?=\s|$)|,\s*(?:and\s+)?(?=(?:every|each|all)\s+\w+\s+(?:must|shall|should)\b)"
        r"|(?:,\s*)?\band\s+(?=(?:at most|at least|no more than|no less than)\b)", text, flags=re.I,
    ) if s.strip()]


def measured_value(text):
    normalized = normalized_numbers(text)
    match = _MEASUREMENT.search(normalized)
    if match:
        return match.group()
    # Unknown numeric wording must not make an exact measurement look visual.
    match = re.search(
        r"\b(?:at most|at least|tolerance(?: of)?)\s+(?:a |an )?(?:half|quarter|one-half|one-quarter)\s+"
        r"(?:pixels?|px|percent|degrees?)\b", normalized, re.I,
    )
    return match.group() if match else None


def explicit_vocabulary(requirement, labels):
    """Require an explicit complete list, rather than example names or prefixes."""
    noun = r"(?:annotation\s+)?(?:labels?|class(?:es)?|categor(?:y|ies)|label set|label vocabulary)"
    lead = r"(?:the\s+)?(?:only\s+)?"  # "The only allowed annotation category is person."
    patterns = (
        rf"{lead}(?:allowed|permitted|valid|accepted|supported)\s+{noun}\s*(?:are|is|:|=)\s*(.+)",
        rf"{lead}{noun}\s*(?:are|is|:|=)\s*(.+)",
        rf"{noun}\s+(?:must|shall)\s+be\s+(?:(?:one of|either|only)\s+)?(.+)",
        rf"only\s+(.+?)\s+(?:are|is)\s+(?:allowed|permitted|valid|accepted)\s+{noun}",
        rf"use\s+only\s+(.+?)\s+as\s+{noun}",
    )
    for clause in _clauses(requirement):
        clause = _LIST_MARKER.sub("", clause)
        if re.search(r"\b(?:for example|such as|including|e\.g)\b", clause, re.I):
            continue
        for pattern in patterns:
            match = re.fullmatch(pattern, clause.rstrip("."), re.I)
            if not match:
                continue
            raw = match.group(1).strip().strip("[]{}").strip()
            # Protect quoted multiword names, including names containing "and".
            tokens = re.split(r'("[^"]*"|\'[^\']*\')', raw)
            declared = []
            for token in tokens:
                token = token.strip()
                if not token:
                    continue
                if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
                    declared.append(token[1:-1])
                else:
                    declared.extend(p.strip() for p in re.split(r",|\b(?:and|or)\b", token) if p.strip())
            if declared and len(declared) == len(set(declared)) and set(labels) == set(declared):
                return True
    return False


def grounded_numeric_parameters(check_name, parameters, requirement, context):
    """Bind a parameter to a stated quantity, comparator and unit.

    A matching number elsewhere in the guideline or in a list marker is not
    evidence. Unrecognized wording stays unsupported instead of being guessed.
    """
    numeric = {k: v for k, v in parameters.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
    if not numeric:
        return True
    clauses = _clauses(normalized_numbers("\n".join([requirement, *context])))
    for parameter, value in numeric.items():
        wanted_bound = "lower" if parameter.startswith("min") else "upper"
        found = set()
        for clause in clauses:
            lower = clause.lower()
            if check_name == "mask_components":
                relevant = "mask" in lower and re.search(r"\b(?:connected\s+)?components?\b", lower)
                units = {"count"}
            elif check_name == "box_overlaps":
                relevant = re.search(r"\biou\b|\bintersection[- ]over[- ]union\b", lower)
                units = {"fraction"}
            elif check_name == "box_mask_alignment":
                relevant = "mask" in lower and "box" in lower and "edge" in lower and re.search(r"differ|align|distance", lower)
                units = {"pixels"}
            elif check_name == "mask_outside_box":
                relevant = "mask" in lower and "box" in lower and "outside" in lower
                units = {"fraction"}
            elif check_name == "mask_box_fill":
                relevant = "mask" in lower and "box" in lower and re.search(r"fill|occup|cover", lower)
                units = {"fraction"}
            else:
                return False
            if not relevant:
                continue
            for match in _BOUND.finditer(clause):
                bound = "lower" if re.fullmatch(_LOWER, match.group("bound"), re.I) else "upper"
                if bound != wanted_bound:
                    continue
                unit = (match.group("unit") or "").lower()
                number = Decimal(match.group("value"))
                if unit in {"%", "percent", "per cent"}:
                    kind, number = "fraction", number / 100
                elif unit in {"px", "pixel", "pixels"}:
                    kind = "pixels"
                else:
                    kind = "count" if check_name == "mask_components" else "fraction"
                if kind in units:
                    found.add(number)
        # Multiple conflicting values for the same measurement require review.
        if found != {Decimal(str(value))}:
            return False
    return True
