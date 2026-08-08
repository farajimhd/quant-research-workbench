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
        "advisory_endorsement_withheld",
        "adverse",
        "market_access_blocked_or_delayed",
        re.compile(
            r"\b(?:FDA\s+)?(?:advisers?|advisors?|advisory (?:panel|committee)|panelists?)\b"
            r".{0,180}\b(?:data|evidence|results?)\b.{0,100}"
            r"\b(?:lack(?:s|ed|ing)?|insufficient|inadequate|unreliable|not reliable)\b"
            r".{0,120}\b(?:endorse|recommend|support)\b.{0,60}\bapproval\b|"
            r"\b(?:FDA\s+)?(?:advisers?|advisors?|advisory (?:panel|committee)|panelists?)\b"
            r".{0,180}\b(?:declin(?:e[sd]?|ing)|fail(?:s|ed)?|refus(?:e[sd]?|ing)|"
            r"did not|does not|cannot|can't)\b.{0,80}\b(?:endorse|recommend|support)\b"
            r".{0,60}\bapproval\b",
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
    r"(?:adjusted\s+|diluted\s+|non-GAAP\s+)?EPS|"
    r"(?:adjusted\s+|diluted\s+|non-GAAP\s+)?earnings(?: per share)?|"
    r"loss(?: per share)?|"
    r"rev\.?|"
    r"(?:core\s+|organic\s+)?(?:revenue|sales) growth|"
    r"(?:adjusted\s+)?EBITDA|(?:revenues?|sales|profit)|(?:gross|operating|EBITDA) margin"
)
_COMPARISON_VALUE_ATOM = (
    r"(?:(?:E?\$|£|€)\s*)?\(?[-+]?\d[\d,]*(?:\.\d+)?\)?\s*"
    r"(?:%|percent\b|trillion\b|billion\b|million\b|thousand\b|[TBMK]\b|(?:-\s*)?cents?\b)?"
)
_COMPARISON_VALUE = (
    rf"{_COMPARISON_VALUE_ATOM}(?:\s*(?:-|â€“|â€”|to)\s*{_COMPARISON_VALUE_ATOM})?"
)
_ESTIMATE_LABEL = r"(?:analysts?'?\s+)?(?:consensus\s+)?(?:est\.?|estimates?|consensus)"
_CONSENSUS_LABEL = (
    r"(?:wall\s+street(?:'s)?|the\s+street(?:'s)?|street(?:'s)?|"
    r"analysts?'?(?:\s+(?:consensus|estimates?|forecast|view))?|"
    r"consensus|estimates?|expectations?)"
)
_GUIDANCE_VALUE_INTRO = (
    r"(?:(?:was|were|is|are|reported|posted)\s+)?"
    r"(?:guidance\s*)?(?:of|at|around|approximately|between|for|~|"
    r"in\s+(?:the\s+)?range\s+of)?\s*(?:just\s+)?"
)
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
GUIDANCE_RELATIONAL_COMPARISON_RE = re.compile(
    rf"\b(?P<metric>{_GUIDANCE_METRIC})(?=\s)\s*{_GUIDANCE_VALUE_INTRO}"
    rf"(?P<subject>{_COMPARISON_VALUE})(?:\s+per\s+share)?\s*[,;]?\s*"
    rf"(?:(?:also|once\s+again)\s+)?"
    rf"(?P<relation>vs\.?|versus|compared\s+(?:with|to)|"
    rf"(?:well\s+|mostly\s+|slightly\s+)?(?:below|above|under|short\s+of)|"
    rf"fell\s+short\s+of|beat(?:s|ing)?)\s*"
    rf"(?:(?P<label>{_CONSENSUS_LABEL})\s*[,;]?\s*)?"
    rf"(?:which\s+(?:stands?|stood)\s+at\s+|(?:estimate|forecast|view)\s+(?:of|at)\s+|"
    rf"(?:estimates?|consensus)\s+(?:of|at|for)\s+|of\s+|at\s+|for\s+)?"
    rf"(?P<comparator>{_COMPARISON_VALUE})",
    re.I,
)
GUIDANCE_PARENTHETICAL_COMPARISON_RE = re.compile(
    rf"\b(?P<metric>{_GUIDANCE_METRIC})(?=\s)\s*{_GUIDANCE_VALUE_INTRO}"
    rf"(?P<subject>{_COMPARISON_VALUE})(?:\s+per\s+share)?\s*[,;]?\s*"
    rf"\(\s*(?P<label>{_CONSENSUS_LABEL})\s*"
    rf"(?:estimate|forecast|view)?\s*(?:of|at)?\s*"
    rf"(?P<comparator>{_COMPARISON_VALUE})\s*\)",
    re.I,
)
GUIDANCE_PRIOR_COMPARISON_RE = re.compile(
    rf"\b(?P<metric>{_GUIDANCE_METRIC})(?=\s)\s*{_GUIDANCE_VALUE_INTRO}"
    rf"(?P<subject>{_COMPARISON_VALUE})(?:\s+per\s+share)?\s*[,;]?\s*"
    rf"(?:vs\.?|versus|compared\s+(?:with|to))\s*"
    rf"(?:the\s+)?(?:prior|previous|earlier)\s+"
    rf"(?:guidance|forecasts?|outlooks?|ranges?)(?:\s+of|\s+at)?\s*"
    rf"(?P<comparator>{_COMPARISON_VALUE})",
    re.I,
)
GUIDANCE_PREVIOUSLY_SEEN_COMPARISON_RE = re.compile(
    rf"\b(?P<metric>{_GUIDANCE_METRIC})(?=\s)\s*{_GUIDANCE_VALUE_INTRO}"
    rf"(?P<subject>{_COMPARISON_VALUE})(?:\s+per\s+share)?\s*[,;]\s*"
    rf"(?:had\s+)?(?:seen|expected|forecast|guided)\s*"
    rf"(?P<comparator>{_COMPARISON_VALUE})",
    re.I,
)
GUIDANCE_METRIC_VALUE_RE = re.compile(
    rf"\b(?P<metric>{_GUIDANCE_METRIC})(?=\s)\s*{_GUIDANCE_VALUE_INTRO}"
    rf"(?P<value>{_COMPARISON_VALUE})(?:\s+per\s+share)?",
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
    r"(?P<value>[-+]?\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent\b|trillion\b|billion\b|million\b|thousand\b|[TBMK]\b|cents?\b)?",
    re.I,
)
_COMPARISON_SCALE = {
    "t": Decimal("1e12"), "trillion": Decimal("1e12"),
    "b": Decimal("1e9"), "billion": Decimal("1e9"),
    "m": Decimal("1e6"), "million": Decimal("1e6"),
    "k": Decimal("1e3"), "thousand": Decimal("1e3"),
    "cent": Decimal("0.01"), "cents": Decimal("0.01"),
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
                r"try(?:ing|ies|ied)?|plans?|intends?|expects?|will|awaits?|awaiting|pending)\b.{0,50}$",
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
    structured_patterns = (
        (GUIDANCE_RELATIONAL_COMPARISON_RE, "consensus_estimate", True),
        (GUIDANCE_PARENTHETICAL_COMPARISON_RE, "consensus_estimate", False),
        (GUIDANCE_PRIOR_COMPARISON_RE, "management_guidance", False),
        (GUIDANCE_PREVIOUSLY_SEEN_COMPARISON_RE, "management_guidance", False),
    )
    for pattern, comparator_role, validate_authored_relation in structured_patterns:
        for match in pattern.finditer(text):
            if (
                pattern is GUIDANCE_RELATIONAL_COMPARISON_RE
                and re.fullmatch(
                    r"vs\.?|versus|compared\s+(?:with|to)",
                    match.group("relation").strip(),
                    re.I,
                )
                and not match.group("label")
            ):
                # A bare versus comparison is commonly a prior period (and
                # may begin with a token such as 3Q19), not an analyst
                # benchmark. Consensus facts require an explicit source.
                continue
            metric = _normalize_comparison_metric(match.group("metric"))
            subject = _comparison_bounds_for_metric(match.group("subject"), metric)
            comparator = _comparison_bounds_for_metric(match.group("comparator"), metric)
            if subject is None or comparator is None:
                continue
            subject_low, subject_high = subject
            comparator_low, comparator_high = comparator
            if comparator_role == "management_guidance":
                relation = (
                    "below"
                    if subject_low <= comparator_low and subject_high < comparator_high
                    else "above"
                    if subject_low > comparator_low and subject_high >= comparator_high
                    else "in_line"
                )
            else:
                relation = (
                    "below" if subject_high < comparator_low
                    else "above" if subject_low > comparator_high
                    else "in_line"
                )
            if validate_authored_relation:
                authored = _authored_comparison_relation(match.group("relation"))
                if authored is not None and authored != relation:
                    continue
            fact = {
                "fact_type": "estimate_comparison",
                "metric": metric,
                "subject_role": subject_role,
                "comparator_role": comparator_role,
                "subject_raw": match.group("subject").strip(),
                "comparator_raw": match.group("comparator").strip(),
                "subject_lower_value": _decimal_text(subject_low),
                "subject_upper_value": _decimal_text(subject_high),
                "comparator_lower_value": _decimal_text(comparator_low),
                "comparator_upper_value": _decimal_text(comparator_high),
                "relation": relation,
                **({"horizon": horizon} if (horizon := _comparison_horizon(text, match.start())) else {}),
            }
            key = (
                fact["metric"],
                fact.get("horizon"),
                fact["comparator_role"],
                fact["subject_lower_value"],
                fact["subject_upper_value"],
                fact["comparator_lower_value"],
                fact["comparator_upper_value"],
            )
            existing_keys = {
                (
                    row.get("metric"), row.get("horizon"), row.get("comparator_role"),
                    row.get("subject_lower_value"), row.get("subject_upper_value"),
                    row.get("comparator_lower_value"), row.get("comparator_upper_value"),
                )
                for row in comparisons
            }
            if key not in existing_keys:
                comparisons.append(fact)
    for fact in _extract_adjacent_guidance_comparisons(text, subject_role):
        key = (
            fact["metric"], fact.get("horizon"), fact["comparator_role"],
            fact["subject_lower_value"], fact["subject_upper_value"],
            fact["comparator_lower_value"], fact["comparator_upper_value"],
        )
        if key not in {
            (
                row.get("metric"), row.get("horizon"), row.get("comparator_role"),
                row.get("subject_lower_value"), row.get("subject_upper_value"),
                row.get("comparator_lower_value"), row.get("comparator_upper_value"),
            )
            for row in comparisons
        }:
            comparisons.append(fact)
    return comparisons


def _extract_adjacent_guidance_comparisons(
    text: str,
    subject_role: str,
) -> list[dict[str, Any]]:
    boundary = re.search(
        r"(?<=[.!?])\s+(?=(?:analysts?|wall street|the street|consensus)\b)",
        text,
        re.I,
    )
    if boundary is None:
        return []
    issuer_clause = text[:boundary.start()]
    comparator_clause = text[boundary.end():]
    if not re.search(
        r"\b(?:sees|expects?|anticipates?|projects?|forecasts?|guidance|outlook)\b",
        issuer_clause,
        re.I,
    ) or not re.search(
        r"\b(?:analysts?|wall street|the street|consensus)\b.{0,80}"
        r"\b(?:project(?:s|ed)?|expect(?:s|ed)?|estimate(?:s|d)?|forecast(?:s|ed)?)\b",
        comparator_clause,
        re.I,
    ):
        return []
    issuer_values: dict[str, tuple[re.Match[str], tuple[Decimal, Decimal]]] = {}
    comparator_values: dict[str, tuple[re.Match[str], tuple[Decimal, Decimal]]] = {}
    for match in GUIDANCE_METRIC_VALUE_RE.finditer(issuer_clause):
        metric = _normalize_comparison_metric(match.group("metric"))
        if bounds := _comparison_bounds_for_metric(match.group("value"), metric):
            issuer_values.setdefault(metric, (match, bounds))
    for match in GUIDANCE_METRIC_VALUE_RE.finditer(comparator_clause):
        metric = _normalize_comparison_metric(match.group("metric"))
        if bounds := _comparison_bounds_for_metric(match.group("value"), metric):
            comparator_values.setdefault(metric, (match, bounds))
    facts: list[dict[str, Any]] = []
    for metric in sorted(issuer_values.keys() & comparator_values.keys()):
        issuer_match, (subject_low, subject_high) = issuer_values[metric]
        comparator_match, (comparator_low, comparator_high) = comparator_values[metric]
        relation = (
            "below" if subject_high < comparator_low
            else "above" if subject_low > comparator_high
            else "in_line"
        )
        facts.append({
            "fact_type": "estimate_comparison",
            "metric": metric,
            "subject_role": subject_role,
            "comparator_role": "consensus_estimate",
            "subject_raw": issuer_match.group("value").strip(),
            "comparator_raw": comparator_match.group("value").strip(),
            "subject_lower_value": _decimal_text(subject_low),
            "subject_upper_value": _decimal_text(subject_high),
            "comparator_lower_value": _decimal_text(comparator_low),
            "comparator_upper_value": _decimal_text(comparator_high),
            "relation": relation,
            **({"horizon": horizon} if (
                horizon := _comparison_horizon(issuer_clause, issuer_match.start())
            ) else {}),
        })
    return facts


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
    parsed: list[tuple[Decimal, str]] = []
    normalized_raw = re.sub(r"(?<=\d)-(?=cents?\b)", " ", raw, flags=re.I)
    try:
        for match in _COMPARISON_NUMBER_RE.finditer(normalized_raw):
            value_text = match.group("value").replace(",", "")
            if (
                value_text.startswith("-")
                and parsed
                and re.search(r"[A-Za-z0-9%)]\s*$", normalized_raw[:match.start()])
            ):
                # A compact range such as 37-47 cents uses the hyphen as a
                # delimiter, not as the sign of the upper endpoint.
                value_text = value_text[1:]
            value = Decimal(value_text)
            if "(" in normalized_raw[max(0, match.start() - 2):match.end() + 1] and ")" in normalized_raw[match.start():match.end() + 2]:
                value = -value
            parsed.append((value, (match.group("unit") or "").casefold()))
    except (InvalidOperation, KeyError):
        return None
    if not parsed:
        return None
    explicit_units = {unit for _value, unit in parsed if unit}
    if len(explicit_units) > 1:
        # A structured range must use one economic scale. Do not compare a
        # malformed or genuinely mixed-unit range by accident.
        return None
    inherited_unit = next(iter(explicit_units), "")
    values = [
        value * _COMPARISON_SCALE[unit or inherited_unit]
        for value, unit in parsed
    ]
    return (min(values), max(values))


def _comparison_bounds_for_metric(
    raw: str,
    metric: str,
) -> tuple[Decimal, Decimal] | None:
    bounds = _comparison_bounds(raw)
    if bounds is None or metric != "eps_loss":
        return bounds
    low, high = bounds
    # A larger quoted loss is economically lower. Convert loss magnitudes to
    # signed EPS before comparing the issuer range with the benchmark range.
    return (-high, -low)


def _authored_comparison_relation(raw: str) -> str | None:
    normalized = " ".join(str(raw).casefold().split())
    if re.search(r"\b(?:below|under|short of|fell short of)\b", normalized):
        return "below"
    if re.search(r"\b(?:above|beat|beats|beating)\b", normalized):
        return "above"
    return None


def _normalize_comparison_metric(raw: str) -> str:
    metric = " ".join(raw.casefold().split())
    if "loss" in metric:
        return "eps_loss"
    if "eps" in metric or "earnings" in metric:
        return "eps"
    if re.fullmatch(r"rev\.?|revenues?", metric):
        return "revenue"
    return metric.replace(" ", "_")


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    return any(span[0] < end and span[1] > start for start, end in occupied)
