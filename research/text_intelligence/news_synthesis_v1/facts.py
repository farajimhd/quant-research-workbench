from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


MONEY_RE = re.compile(
    r"(?<!\w)(?P<currency>E\$|[$£€])\s?(?P<open>\()?"
    r"(?P<value>\d[\d,]*(?:\.\d+)?)\)?\s?"
    r"(?P<unit>trillion|billion|million|thousand|[TBMK])?(?!\w)",
    re.I,
)
PERCENT_RE = re.compile(
    r"(?<!\w)(?P<value>-?\d+(?:\.\d+)?)\s*(?:%|percent)(?!\w)", re.I
)
PERCENT_RANGE_RE = re.compile(
    r"(?<![\w.])(?P<lower>-?\d+(?:\.\d+)?)\s*%?\s*(?:-|–|—|to)\s*"
    r"(?P<upper>-?\d+(?:\.\d+)?)\s*(?:%|percent)(?!\w)",
    re.I,
)
DATE_RE = re.compile(
    r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2}(?:,\s+\d{4})?\b",
    re.I,
)
TIME_RE = re.compile(
    r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?"
    r"(?:\s?[ap]\.?(?:m\.?)?)?(?:\s?(?:ET|EST|EDT|UTC))?\b",
    re.I,
)
NUMBER_RE = re.compile(
    r"(?<![\w.\-$£€])(?P<value>-?\d[\d,]*(?:\.\d+)?)(?P<unit>[KMBT])?(?![\w.-])",
    re.I,
)

_GUIDANCE_METRIC = (
    r"(?:adjusted\s+|diluted\s+)?EPS|earnings per share|"
    r"(?:core\s+|organic\s+)?(?:revenue|sales) growth|"
    r"(?:adjusted\s+)?EBITDA|(?:revenue|sales)|(?:gross|operating|EBITDA) margin"
)
_COMPARISON_VALUE_ATOM = (
    r"(?:[$Â£â‚¬]\s*)?\d[\d,]*(?:\.\d+)?\s*"
    r"(?:%|percent|trillion|billion|million|thousand|[TBMK])?"
)
_COMPARISON_VALUE = (
    rf"{_COMPARISON_VALUE_ATOM}(?:\s*(?:-|â€“|â€”|to)\s*{_COMPARISON_VALUE_ATOM})?"
)
_ESTIMATE_LABEL = r"(?:analysts?'?\s+)?(?:consensus\s+)?(?:est\.?|estimates?|consensus)"
GUIDANCE_COMPARISON_RE = re.compile(
    rf"\b(?P<metric>{_GUIDANCE_METRIC})\b\s*"
    rf"(?:guidance\s*)?(?:of|at|around|approximately|between|~)?\s*"
    rf"(?P<subject>{_COMPARISON_VALUE})\s*"
    rf"(?:vs\.?|versus|compared (?:with|to))\s*"
    rf"(?:(?P<prefix>{_ESTIMATE_LABEL})\s*(?:of|at)?\s*)?"
    rf"(?P<comparator>{_COMPARISON_VALUE})"
    rf"(?:\s*(?P<suffix>{_ESTIMATE_LABEL}))?",
    re.I,
)
_COMPARISON_NUMBER_RE = re.compile(
    r"(?P<value>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent|trillion|billion|million|thousand|[TBMK])?",
    re.I,
)
_COMPARISON_SCALE = {
    "t": Decimal("1e12"), "trillion": Decimal("1e12"),
    "b": Decimal("1e9"), "billion": Decimal("1e9"),
    "m": Decimal("1e6"), "million": Decimal("1e6"),
    "k": Decimal("1e3"), "thousand": Decimal("1e3"),
    "%": Decimal("1"), "percent": Decimal("1"), "": Decimal("1"),
}

_CURRENCY = {"$": "USD", "E$": "EUR", "£": "GBP", "€": "EUR"}
_MAGNITUDE = {
    "t": "trillion",
    "trillion": "trillion",
    "b": "billion",
    "billion": "billion",
    "m": "million",
    "million": "million",
    "k": "thousand",
    "thousand": "thousand",
}


def extract_typed_facts(spans: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Extract typed numeric and temporal facts without treating identifiers as numbers."""
    facts: list[dict[str, Any]] = []
    for span in spans:
        quote = str(span["quote"])
        facts.extend(_extract_guidance_comparisons(quote))
        occupied: list[tuple[int, int]] = []
        for match in MONEY_RE.finditer(quote):
            occupied.append(match.span())
            value = match.group("value").replace(",", "")
            if match.group("open"):
                value = f"-{value}"
            magnitude = _MAGNITUDE.get((match.group("unit") or "").lower(), "units")
            facts.append(
                {
                    "fact_type": "money",
                    "raw": match.group(0),
                    "value": value,
                    "currency": _CURRENCY[match.group("currency")],
                    "magnitude": magnitude,
                }
            )
        for match in PERCENT_RANGE_RE.finditer(quote):
            occupied.append(match.span())
            facts.append(
                {
                    "fact_type": "percentage_range",
                    "raw": match.group(0),
                    "lower_value": match.group("lower"),
                    "upper_value": match.group("upper"),
                }
            )
        for match in PERCENT_RE.finditer(quote):
            if _overlaps(match.span(), occupied):
                continue
            occupied.append(match.span())
            facts.append(
                {
                    "fact_type": "percentage",
                    "raw": match.group(0),
                    "value": match.group("value"),
                }
            )
        for fact_type, pattern in (("date", DATE_RE), ("time", TIME_RE)):
            for match in pattern.finditer(quote):
                occupied.append(match.span())
                facts.append({"fact_type": fact_type, "raw": match.group(0)})
        for match in NUMBER_RE.finditer(quote):
            if _overlaps(match.span(), occupied):
                continue
            facts.append(
                {
                    "fact_type": "number",
                    "raw": match.group(0),
                    "value": match.group("value").replace(",", ""),
                    "magnitude": _MAGNITUDE.get(
                        (match.group("unit") or "").lower(), "units"
                    ),
                }
            )
    return facts


def _extract_guidance_comparisons(text: str) -> list[dict[str, Any]]:
    comparisons: list[dict[str, Any]] = []
    for match in GUIDANCE_COMPARISON_RE.finditer(text):
        if not match.group("prefix") and not match.group("suffix"):
            continue
        subject = _comparison_bounds(match.group("subject"))
        comparator = _comparison_bounds(match.group("comparator"))
        if subject is None or comparator is None:
            continue
        subject_low, subject_high = subject
        comparator_low, comparator_high = comparator
        if subject_high < comparator_low:
            relation = "below"
        elif subject_low > comparator_high:
            relation = "above"
        else:
            relation = "in_line"
        comparisons.append({
            "fact_type": "estimate_comparison",
            "metric": _normalize_comparison_metric(match.group("metric")),
            "subject_role": "issuer_guidance",
            "comparator_role": "consensus_estimate",
            "subject_raw": match.group("subject").strip(),
            "comparator_raw": match.group("comparator").strip(),
            "subject_lower_value": _decimal_text(subject_low),
            "subject_upper_value": _decimal_text(subject_high),
            "comparator_lower_value": _decimal_text(comparator_low),
            "comparator_upper_value": _decimal_text(comparator_high),
            "relation": relation,
        })
    return comparisons


def _comparison_bounds(raw: str) -> tuple[Decimal, Decimal] | None:
    values: list[Decimal] = []
    try:
        for match in _COMPARISON_NUMBER_RE.finditer(raw):
            value = Decimal(match.group("value").replace(",", ""))
            scale = _COMPARISON_SCALE[(match.group("unit") or "").casefold()]
            values.append(value * scale)
    except (InvalidOperation, KeyError):
        return None
    if not values:
        return None
    return (min(values), max(values))


def _normalize_comparison_metric(raw: str) -> str:
    metric = " ".join(raw.casefold().split())
    if "eps" in metric or "earnings per share" in metric:
        return "eps"
    return metric.replace(" ", "_")


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and span[1] > start for start, end in occupied)
