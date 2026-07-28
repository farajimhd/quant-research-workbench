from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ConceptRule:
    family: str
    subtype: str
    phrases: tuple[str, ...]
    direction: str = "neutral"
    weight: float = 0.0
    modality: str = "confirmed"
    time_orientation: str = "current"
    patterns: tuple[str, ...] = ()


CONCEPT_RULES: tuple[ConceptRule, ...] = (
    ConceptRule("financing", "registered_direct", ("registered direct offering",), "negative", -1.0),
    ConceptRule("financing", "public_offering", ("underwritten public offering", "public offering"), "negative", -0.9),
    ConceptRule("financing", "at_the_market", ("at-the-market offering", "at the market offering", "sales agreement"), "negative", -0.8),
    ConceptRule("financing", "private_placement", ("private placement",), "mixed", -0.3),
    ConceptRule("financing", "convertible_debt", ("convertible note", "convertible senior notes"), "mixed", -0.3),
    ConceptRule(
        "financing",
        "warrant",
        ("warrant exercise", "purchase warrants"),
        "mixed",
        -0.4,
        patterns=(
            r"\bwarrants?\b[^.\n]{0,120}\bexercise price\b",
            r"\bexercise price\b[^.\n]{0,120}\bwarrants?\b",
        ),
    ),
    ConceptRule("financing", "debt_conversion", ("strategic financing agreement", "outstanding debt into", "debt into preferred"), "negative", -0.7, modality="planned", time_orientation="forward"),
    ConceptRule("capital_structure", "reverse_split", ("reverse stock split", "reverse share split"), "negative", -0.7),
    ConceptRule("capital_structure", "forward_split", ("forward stock split",), "neutral", 0.0),
    ConceptRule("credit_solvency", "bankruptcy", ("chapter 11", "chapter 7", "bankruptcy protection"), "negative", -2.0),
    ConceptRule("credit_solvency", "going_concern", ("substantial doubt", "going concern"), "negative", -1.3),
    ConceptRule(
        "guidance",
        "raise",
        ("raises guidance", "raised guidance", "increases guidance"),
        "positive",
        1.2,
        time_orientation="forward",
        patterns=(r"\bguidance\s+(?:was\s+|has\s+been\s+)?raised\b",),
    ),
    ConceptRule(
        "guidance",
        "cut",
        ("cuts guidance", "lowered guidance", "reduces guidance"),
        "negative",
        -1.4,
        time_orientation="forward",
        patterns=(r"\bguidance\s+(?:was\s+|has\s+been\s+)?(?:cut|lowered|reduced)\b",),
    ),
    ConceptRule("guidance", "reaffirm", ("reaffirms guidance", "reiterates guidance"), "neutral", 0.2, time_orientation="forward"),
    ConceptRule("earnings", "beat", ("beats estimates", "above consensus", "earnings beat"), "positive", 1.0, time_orientation="historical"),
    ConceptRule("earnings", "miss", ("misses estimates", "below consensus", "earnings miss"), "negative", -1.0, time_orientation="historical"),
    ConceptRule(
        "earnings",
        "revenue_growth",
        ("net revenue growth", "revenue increased", "sales increased"),
        "positive",
        0.6,
        time_orientation="historical",
        patterns=(r"\brevenue\s+(?:grew|rose|increased)\s+(?:by\s+)?\d",),
    ),
    ConceptRule(
        "earnings",
        "revenue_decline",
        ("revenue declined", "revenue decreased", "sales declined"),
        "negative",
        -0.6,
        time_orientation="historical",
        patterns=(r"\brevenue\s+(?:fell|declined|decreased)\s+(?:by\s+)?\d",),
    ),
    ConceptRule(
        "earnings",
        "profitability_improvement",
        ("record adjusted ebitda", "achieved profitability", "returned to profitability"),
        "positive",
        0.8,
        time_orientation="historical",
    ),
    ConceptRule("regulatory", "fda_approval", ("fda approval", "fda approved", "food and drug administration approval"), "positive", 1.7),
    ConceptRule("regulatory", "fda_rejection", ("complete response letter", "fda rejected", "fda denial"), "negative", -1.8),
    ConceptRule("clinical", "success", ("met primary endpoint", "statistically significant", "positive topline results"), "positive", 1.4),
    ConceptRule("clinical", "failure", ("failed primary endpoint", "did not meet primary endpoint", "clinical hold"), "negative", -1.6),
    ConceptRule("clinical", "data_publication", ("publication of data", "published results", "new clinical data"), "neutral", 0.1, time_orientation="historical"),
    ConceptRule("ma_transaction", "merger_agreement", ("definitive merger agreement", "business combination agreement"), "positive", 0.7),
    ConceptRule("ma_transaction", "acquisition", ("agreed to acquire", "latest acquisition", "acquisition agreement"), "mixed", 0.2),
    ConceptRule("ma_transaction", "merger_termination", ("terminated merger agreement", "merger termination"), "negative", -0.9),
    ConceptRule(
        "contract_order",
        "award",
        ("awarded a contract", "contract award"),
        "positive",
        0.8,
        patterns=(
            r"\b(?:received|secured|won|announced)\s+(?:a\s+)?"
            r"(?:new\s+)?purchase order\b",
        ),
    ),
    ConceptRule("contract_order", "termination", ("contract termination", "terminated the contract"), "negative", -0.9),
    ConceptRule("capital_return", "buyback", ("share repurchase", "stock buyback", "repurchase authorization"), "positive", 0.8),
    ConceptRule("capital_return", "dividend_increase", ("increased dividend", "dividend increase"), "positive", 0.7),
    ConceptRule("capital_return", "dividend_suspension", ("suspended dividend", "dividend suspension"), "negative", -1.2),
    ConceptRule("operations", "restructuring", ("restructuring plan", "workforce reduction", "reduction in force"), "negative", -0.7),
    ConceptRule("accounting_audit", "material_weakness", ("material weakness", "ineffective internal control"), "negative", -1.0),
    ConceptRule("accounting_audit", "restatement", ("financial restatement", "will restate", "should no longer be relied upon"), "negative", -1.4),
    ConceptRule("listing_market_structure", "noncompliance", ("listing compliance", "minimum bid price", "delisting notice"), "negative", -0.9),
    ConceptRule("listing_market_structure", "trading_halt", ("trading halt", "halted pending news"), "neutral", 0.0),
    ConceptRule("legal", "investigation", ("formal investigation", "regulatory investigation", "subpoena"), "negative", -0.8),
    ConceptRule("legal", "lawsuit", ("class action lawsuit", "patent infringement lawsuit", "filed a lawsuit"), "negative", -0.7),
    ConceptRule("legal", "settlement", ("settlement agreement", "agreed to settle"), "mixed", 0.1),
    ConceptRule("ownership", "insider_buy", ("insider purchase", "insider bought"), "positive", 0.5),
    ConceptRule("ownership", "insider_sell", ("insider sale", "insider sold"), "negative", -0.4),
    ConceptRule("analyst_action", "upgrade", ("analyst upgrade", "upgrades to buy", "raises price target"), "positive", 0.5, modality="opinion"),
    ConceptRule("analyst_action", "downgrade", ("analyst downgrade", "downgrades to sell", "cuts price target", "downgraded", "cut to hold", "lowered its rating"), "negative", -0.5, modality="opinion"),
    ConceptRule("earnings", "preview", ("earnings outlook", "earnings report", "analysts estimate"), "neutral", 0.0, modality="estimated", time_orientation="forward"),
    ConceptRule("regulatory", "broker_financial_report", ("x-17a-5",), "neutral", 0.0),
    ConceptRule("regulatory", "trustee_eligibility", ("form t-1", "trust indenture act"), "neutral", 0.0),
    ConceptRule(
        "management_governance",
        "board_appointment",
        ("elected to the board of directors", "appointed to the board of directors"),
        "neutral",
        0.1,
        patterns=(r"\b(?:elected|appointed)\s+to\s+[^.\n]{0,80}\bboard(?:\s+of\s+directors)?\b",),
    ),
    ConceptRule("management_governance", "compensation_plan_amendment", ("stock deferral plan", "the plan is hereby amended"), "neutral", 0.0),
    ConceptRule("management_governance", "power_of_attorney", ("power of attorney", "attorney-in-fact"), "neutral", 0.0),
)

ROLE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("market_roundup", re.compile(r"\b(?:biggest|top)\s+(?:stock\s+)?(?:gainers|losers|movers)\b|\bstocks?\s+moving\s+in\b", re.IGNORECASE)),
    ("mover_recap", re.compile(r"\b(?:premarket|after-hours|mid-day|midday)\s+movers?\b", re.IGNORECASE)),
    ("why_moving_followup", re.compile(r"\bwhy\s+is\b.+\b(?:moving|up|down)\b|\bwhy\b.+\bstock\s+is\s+moving\b", re.IGNORECASE)),
    ("analyst_event", re.compile(r"\b(?:upgrades?|downgrades?|initiates?|price target|analyst)\b", re.IGNORECASE)),
    ("preview", re.compile(r"\b(?:earnings outlook|what investors need to know before|preview)\b", re.IGNORECASE)),
    ("administrative", re.compile(r"\b(?:power of attorney|form id|edgar next|trust indenture act)\b", re.IGNORECASE)),
    ("regulatory_event", re.compile(r"\b(?:sec filing|form\s+(?:8-k|10-q|10-k)|fda|regulatory)\b", re.IGNORECASE)),
)

MODALITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("rumored", re.compile(r"\b(?:rumou?red|reportedly|sources say|considering)\b", re.IGNORECASE)),
    ("proposed", re.compile(r"\b(?:proposes?|proposed|subject to approval|intends? to)\b", re.IGNORECASE)),
    ("planned", re.compile(r"\b(?:plans? to|expects? to|will)\b", re.IGNORECASE)),
    ("estimated", re.compile(r"\b(?:estimates?|approximately|forecast)\b", re.IGNORECASE)),
    ("opinion", re.compile(r"\b(?:believes?|opinion|analyst|rating)\b", re.IGNORECASE)),
)

TIME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("forward", re.compile(r"\b(?:will|expects?|guidance|outlook|forecast|next quarter|next year)\b", re.IGNORECASE)),
    ("historical", re.compile(r"\b(?:reported|for the quarter ended|last year|year-over-year|previously)\b", re.IGNORECASE)),
    ("current", re.compile(r"\b(?:today|currently|announces?|has entered|is)\b", re.IGNORECASE)),
)

CONCEPT_BY_PHRASE: dict[str, str] = {
    phrase.casefold(): f"{rule.family}.{rule.subtype}"
    for rule in CONCEPT_RULES
    for phrase in rule.phrases
}
