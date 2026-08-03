from __future__ import annotations

import re
from typing import Any, Mapping


MONEY_RE = re.compile(
    r"(?<!\w)(?P<currency>E\$|[$£€])\s?(?P<open>\()?"
    r"(?P<value>\d[\d,]*(?:\.\d+)?)\)?\s?"
    r"(?P<unit>trillion|billion|million|thousand|[TBMK])?(?!\w)",
    re.I,
)
PERCENT_RE = re.compile(r"(?<!\w)(?P<value>-?\d+(?:\.\d+)?)\s*(?:%|percent)(?!\w)", re.I)
PERCENT_RANGE_RE = re.compile(
    r"(?<![\w.])(?P<lower>-?\d+(?:\.\d+)?)\s*%?\s*(?:-|â€“|â€”|to)\s*"
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
    r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?:\:[0-5]\d)?"
    r"(?:\s?[ap]\.?(?:m\.?)?)?(?:\s?(?:ET|EST|EDT|UTC))?\b",
    re.I,
)
NUMBER_RE = re.compile(r"(?<![\w.\-$£€])(?P<value>-?\d[\d,]*(?:\.\d+)?)(?P<unit>[KMBT])?(?![\w.-])", re.I)

_CURRENCY = {"$": "USD", "E$": "EUR", "£": "GBP", "€": "EUR"}
_MAGNITUDE = {
    "t": "trillion", "trillion": "trillion",
    "b": "billion", "billion": "billion",
    "m": "million", "million": "million",
    "k": "thousand", "thousand": "thousand",
}


def extract_typed_facts(spans: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for span in spans:
        quote = str(span["quote"])
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
            facts.append({
                "fact_type": "percentage_range",
                "raw": match.group(0),
                "lower_value": match.group("lower"),
                "upper_value": match.group("upper"),
            })
        for match in PERCENT_RE.finditer(quote):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            occupied.append(match.span())
            facts.append({"fact_type": "percentage", "raw": match.group(0), "value": match.group("value")})
        for match in DATE_RE.finditer(quote):
            occupied.append(match.span())
            facts.append({"fact_type": "date", "raw": match.group(0)})
        for match in TIME_RE.finditer(quote):
            occupied.append(match.span())
            facts.append({"fact_type": "time", "raw": match.group(0)})
        for match in NUMBER_RE.finditer(quote):
            if any(match.start() < end and match.end() > start for start, end in occupied):
                continue
            facts.append(
                {
                    "fact_type": "number",
                    "raw": match.group(0),
                    "value": match.group("value").replace(",", ""),
                    "magnitude": _MAGNITUDE.get((match.group("unit") or "").lower(), "units"),
                }
            )
    return facts
