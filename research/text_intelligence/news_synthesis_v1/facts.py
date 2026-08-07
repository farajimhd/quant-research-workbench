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

REGULATORY_AUTHORITY_RE = re.compile(
    r"\b(?:FDA|U\.S\. Food and Drug Administration|EMA|European Medicines Agency|"
    r"Health Canada|MHRA|PMDA)\b",
    re.I,
)

_REGULATORY_OUTCOME_PATTERNS = (
    (
        "clinical_hold_lifted",
        "favorable",
        "market_access_restored",
        re.compile(
            r"\b(?:lift(?:s|ed|ing)?|remov(?:e[sd]?|ing)|resolv(?:e[sd]?|ing))\b"
            r".{0,80}\bclinical hold\b|\bclinical hold\b.{0,80}"
            r"\b(?:lift(?:s|ed|ing)?|remov(?:e[sd]?|ing)|resolv(?:e[sd]?|ing))\b",
            re.I,
        ),
    ),
    (
        "not_substantially_equivalent",
        "adverse",
        "market_access_blocked_or_delayed",
        re.compile(
            r"\b(?:not substantially equivalent|NSE (?:letter|determination|decision|finding))\b",
            re.I,
        ),
    ),
    (
        "complete_response",
        "adverse",
        "market_access_blocked_or_delayed",
        re.compile(r"\b(?:complete response letters?|CRLs?)\b", re.I),
    ),
    (
        "refusal_to_file",
        "adverse",
        "market_access_blocked_or_delayed",
        re.compile(r"\b(?:refus(?:e[sd]?|al)[- ]to[- ]file|refuse[- ]to[- ]file)\b", re.I),
    ),
    (
        "clinical_hold",
        "adverse",
        "development_blocked_or_delayed",
        re.compile(r"\bclinical hold\b", re.I),
    ),
    (
        "approval_denied",
        "adverse",
        "market_access_blocked_or_delayed",
        re.compile(
            r"\b(?:did not approve|has not approved|declin(?:e[sd]?|ing) (?:the )?approval|"
            r"den(?:y|ies|ied|ial) (?:the )?approval|reject(?:s|ed|ing) (?:the )?"
            r"(?:application|submission))\b",
            re.I,
        ),
    ),
    (
        "deficiency_identified",
        "adverse",
        "market_access_blocked_or_delayed",
        re.compile(r"\b(?:deficien(?:cy|cies) letters?|major deficien(?:cy|cies))\b", re.I),
    ),
    (
        "clearance_granted",
        "favorable",
        "market_access_granted",
        re.compile(
            r"\bcleared\b|\b(?:grant(?:s|ed|ing)?|receiv(?:e[sd]?|ing)|secur(?:e[sd]?|ing)|"
            r"obtain(?:s|ed|ing)?)\b.{0,50}\bclearance\b",
            re.I,
        ),
    ),
    (
        "approval_granted",
        "favorable",
        "market_access_granted",
        re.compile(
            r"\bapprov(?:e[sd]|ing)\b|\b(?:grant(?:s|ed|ing)?|receiv(?:e[sd]?|ing)|"
            r"secur(?:e[sd]?|ing)|obtain(?:s|ed|ing)?)\b.{0,50}\bapproval\b",
            re.I,
        ),
    ),
    (
        "authorization_granted",
        "favorable",
        "market_access_granted",
        re.compile(r"\b(?:authorization|authoriz(?:e[sd]?|ing))\b", re.I),
    ),
    (
        "application_accepted",
        "favorable",
        "regulatory_review_started",
        re.compile(r"\baccept(?:s|ed|ance)?\b.{0,60}\b(?:application|submission)\b", re.I),
    ),
    (
        "regulatory_submission",
        "procedural",
        "regulatory_review_pending",
        re.compile(r"\b(?:submission|resubmission|submit(?:s|ted|ting))\b", re.I),
    ),
)

_GUIDANCE_METRIC = (
    r"(?:adjusted\s+|diluted\s+)?EPS|earnings per share|"
    r"rev\.?|"
    r"(?:core\s+|organic\s+)?(?:revenue|sales) growth|"
    r"(?:adjusted\s+)?EBITDA|(?:revenues?|sales|profit)|(?:gross|operating|EBITDA) margin"
)
_COMPARISON_VALUE_ATOM = (
    r"(?:(?:E?\$|£|€)\s*)?\(?\d[\d,]*(?:\.\d+)?\)?\s*"
    r"(?:%|percent|trillion|billion|million|thousand|[TBMK])?"
)
_COMPARISON_VALUE = (
    rf"{_COMPARISON_VALUE_ATOM}(?:\s*(?:-|â€“|â€”|to)\s*{_COMPARISON_VALUE_ATOM})?"
)
_ESTIMATE_LABEL = r"(?:analysts?'?\s+)?(?:consensus\s+)?(?:est\.?|estimates?|consensus)"
GUIDANCE_COMPARISON_RE = re.compile(
    rf"\b(?P<metric>{_GUIDANCE_METRIC})(?=\s)\s*"
    rf"(?:guidance\s*)?(?:of|at|around|approximately|between|~)?\s*"
    rf"(?P<subject>{_COMPARISON_VALUE})\s*"
    rf"(?:vs\.?|versus|compared (?:with|to))\s*"
    rf"(?:(?P<prefix>{_ESTIMATE_LABEL})\s*(?:of|at)?\s*)?"
    rf"(?P<comparator>{_COMPARISON_VALUE})"
    rf"(?:\s*(?P<suffix>{_ESTIMATE_LABEL}))?",
    re.I,
)
ANALYST_ESTIMATE_COMPARISON_RE = re.compile(
    rf"\b(?P<metric>{_GUIDANCE_METRIC})(?=\s)\s*(?:estimate|forecast)s?\b"
    rf".{{0,100}}?\b(?:to|at|of)\s*(?P<subject>{_COMPARISON_VALUE})"
    rf".{{0,100}}?\b(?P<relation>below|above|in[- ]line with|in line with)\s+"
    rf"(?:the\s+)?(?P<label>consensus|street)(?:\s+(?:estimate|forecast))?\s*"
    rf"(?:of|at)?\s*(?P<comparator>{_COMPARISON_VALUE})",
    re.I,
)
OUTLOOK_ESTIMATE_COMPARISON_RE = re.compile(
    rf"\b(?P<metric>{_GUIDANCE_METRIC})(?=\s)\s+(?:forecast|outlook|guidance)\b"
    rf".{{0,60}}?\b(?:is|at|of|between)\s*(?P<subject>{_COMPARISON_VALUE})"
    rf".{{0,100}}?\b(?:while|vs\.?|versus|compared (?:with|to))\s+"
    rf"(?:(?:analysts?'?\s+)?(?:estimates?|consensus|street)(?:\s+(?:estimate|view))?\s*)"
    rf"(?:stand(?:s|ing)?\s+)?(?:at|of)?\s*(?P<comparator>{_COMPARISON_VALUE})",
    re.I,
)
MANAGEMENT_RANGE_POSITION_RE = re.compile(
    rf"\b(?:at|near)\s+(?:the\s+)?(?P<position>low(?:er)?|bottom|high(?:er)?|top|mid(?:dle)?)\s+"
    rf"(?:end\s+)?of\s+(?:(?:management|mgt)\s*'?s?\s+)?"
    rf"(?P<range>{_COMPARISON_VALUE})\s+range\b",
    re.I,
)
_ESTIMATE_REVISION_PATTERNS = (
    re.compile(
        rf"\b(?P<action>rais(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|boost(?:s|ed|ing)?|"
        rf"lower(?:s|ed|ing)?|cut(?:s|ting)?|reduc(?:e|es|ed|ing))\b"
        rf".{{0,80}}?\b(?P<metric>{_GUIDANCE_METRIC})\b\s*(?:estimate|forecast)s?\b",
        re.I,
    ),
    re.compile(
        rf"\b(?P<metric>{_GUIDANCE_METRIC})\b\s*(?:estimate|forecast)s?\b"
        rf".{{0,60}}?\b(?:was|were|is|are|has been|have been)?\s*"
        rf"(?P<action>rais(?:e|es|ed|ing)|increas(?:e|es|ed|ing)|boost(?:s|ed|ing)?|"
        rf"lower(?:s|ed|ing)?|cut|reduc(?:e|es|ed|ing))\b",
        re.I,
    ),
)
_OPERATING_RISK_PATTERNS = (
    (
        "margin_pressure",
        re.compile(
            r"\b(?:gross|operating|EBITDA|profit)?\s*margins?\b.{0,50}\b"
            r"(?:pressure|pressured|compression|compressing|headwind|deterioration)\b|"
            r"\b(?:pressure|compression|headwind)\b.{0,50}\b(?:gross|operating|EBITDA|profit)?\s*margins?\b",
            re.I,
        ),
    ),
    (
        "input_cost_pressure",
        re.compile(
            r"\b(?:escalating|rising|higher|increasing|inflationary)\b.{0,50}\b"
            r"(?:input|raw material|commodity|labor|freight) costs?\b|"
            r"\b(?:input|raw material|commodity|labor|freight) costs?\b.{0,50}\b"
            r"(?:pressure|headwind|inflation|rising|higher|increasing|escalating)\b",
            re.I,
        ),
    ),
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


def extract_typed_facts(
    spans: list[Mapping[str, Any]],
    *,
    estimate_subject_role: str = "issuer_guidance",
) -> list[dict[str, Any]]:
    """Extract typed numeric and temporal facts without treating identifiers as numbers."""
    facts: list[dict[str, Any]] = []
    for span in spans:
        quote = str(span["quote"])
        facts.extend(extract_regulatory_decision_facts(quote))
        facts.extend(_extract_estimate_comparisons(quote, estimate_subject_role))
        facts.extend(_extract_estimate_revision_facts(quote))
        facts.extend(_extract_management_range_positions(quote, estimate_subject_role))
        facts.extend(_extract_operating_risks(quote))
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


def extract_regulatory_decision_facts(text: str) -> list[dict[str, Any]]:
    """Normalize medical-regulatory outcomes independently of surface word order."""
    authority_match = REGULATORY_AUTHORITY_RE.search(text)
    facts: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for outcome, outcome_class, commercial_effect, pattern in _REGULATORY_OUTCOME_PATTERNS:
        for match in pattern.finditer(text):
            if _overlaps(match.span(), occupied):
                continue
            if outcome == "clinical_hold" and re.search(
                r"\b(?:lift(?:s|ed|ing)?|remov(?:e[sd]?|ing)|resolv(?:e[sd]?|ing)|"
                r"clear(?:s|ed|ing)?\b.{0,80}\b(?:resume|begin enrolling)|"
                r"address(?:es|ed|ing)? all clinical hold (?:issues|concerns))\b",
                text,
                re.I,
            ):
                # The noun names the obstacle that was resolved; it is not a
                # second, still-active adverse disposition.
                continue
            if authority_match is None and outcome not in {
                "clinical_hold_lifted",
                "complete_response",
                "refusal_to_file",
                "clinical_hold",
            }:
                continue
            effective_outcome = outcome
            effective_class = outcome_class
            effective_effect = commercial_effect
            if outcome_class == "favorable" and re.search(
                r"\b(?:attempt(?:s|ed|ing)?|seek(?:s|ing)?|aim(?:s|ed|ing)?|"
                r"try(?:ing|ies|ied)?|plans?|intends?|expects?|will)\b.{0,50}$",
                text[max(0, match.start() - 70):match.start()],
                re.I,
            ):
                effective_outcome = "regulatory_authorization_sought"
                effective_class = "procedural"
                effective_effect = "market_access_pending"
            occupied.append(match.span())
            fact = {
                "fact_type": "regulatory_decision",
                "raw": match.group(0),
                "authority": authority_match.group(0) if authority_match else "unspecified_regulator",
                "outcome": effective_outcome,
                "outcome_class": effective_class,
                "commercial_effect": effective_effect,
                "start": match.start(),
                "end": match.end(),
            }
            if subject := _regulatory_subject(text, match.span()):
                fact["subject_raw"] = subject
            if (
                effective_outcome in {"clearance_granted", "approval_granted", "authorization_granted"}
                and (
                    re.search(r"\bsupplements?\b", str(fact.get("subject_raw") or ""), re.I)
                    or re.search(r"\bPAS submission\b", text, re.I)
                )
            ):
                fact["commercial_effect"] = "supplement_scope_granted"
            facts.append(fact)
    return facts


def _regulatory_subject(text: str, outcome_span: tuple[int, int]) -> str | None:
    """Retain the product/application scope nearest a regulatory disposition."""
    suffix = text[outcome_span[1]:]
    scoped = re.search(
        r"\b(?:for|on|of)\s+(?P<subject>[^,;.!?]{3,160})",
        suffix,
        re.I,
    )
    if scoped:
        subject = scoped.group("subject")
    else:
        direct = re.match(r"[\s:\-]*(?P<subject>[^,;.!?]{3,120})", suffix)
        subject = direct.group("subject") if direct else ""
    subject = re.split(
        r"\b(?:after|before|because|but|while|and (?:will|plans?|expects?|intends?))\b",
        subject,
        maxsplit=1,
        flags=re.I,
    )[0]
    subject = " ".join(subject.strip(" :-").split())
    return subject or None


def _extract_estimate_comparisons(
    text: str,
    subject_role: str,
) -> list[dict[str, Any]]:
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
            "subject_role": subject_role,
            "comparator_role": "consensus_estimate",
            "subject_raw": match.group("subject").strip(),
            "comparator_raw": match.group("comparator").strip(),
            "subject_lower_value": _decimal_text(subject_low),
            "subject_upper_value": _decimal_text(subject_high),
            "comparator_lower_value": _decimal_text(comparator_low),
            "comparator_upper_value": _decimal_text(comparator_high),
            "relation": relation,
            **({"horizon": horizon} if (horizon := _comparison_horizon(text, match.start())) else {}),
        })
    for match in ANALYST_ESTIMATE_COMPARISON_RE.finditer(text):
        subject = _comparison_bounds(match.group("subject"))
        comparator = _comparison_bounds(match.group("comparator"))
        if subject is None or comparator is None:
            continue
        subject_low, subject_high = subject
        comparator_low, comparator_high = comparator
        relation_word = match.group("relation").casefold()
        relation = (
            "below" if relation_word == "below"
            else "above" if relation_word == "above"
            else "in_line"
        )
        numeric_relation = (
            "below" if subject_high < comparator_low
            else "above" if subject_low > comparator_high
            else "in_line"
        )
        if relation != numeric_relation:
            continue
        comparisons.append({
            "fact_type": "estimate_comparison",
            "metric": _normalize_comparison_metric(match.group("metric")),
            "subject_role": subject_role,
            "comparator_role": "consensus_estimate",
            "subject_raw": match.group("subject").strip(),
            "comparator_raw": match.group("comparator").strip(),
            "subject_lower_value": _decimal_text(subject_low),
            "subject_upper_value": _decimal_text(subject_high),
            "comparator_lower_value": _decimal_text(comparator_low),
            "comparator_upper_value": _decimal_text(comparator_high),
            "relation": relation,
            **({"horizon": horizon} if (horizon := _comparison_horizon(text, match.start())) else {}),
        })
    for match in OUTLOOK_ESTIMATE_COMPARISON_RE.finditer(text):
        subject = _comparison_bounds(match.group("subject"))
        comparator = _comparison_bounds(match.group("comparator"))
        if subject is None or comparator is None:
            continue
        subject_low, subject_high = subject
        comparator_low, comparator_high = comparator
        relation = (
            "below" if subject_high < comparator_low
            else "above" if subject_low > comparator_high
            else "in_line"
        )
        comparisons.append({
            "fact_type": "estimate_comparison",
            "metric": _normalize_comparison_metric(match.group("metric")),
            "subject_role": subject_role,
            "comparator_role": "consensus_estimate",
            "subject_raw": match.group("subject").strip(),
            "comparator_raw": match.group("comparator").strip(),
            "subject_lower_value": _decimal_text(subject_low),
            "subject_upper_value": _decimal_text(subject_high),
            "comparator_lower_value": _decimal_text(comparator_low),
            "comparator_upper_value": _decimal_text(comparator_high),
            "relation": relation,
            **({"horizon": horizon} if (horizon := _comparison_horizon(text, match.start())) else {}),
        })
    return comparisons


def _extract_estimate_revision_facts(text: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for pattern in _ESTIMATE_REVISION_PATTERNS:
        for match in pattern.finditer(text):
            if _overlaps(match.span(), occupied):
                continue
            occupied.append(match.span())
            action = match.group("action").casefold()
            direction = "up" if re.match(r"(?:rais|increas|boost)", action) else "down"
            suffix = text[match.end():match.end() + 120]
            new_value = re.search(rf"\bto\s*(?P<value>{_COMPARISON_VALUE})", suffix, re.I)
            delta = re.search(
                r"\bby\s+(?P<delta>(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)"
                r"(?:\s+(?:penn(?:y|ies)|cents?|points?|percent|%))?)",
                suffix,
                re.I,
            )
            facts.append({
                "fact_type": "estimate_revision",
                "metric": _normalize_comparison_metric(match.group("metric")),
                "direction": direction,
                "raw": match.group(0),
                **({"new_value_raw": new_value.group("value").strip()} if new_value else {}),
                **({"delta_raw": delta.group("delta").strip()} if delta else {}),
            })
    return facts


def _extract_management_range_positions(text: str, subject_role: str) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for match in MANAGEMENT_RANGE_POSITION_RE.finditer(text):
        bounds = _comparison_bounds(match.group("range"))
        if bounds is None:
            continue
        raw_position = match.group("position").casefold()
        position = (
            "low_end" if raw_position in {"low", "lower", "bottom"}
            else "high_end" if raw_position in {"high", "higher", "top"}
            else "mid_range"
        )
        facts.append({
            "fact_type": "estimate_range_position",
            "subject_role": subject_role,
            "comparator_role": "management_guidance",
            "position": position,
            "range_raw": match.group("range").strip(),
            "range_lower_value": _decimal_text(bounds[0]),
            "range_upper_value": _decimal_text(bounds[1]),
        })
    return facts


def _extract_operating_risks(text: str) -> list[dict[str, Any]]:
    return [
        {
            "fact_type": "operating_risk",
            "risk_type": risk_type,
            "direction": "adverse",
            "raw": match.group(0),
        }
        for risk_type, pattern in _OPERATING_RISK_PATTERNS
        if (match := pattern.search(text))
    ]


def _comparison_horizon(text: str, metric_start: int) -> str | None:
    prefix = text[max(0, metric_start - 50):metric_start]
    matches = list(re.finditer(
        r"\b(?:Q[1-4](?:\s+20\d{2})?|FY\s*\d{2,4}|full[- ]year(?:\s+20\d{2})?|fiscal[- ]year(?:\s+20\d{2})?)\b",
        prefix,
        re.I,
    ))
    return " ".join(matches[-1].group(0).upper().split()) if matches else None


def _comparison_bounds(raw: str) -> tuple[Decimal, Decimal] | None:
    values: list[Decimal] = []
    try:
        for match in _COMPARISON_NUMBER_RE.finditer(raw):
            value = Decimal(match.group("value").replace(",", ""))
            if "(" in raw[max(0, match.start() - 2):match.end() + 1] and ")" in raw[match.start():match.end() + 2]:
                value = -value
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
    if re.fullmatch(r"rev\.?", metric):
        return "revenue"
    return metric.replace(" ", "_")


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and span[1] > start for start, end in occupied)
