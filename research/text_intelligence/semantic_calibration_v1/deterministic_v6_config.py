from __future__ import annotations

from dataclasses import dataclass


DETERMINISTIC_V6_VERSION = "news_deterministic_v6_candidate_1"


@dataclass(frozen=True, slots=True)
class PatternRule:
    rule_id: str
    patterns: tuple[str, ...]
    value: str = ""
    weight: float = 0.0
    concept_family: str = ""


# Ordered from the most structurally specific format to the least specific.
# These expressions are intentionally readable source configuration. They are
# not coefficients learned from the reviewed collection.
ROLE_RULES: tuple[PatternRule, ...] = (
    PatternRule("why_moving", (
        r"\bwhy\s+(?:is|are|did)\b.{0,100}\b(?:moving|up|down|rising|falling)\b",
        r"\bwhat(?:'s| is)\s+going\s+on\s+with\b",
    ), "why_moving_followup"),
    PatternRule("mover_recap", (
        r"\b\d+\s+stocks?\s+(?:moving|to watch)\b",
        r"\b(?:biggest|top)\s+(?:stock\s+)?(?:gainers|losers|movers)\b",
        r"\b(?:pre[- ]?market|after[- ]?hours?|mid[- ]?day)\s+movers?\b",
        r"\bstocks?\s+moving\s+in\s+(?:monday|tuesday|wednesday|thursday|friday)'?s\b",
    ), "mover_recap"),
    PatternRule("market_roundup", (
        r"\bmarket\s+update\b",
        r"\b(?:mid[- ]?morning|mid[- ]?afternoon|opening|closing)\s+market\s+update\b",
        r"\bthe\s+daily\s+(?:biotech|technology|energy)\s+pulse\b",
        r"\bmarkets?\s+(?:today|this week|wrap|recap)\b",
        r"\bleading\s+and\s+lagging\s+sectors\b",
    ), "market_roundup"),
    PatternRule("automated_summary", (
        r"\binsights?\s+into\b.{0,120}\bperformance\b",
        r"\bindustry\s+comparison\b",
        r"\bperformance\s+versus\s+peers\b",
        r"\bautomatically\s+generated\b",
        r"\bbenzinga\s+insights\b",
    ), "automated_summary"),
    PatternRule("analyst_event", (
        r"\b(?:analyst|brokerage|research\s+firm)\b.{0,160}\b(?:maintains?|reiterates?|upgrades?|downgrades?|initiates?|resumes?|price\s+target|rating)\b",
        r"\b(?:maintains?|reiterates?|upgrades?|downgrades?|initiates?|resumes?)\b.{0,120}\b(?:buy|sell|hold|overweight|underweight|outperform|underperform|neutral)\b",
        r"\b(?:raises?|lowers?|cuts?)\s+(?:its\s+)?price\s+target\b",
    ), "analyst_event"),
    PatternRule("preview", (
        r"\bearnings\s+(?:preview|outlook)\b",
        r"\bwhat\s+(?:investors|traders)\s+need\s+to\s+know\s+(?:before|ahead of)\b",
        r"\b(?:scheduled|expected)\s+to\s+report\s+(?:quarterly\s+)?(?:earnings|results)\b",
        r"\bweek\s+ahead\b",
    ), "preview"),
    PatternRule("regulatory_event", (
        r"\b(?:fda|food and drug administration)\b",
        r"\b(?:sec|securities and exchange commission)\s+(?:filing|form|investigation)\b",
        r"\b(?:nasdaq|nyse)\s+(?:listing|noncompliance|delisting)\b",
    ), "regulatory_event"),
    PatternRule("primary_event", (
        r"\b(?:announces?|reports?|launches?|receives?|secures?|signs?|enters?|acquires?|merges?|prices?|completes?|closes?)\b",
        r"\b(?:earnings|quarterly results|clinical results|offering|financing|acquisition|merger|contract|partnership|guidance)\b",
    ), "primary_event"),
)


ORIGIN_AUTOMATED_PATTERNS = (
    r"\bbenzinga\s+insights\b",
    r"\bautomatically\s+generated\b",
    r"\binsights?\s+into\b.{0,120}\bperformance\b",
)
ORIGIN_ISSUER_PATTERNS = (
    r"\bpress\s+release\b",
    r"\bcompany\s+news\b",
    r"\b(?:business\s+wire|globe\s+newswire|pr\s+newswire|accesswire)\b",
)
ORIGIN_REGULATORY_PATTERNS = (
    r"\bsec\.gov\b",
    r"\bregulatory\s+filing\b",
)


# Supplemental rules fill ordinary financial-language gaps in V5. A rule is
# counted at most once per issuer-scoped evidence unit, no matter how often its
# phrase repeats. Positive and negative evidence remain separate so mixed
# language is not collapsed by a single keyword sum.
DIRECTION_RULES: tuple[PatternRule, ...] = (
    PatternRule("earnings_beat", (
        r"\bbetter[- ]than[- ]expected\b",
        r"\bbeat(?:s|ing)?\b.{0,80}\b(?:estimates?|expectations?|consensus)\b",
        r"\b(?:revenue|sales|earnings|eps)\b.{0,80}\b(?:above|exceeded)\b.{0,60}\b(?:estimates?|expectations?|consensus)\b",
    ), weight=1.1, concept_family="earnings"),
    PatternRule("earnings_miss", (
        r"\bweaker[- ]than[- ]expected\b",
        r"\bmiss(?:es|ed)?\b.{0,80}\b(?:estimates?|expectations?|consensus)\b",
        r"\b(?:revenue|sales|earnings|eps)\b.{0,80}\b(?:below|fell short of)\b.{0,60}\b(?:estimates?|expectations?|consensus)\b",
    ), weight=-1.1, concept_family="earnings"),
    PatternRule("guidance_raise", (
        r"\b(?:raises?|boosts?|increases?)\b.{0,60}\b(?:guidance|outlook|forecast)\b",
        r"\b(?:guidance|outlook|forecast)\b.{0,60}\b(?:raised|increased|above consensus)\b",
    ), weight=1.2, concept_family="guidance"),
    PatternRule("guidance_cut", (
        r"\b(?:cuts?|lowers?|reduces?|withdraws?)\b.{0,60}\b(?:guidance|outlook|forecast)\b",
        r"\b(?:guidance|outlook|forecast)\b.{0,60}\b(?:cut|lowered|reduced|withdrawn)\b",
    ), weight=-1.3, concept_family="guidance"),
    PatternRule("revenue_growth", (
        r"\b(?:revenue|sales)\b.{0,60}\b(?:grew|rose|increased|record)\b",
        r"\brecord\s+(?:quarterly|annual)?\s*(?:revenue|sales)\b",
    ), weight=0.55, concept_family="earnings"),
    PatternRule("revenue_decline", (
        r"\b(?:revenue|sales)\b.{0,60}\b(?:fell|declined|decreased|contracted)\b",
        r"\b(?:declining|lower)\s+(?:revenue|sales)\b",
    ), weight=-0.55, concept_family="earnings"),
    PatternRule("profit_positive", (
        r"\b(?:returned to|achieved)\s+profitability\b",
        r"\brecord\s+(?:adjusted\s+)?(?:profit|ebitda|net income)\b",
    ), weight=0.7, concept_family="earnings"),
    PatternRule("strong_results", (
        r"\bstrong\s+(?:quarterly|annual|financial|clinical)\s+(?:results|performance)\b",
        r"\b(?:earnings|profit|net income)\b.{0,60}\b(?:rose|increased|improved|record)\b",
    ), weight=0.7, concept_family="earnings"),
    PatternRule("loss_pressure", (
        r"\b(?:net|operating)\s+loss\b.{0,80}\b(?:widened|increased|grew)\b",
        r"\bwider[- ]than[- ]expected\s+loss\b",
    ), weight=-0.65, concept_family="earnings"),
    PatternRule("weak_results", (
        r"\bweak(?:er)?\s+(?:quarterly|annual|financial|clinical)\s+(?:results|performance)\b",
        r"\b(?:earnings|profit|net income)\b.{0,60}\b(?:fell|declined|decreased|deteriorated)\b",
    ), weight=-0.7, concept_family="earnings"),
    PatternRule("clinical_success", (
        r"\bmet\s+(?:its\s+|the\s+)?(?:primary|key)\s+endpoint\b",
        r"\bpositive\s+(?:topline|clinical|phase\s+[123])\s+(?:data|results)\b",
        r"\bstatistically\s+significant\b.{0,100}\bimprovement\b",
    ), weight=1.3, concept_family="clinical"),
    PatternRule("clinical_failure", (
        r"\b(?:failed|did not meet)\b.{0,60}\b(?:primary|key)\s+endpoint\b",
        r"\bclinical\s+hold\b",
        r"\bcomplete\s+response\s+letter\b",
    ), weight=-1.45, concept_family="clinical"),
    PatternRule("regulatory_approval", (
        r"\b(?:fda|regulator|european commission)\b.{0,100}\b(?:approves?|approval|authorized)\b",
        r"\breceives?\b.{0,80}\b(?:fda|regulatory)\s+approval\b",
    ), weight=1.35, concept_family="regulatory"),
    PatternRule("regulatory_setback", (
        r"\b(?:fda|regulator)\b.{0,120}\b(?:rejects?|denies?|deficienc|declines?)\b",
        r"\b(?:approval|application)\b.{0,100}\b(?:rejected|denied)\b",
    ), weight=-1.35, concept_family="regulatory"),
    PatternRule("financing_dilutive", (
        r"\b(?:registered direct|underwritten public|public)\s+offering\b",
        r"\bat[- ]the[- ]market\s+(?:offering|sales agreement)\b",
        r"\bprivate\s+placement\b",
        r"\b(?:issue|sale|sell|offering)\b.{0,100}\b(?:common shares?|common stock|warrants?)\b",
    ), weight=-0.9, concept_family="financing"),
    PatternRule("capital_return", (
        r"\b(?:increases?|raises?)\b.{0,50}\bdividend\b",
        r"\b(?:share repurchase|stock buyback|repurchase authorization)\b",
    ), weight=0.75, concept_family="capital_return"),
    PatternRule("contract_award", (
        r"\b(?:awarded|secured|won|receives?)\b.{0,100}\b(?:contract|purchase order|order)\b",
        r"\bcontract\s+award\b",
    ), weight=0.75, concept_family="contract_order"),
    PatternRule("commercial_launch", (
        r"\b(?:launches?|commercializes?|begins? commercial)\b.{0,100}\b(?:product|service|platform|drug)\b",
    ), weight=0.45, concept_family="product_commercial"),
    PatternRule("commercial_progress", (
        r"\b(?:record|strong)\s+(?:orders?|bookings?|demand|prescriptions?|shipments?)\b",
        r"\b(?:orders?|bookings?|prescriptions?|shipments?)\b.{0,60}\b(?:grew|rose|increased|record)\b",
    ), weight=0.55, concept_family="product_commercial"),
    PatternRule("partnership", (
        r"\b(?:strategic\s+)?(?:partnership|collaboration|license agreement)\b",
    ), weight=0.45, concept_family="contract_order"),
    PatternRule("ma_signed", (
        r"\b(?:definitive\s+)?(?:merger|acquisition)\s+agreement\b",
        r"\bagree(?:s|d)?\s+to\s+acquire\b",
    ), weight=0.7, concept_family="ma_transaction"),
    PatternRule("bankruptcy", (
        r"\bchapter\s+(?:7|11)\b",
        r"\bbankruptcy\s+(?:filing|protection)\b",
        r"\bsubstantial\s+doubt\b.{0,80}\bgoing\s+concern\b",
    ), weight=-1.6, concept_family="credit_solvency"),
    PatternRule("legal_negative", (
        r"\b(?:formal|regulatory|criminal)\s+investigation\b",
        r"\b(?:subpoena|class action lawsuit|fraud charges?)\b",
    ), weight=-0.8, concept_family="legal"),
    PatternRule("listing_negative", (
        r"\b(?:delisting|noncompliance|minimum bid price)\b",
        r"\breverse\s+(?:stock|share)\s+split\b",
    ), weight=-0.75, concept_family="listing_market_structure"),
    PatternRule("operations_negative", (
        r"\b(?:restructuring|workforce reduction|reduction in force|layoffs?)\b",
        r"\b(?:shutdown|closure|production halt|supply disruption)\b",
    ), weight=-0.6, concept_family="operations"),
    PatternRule("analyst_positive", (
        r"\bupgrades?\b.{0,100}\b(?:buy|overweight|outperform)\b",
        r"\braises?\b.{0,40}\bprice\s+target\b",
    ), weight=0.5, concept_family="analyst_action"),
    PatternRule("analyst_negative", (
        r"\bdowngrades?\b.{0,100}\b(?:sell|underweight|underperform|hold)\b",
        r"\b(?:cuts?|lowers?)\b.{0,40}\bprice\s+target\b",
    ), weight=-0.5, concept_family="analyst_action"),
)


POSITIVE_THRESHOLD = 0.45
NEGATIVE_THRESHOLD = -0.45
MIXED_COMPONENT_THRESHOLD = 0.45
MIXED_DOMINANCE_MARGIN = 1.15
