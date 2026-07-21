from __future__ import annotations

from dataclasses import dataclass

from pipelines.news.benzinga.news_reaction_phrase_dictionary import PHRASE_RULES


EVENT_DICTIONARY_VERSION = "news_semantic_event_dictionary_v2_1"


@dataclass(frozen=True, slots=True)
class SemanticEventRule:
    event_id: str
    canonical_event: str
    family: str
    direction: int
    strength: float
    materiality: float
    certainty: float
    time_orientation: str
    feature_role: str
    needles: tuple[str, ...]


FORWARD_FAMILIES = {
    "guidance",
    "contracts_orders",
    "products_commercial",
    "regulatory_clinical",
    "management_governance",
    "operations",
    "credit_solvency",
    "analyst_action",
    "macro",
}
HISTORICAL_PREFIXES = ("earnings_",)
STRUCTURAL_FAMILIES = {"capital_allocation", "financing", "mergers_acquisitions", "legal_compliance"}


def _orientation(phrase_id: str, family: str) -> str:
    if phrase_id.startswith(HISTORICAL_PREFIXES):
        return "historical"
    if family in FORWARD_FAMILIES:
        return "forward"
    if family in STRUCTURAL_FAMILIES:
        return "structural"
    return "current"


def _materiality(family: str, strength: float) -> float:
    family_multiplier = {
        "guidance": 1.20,
        "regulatory_clinical": 1.20,
        "credit_solvency": 1.25,
        "earnings": 1.10,
        "financing": 1.10,
        "mergers_acquisitions": 1.05,
        "legal_compliance": 1.05,
        "analyst_action": 0.80,
        "products_commercial": 0.85,
        "market_reaction": 0.60,
    }.get(family, 1.0)
    return min(1.0, max(0.1, strength * family_multiplier))


def _certainty(phrase_id: str) -> float:
    if any(token in phrase_id for token in ("proposal", "initiate", "outlook")):
        return 0.75
    if any(token in phrase_id for token in ("agreement", "authorize", "approval", "clearance", "award")):
        return 0.95
    return 0.90


BASE_EVENT_RULES = tuple(
    SemanticEventRule(
        event_id=rule.phrase_id,
        canonical_event=rule.canonical_phrase,
        family=rule.family,
        direction=rule.direction,
        strength=rule.strength,
        materiality=_materiality(rule.family, rule.strength),
        certainty=_certainty(rule.phrase_id),
        time_orientation=_orientation(rule.phrase_id, rule.family),
        feature_role=rule.feature_role,
        needles=rule.needles,
    )
    for rule in PHRASE_RULES
)


EXTRA_EVENT_RULES: tuple[SemanticEventRule, ...] = (
    SemanticEventRule("clinical_no_safety_concern", "No new safety concern", "regulatory_clinical", 1, 0.45, 0.55, 0.90, "forward", "event_language", ("raised no safety concerns", "found no safety concerns", "identified no new safety concerns")),
    SemanticEventRule("clinical_positive_response", "Positive clinical response", "regulatory_clinical", 1, 0.80, 0.90, 0.90, "forward", "event_language", ("positive early responses", "durable functional benefit", "statistically significant improvement")),
    SemanticEventRule("regulatory_positive_opinion", "Positive regulatory opinion", "regulatory_clinical", 1, 0.70, 0.80, 0.95, "forward", "event_language", ("adopts positive opinion", "positive opinion for orphan drug designation")),
    SemanticEventRule("financing_atm_facility", "ATM equity facility", "financing", -1, 0.65, 0.75, 0.95, "structural", "event_language", ("atm equity offering facility", "at-the-market equity program", "equity distribution agreement", "equity line of credit")),
    SemanticEventRule("financing_prefunded_warrants", "Pre-funded warrant financing", "financing", -1, 0.70, 0.80, 0.95, "structural", "event_language", ("pre-funded warrants", "prefunded warrants", "new warrants")),
    SemanticEventRule("operations_large_workforce_cut", "Large workforce reduction", "operations", -1, 0.75, 0.85, 0.95, "forward", "event_language", ("reduce workforce by", "reducing its full-time workforce", "workforce by more than", "exit 22 countries")),
    SemanticEventRule("earnings_double_miss", "Earnings double miss", "earnings", -1, 0.85, 0.95, 0.95, "historical", "event_language", ("double miss", "earnings come up short")),
    SemanticEventRule("revenue_yoy_growth", "Revenue grows year over year", "earnings", 1, 0.55, 0.60, 0.95, "historical", "event_language", ("revenue up from", "sales up from", "revenue increased year over year")),
    SemanticEventRule("debt_repayment", "Repays debt", "financing", 1, 0.55, 0.65, 0.95, "structural", "event_language", ("debt repayment", "completes $1b debt repayment", "paid down debt")),
    SemanticEventRule("investment_received", "Receives strategic investment", "financing", 1, 0.45, 0.55, 0.90, "structural", "event_language", ("investment from", "strategic investment", "received an investment")),
    SemanticEventRule("contract_revenue_added", "Contract adds revenue", "contracts_orders", 1, 0.65, 0.75, 0.95, "forward", "event_language", ("adds $1m in revenue", "contract update", "contract awards with")),
    SemanticEventRule("regulatory_deal_approval", "Regulator approves transaction", "mergers_acquisitions", 1, 0.70, 0.80, 0.95, "structural", "event_language", ("justice department approves", "regulatory approval for the acquisition", "clears strategic deal")),
    SemanticEventRule("deal_offer_rejected", "Acquisition offer rejected", "mergers_acquisitions", -1, 0.65, 0.75, 0.95, "structural", "event_language", ("rejects acquisition offer", "rejects gamestop's unsolicited bid", "offer was rejected")),
    SemanticEventRule("analyst_maintains_positive", "Maintains positive rating", "analyst_action", 1, 0.40, 0.35, 0.95, "forward", "event_language", ("maintains buy", "maintains outperform", "maintains overweight", "reiterates buy")),
    SemanticEventRule("analyst_maintains_negative", "Maintains negative rating", "analyst_action", -1, 0.45, 0.40, 0.95, "forward", "event_language", ("maintains sell", "maintains underperform", "maintains underweight")),
)


EVENT_RULES = BASE_EVENT_RULES + EXTRA_EVENT_RULES


def validate_event_rules(rules: tuple[SemanticEventRule, ...] = EVENT_RULES) -> None:
    event_ids = [rule.event_id for rule in rules]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("semantic event dictionary contains duplicate event IDs")
    if any(rule.direction not in {-1, 0, 1} for rule in rules):
        raise ValueError("semantic event direction must be -1, 0, or 1")
    if any(not 0.0 <= value <= 1.0 for rule in rules for value in (rule.strength, rule.materiality, rule.certainty)):
        raise ValueError("semantic event weights must be between zero and one")
    if any(rule.time_orientation not in {"historical", "current", "forward", "structural"} for rule in rules):
        raise ValueError("invalid semantic event time orientation")
    if any(not rule.needles for rule in rules):
        raise ValueError("each semantic event requires at least one phrase variant")
    if any(needle != needle.strip().lower() for rule in rules for needle in rule.needles):
        raise ValueError("semantic event phrase variants must be normalized lowercase text")


validate_event_rules()
