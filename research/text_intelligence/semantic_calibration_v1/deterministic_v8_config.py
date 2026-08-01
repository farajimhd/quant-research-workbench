from __future__ import annotations

from .deterministic_v6_config import PatternRule


DETERMINISTIC_V8_VERSION = "news_deterministic_v8_candidate_1"


# These metadata signals were discovered on the disjoint 9,997-document Sol
# teacher corpus and retained only when their structural meaning was also
# supported by the 900 human-reviewed development articles.  They are source
# rules, not fitted model parameters.
MOVER_TAGS = frozenset({
    "big gainers", "gainers", "losers", "mid-day losers", "mid-day movers",
    "movers from yesterday", "premarket movers", "top gainers", "top losers",
})
MARKET_UPDATE_TAGS = frozenset({
    "mid afternoon market update", "mid-afternoon market update",
    "mid morning market update", "mid-morning market update",
    "mid-day market update",
})
AUTOMATED_TAGS = frozenset({"bzi-recaps", "bzi-shorthist", "bzi-uoa"})
PREVIEW_TAGS = frozenset({"bzi-ep"})
ANALYST_CHANNELS = frozenset({
    "analyst color", "downgrades", "initiation", "price target",
    "reiteration", "upgrades",
})
PREVIEW_CHANNELS = frozenset({"previews"})


# A context-only passage is retained only when it contains an issuer event,
# not merely a reported price move, scheduled item, or symbol mention.
CONTEXT_EVENT_PATTERNS: tuple[str, ...] = (
    r"\b(?:earnings|eps|revenue|sales)\s+(?:beat|miss(?:ed|es)?)\b",
    r"\b(?:beat|miss(?:ed|es)?)\b.{0,80}\b(?:estimate|expectation|consensus)s?\b",
    r"\b(?:weak|strong|raised|lowered|cut|reaffirmed|withdrawn)\b.{0,60}\b(?:guidance|outlook|forecast)\b",
    r"\b(?:primary|key)\s+endpoint\b",
    r"\b(?:positive|negative)\b.{0,80}\b(?:clinical|trial|study|topline)\b",
    r"\b(?:fda|regulator|european commission)\b.{0,100}\b(?:approv|accept|reject|grant|hold|fast track|response letter)\w*\b",
    r"\b(?:offering|private placement|at[- ]the[- ]market|share registration|s-1 filing)\b",
    r"\b(?:acqui(?:re|sition)|merger|go private|definitive agreement)\b",
    r"\b(?:contract|purchase order|partnership|collaboration|license agreement)\b",
    r"\b(?:investigation|subpoena|lawsuit|litigation|settlement|recall)\b",
    r"\b(?:bankruptcy|chapter\s+(?:7|11)|going concern|delisting|noncompliance)\b",
    r"\b(?:restructuring|workforce reduction|layoffs?|shutdown|production halt)\b",
    r"\b(?:buyback|repurchase|dividend)\b",
)


# Supplemental signed concepts cover recurrent financial constructions that
# V7 left neutral.  Each rule contributes at most once per issuer passage.
DIRECTION_RULES_V8: tuple[PatternRule, ...] = (
    PatternRule("direct_earnings_beat", (
        r"\b(?:earnings|eps|revenue|sales)\s+(?:beat|beats)\b",
        r"\bupbeat\s+(?:earnings|results|guidance)\b",
    ), weight=0.9, concept_family="earnings"),
    PatternRule("direct_earnings_miss", (
        r"\b(?:earnings|eps|revenue|sales)\s+miss(?:ed|es)?\b",
        r"\bdisappointing\s+(?:earnings|results|guidance)\b",
    ), weight=-0.9, concept_family="earnings"),
    PatternRule("weak_guidance", (
        r"\b(?:weak|light|soft|downbeat)\b.{0,45}\b(?:guidance|outlook|forecast)\b",
        r"\b(?:guidance|outlook|forecast)\b.{0,45}\b(?:weak|light|soft|downbeat|below consensus)\b",
    ), weight=-1.1, concept_family="guidance"),
    PatternRule("robust_guidance", (
        r"\b(?:strong|robust|upbeat)\b.{0,45}\b(?:guidance|outlook|forecast)\b",
    ), weight=0.9, concept_family="guidance"),
    PatternRule("reported_loss", (
        r"\breported\b.{0,80}\b(?:net|gaap|adjusted)?\s*loss\b",
        r"\bloss\s+of\s+\$?\d+(?:\.\d+)?\s*(?:cents?|per share)\b",
    ), weight=-0.35, concept_family="earnings"),
    PatternRule("accretive_transaction", (
        r"\b(?:accretive|add)\b.{0,80}\b(?:eps|earnings per share|earnings)\b",
    ), weight=0.65, concept_family="ma_transaction"),
    PatternRule("dilutive_transaction", (
        r"\bdilutive\b.{0,80}\b(?:margin|eps|earnings per share|earnings)\b",
    ), weight=-0.65, concept_family="ma_transaction"),
    PatternRule("regulatory_progress", (
        r"\b(?:fda|european commission|regulator)\b.{0,120}\b(?:accept(?:ed|s)?|validat(?:ed|es)|fast track|accelerated assessment|granted)\b",
        r"\b(?:application|bla|nda|maa)\b.{0,100}\baccepted\s+for\s+review\b",
    ), weight=0.8, concept_family="regulatory"),
    PatternRule("positive_data", (
        r"\bpositive\b.{0,80}\b(?:data|results|outcomes?|study|trial)\b",
        r"\b(?:demonstrated|showed)\b.{0,100}\b(?:potent|significant|effective|efficacy|improvement)\b",
    ), weight=0.85, concept_family="clinical"),
    PatternRule("development_setback", (
        r"\b(?:partial|full)\s+clinical\s+hold\b",
        r"\brecommend(?:ed|ing)?\s+withdrawal\s+of\s+approval\b",
    ), weight=-1.2, concept_family="regulatory"),
    PatternRule("commercial_demand", (
        r"\b(?:preorders?|orders?|bookings?|customers?|test volume)\b.{0,80}\b(?:grew|growth|increased|strong|record|exceeded)\b",
        r"\b(?:received|has)\b.{0,50}\b\d[\d,]*\s+pre[- ]?orders?\b",
    ), weight=0.65, concept_family="product_commercial"),
    PatternRule("strategic_alternatives", (
        r"\bexplor(?:e|ing)\b.{0,100}\b(?:strategic alternatives?|sale|merger|partnership)\b",
    ), weight=0.45, concept_family="ma_transaction"),
    PatternRule("patent_or_license_grant", (
        r"\b(?:patent|license)\b.{0,100}\b(?:allowed|approved|granted|renewed)\b",
    ), weight=0.45, concept_family="regulatory"),
    PatternRule("enrollment_progress", (
        r"\b(?:exceeded|reached)\b.{0,60}\benrollment\s+target\b",
    ), weight=0.55, concept_family="clinical"),
    PatternRule("filing_delay", (
        r"\b(?:delay|delayed|late)\b.{0,80}\b(?:10-k|10-q|annual report|quarterly report|filing)\b",
        r"\bmay\s+delay\b.{0,80}\bfiling\b",
    ), weight=-0.75, concept_family="regulatory"),
    PatternRule("material_weakness", (
        r"\bmaterial weaknesses?\b.{0,100}\b(?:not|had not|unremediated|remain)\w*\b",
    ), weight=-0.8, concept_family="legal"),
    PatternRule("distribution_expansion", (
        r"\b(?:available|launch(?:ed|es)?)\b.{0,100}\b(?:stores?|locations?|nationwide)\b",
        r"\bextended\s+distribution\s+agreement\b",
    ), weight=0.4, concept_family="product_commercial"),
)
