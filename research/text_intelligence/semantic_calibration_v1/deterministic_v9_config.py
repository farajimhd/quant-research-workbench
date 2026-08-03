from __future__ import annotations

from dataclasses import dataclass


DETERMINISTIC_V9_VERSION = "news_deterministic_v9_candidate_21"
CALIBRATION_VERSION = "news_deterministic_v9_calibration_22"
CALIBRATION_SPLIT_SHA256 = "dd960925e6ae60a6a465847717a89b277b8f453341187db3058bca446481765f"

# These otherwise teacher-supported overrides were rejected because they made
# the already reviewed 900-item human development benchmark worse. This guard
# prevents weak teacher conventions from redefining semantic provenance.
HUMAN_DEVELOPMENT_GUARD_REJECTIONS: dict[str, tuple[str, ...]] = {
    "content_role": (
        "channel:contracts",
        "channel:press releases",
        "channel:price target",
        "channel:management",
        "channel:asset sales",
    ),
    "source_origin": (
        "tag:top downgrades",
        "title_family:offering",
        "tag:morgan stanley",
        "channel:analyst color",
        "channel:price target",
        "channel:downgrades",
        "channel:eurozone",
        "channel:initiation",
    ),
}


# The values below are frozen, readable runtime configuration derived from the
# 7,997-item development partition. Runtime inference does not load a model,
# teacher labels, or calibration artifacts.
ARTICLE_ROLE_OVERRIDES: dict[str, str] = {
    "tag:us stock futures": "market_roundup",
    "tag:us market preview": "market_roundup",
    "tag:earnings previews": "preview",
    "tag:top downgrades": "analyst_event",
    "channel:reiteration": "analyst_event",
    "channel:initiation": "analyst_event",
    "channel:analyst color": "analyst_event",
    "tag:contributors": "editorial_analysis",
    "title_family:earnings_preview": "preview",
    "channel:opinion": "editorial_analysis",
    "origin:issuer_distribution_channel": "primary_event",
    "channel:downgrades": "analyst_event",
    "origin:v5_direct_source_fallback": "primary_event",
    "channel:stock split": "primary_event",
    "tag:bofa securities": "analyst_event",
    "channel:treasuries": "market_roundup",
    "channel:personal finance": "editorial_analysis",
    "channel:analyst ratings": "analyst_event",
    "title_family:index_constituent_change": "primary_event",
}

SOURCE_ORIGIN_OVERRIDES: dict[str, str] = {
    "channel:opinion": "editorial_original",
    "tag:stocks to watch": "editorial_aggregation",
    "tag:mid day market update": "editorial_aggregation",
    "tag:contributors": "editorial_original",
    "tag:us stock futures": "editorial_aggregation",
    "channel:reiteration": "analyst_research",
    "channel:press releases": "issuer_direct",
    "tag:us market preview": "editorial_aggregation",
    "tag:earnings previews": "editorial_original",
    "channel:personal finance": "editorial_original",
    "tag:thomson reuters": "issuer_direct",
    "tag:bofa securities": "analyst_research",
    "tag:partner content": "editorial_original",
    "channel:stock split": "issuer_direct",
    "channel:pre-market outlook": "editorial_aggregation",
}

SINGLE_TICKER_CONCEPT_ADDITIONS: dict[str, tuple[str, ...]] = {
    "channel:downgrades": ("analyst_action",),
    "channel:buybacks": ("capital_return",),
    "title_family:analyst_maintains": ("analyst_action",),
    "channel:upgrades": ("analyst_action",),
    "role:why_moving": ("market_reaction",),
    "tag:morgan stanley": ("analyst_action",),
    "channel:initiation": ("analyst_action",),
    "tag:earnings conference call transcripts": ("earnings", "operations", "guidance"),
    "tag:wedbush": ("analyst_action",),
    "tag:piper jaffray": ("analyst_action",),
    "tag:oppenheimer": ("analyst_action",),
    "role:automated_earnings_preview_tag": ("earnings",),
    "tag:bzi-ep": ("earnings",),
    "tag:jefferies": ("analyst_action",),
    "tag:keybanc capital markets": ("analyst_action",),
    "tag:raymond james": ("analyst_action",),
    "tag:pt changes": ("analyst_action",),
    "channel:price target": ("analyst_action",),
    "channel:reiteration": ("analyst_action",),
    "title_family:analyst_changes": ("analyst_action",),
    "title_family:why_moving": ("market_reaction",),
    "title_family:earnings_preview": ("earnings",),
    "title_family:clinical": ("clinical",),
    "channel:analyst ratings": ("analyst_action",),
    "origin:analyst_distribution_channel": ("analyst_action",),
    "role:analyst_distribution_channel": ("analyst_action",),
    "channel:dividends": ("capital_return",),
    "channel:analyst color": ("analyst_action",),
    "tag:bank of america": ("analyst_action",),
    "channel:previews": ("earnings",),
    "tag:goldman sachs": ("analyst_action",),
    "channel:stock split": ("capital_structure",),
    "tag:credit suisse": ("analyst_action",),
    "tag:deutsche bank": ("analyst_action",),
    "title_family:ma": ("ma_transaction",),
    "origin:regulatory_primary_title": ("regulatory",),
    "channel:earnings": ("earnings",),
    "tag:stifel": ("analyst_action",),
    "title_family:mover_list": ("market_reaction",),
}

# A relational title describes one shared transaction even when its clauses
# contain issuer-specific consequences.  Preserve that event family on every
# explicitly resolved participant; downstream issuer-scoped rules may add more
# specific concepts without losing the shared transaction identity.
SHARED_EVENT_CONCEPT_ADDITIONS: dict[str, tuple[str, ...]] = {
    "title_family:ma": ("ma_transaction",),
}

DENIED_UNIT_ROLES: frozenset[str] = frozenset()

# Article structure is resolved before issuer semantics.  These patterns are
# format families, never source IDs or issuer-specific exceptions.
NON_TRIGGER_ARTICLE_ROLES: frozenset[str] = frozenset({
    "analyst_event",
    "editorial_analysis",
    "market_roundup",
    "mover_recap",
    "preview",
    "why_moving_followup",
    "automated_summary",
    "automated_market_statistics",
})

CONTEXT_ONLY_UNIT_ROLES_V9: frozenset[str] = frozenset({
    "ticker_market_observation",
    "editorial_reaction_explanation",
    "ticker_scoped_editorial_context",
    "ticker_scoped_analyst_context",
})

HIGH_VALUE_TRIGGER_CONCEPT_PREFIXES: tuple[str, ...] = (
    "capital_return",
    "clinical",
    "commercial",
    "contract",
    "credit_solvency",
    "earnings",
    "financing",
    "guidance",
    "legal",
    "listing_market_structure",
    "ma_transaction",
    "management_governance",
    "operations",
    "regulatory",
)

DIRECTION_RULE_WEIGHTS: dict[str, float] = {
    "accretive_transaction": 0.65,
    "analyst_negative": -2.0,
    "analyst_positive": 0.5,
    "bankruptcy": -1.5,
    "capital_return": 0.75,
    "clinical_failure": -1.0,
    "clinical_success": 0.45,
    "commercial_demand": 0.45,
    "commercial_launch": 0.45,
    "commercial_progress": 0.55,
    "contract_award": 0.75,
    "development_setback": -1.2,
    "dilutive_transaction": -0.65,
    "direct_earnings_beat": 1.0,
    "direct_earnings_miss": -0.6,
    "distribution_expansion": 0.4,
    "earnings_beat": 0.8,
    "earnings_miss": -1.0,
    "enrollment_progress": 0.55,
    "filing_delay": -0.75,
    "financing_dilutive": -0.6,
    "guidance_cut": -0.8,
    "guidance_raise": 0.8,
    "legal_negative": -0.8,
    "listing_negative": -1.0,
    "loss_pressure": -0.45,
    "ma_signed": 0.7,
    "material_weakness": -0.8,
    "operations_negative": -0.6,
    "partnership": 0.8,
    "patent_or_license_grant": 0.45,
    "positive_data": 0.6,
    "profit_positive": 0.7,
    "regulatory_approval": 1.35,
    "regulatory_progress": 1.5,
    "regulatory_setback": -1.35,
    "reported_loss": -0.35,
    "revenue_decline": -0.6,
    "revenue_growth": 0.55,
    "robust_guidance": 0.45,
    "strategic_alternatives": 0.45,
    "strong_results": 0.8,
    "weak_guidance": -0.6,
    "weak_results": -0.6,
}

# Issuer-role and transaction-state rules are evaluated only against the
# already issuer-scoped evidence. They correct the meaning of a transaction
# without leaking one participant's consequence to another participant.
@dataclass(frozen=True, slots=True)
class IssuerStateDirectionRule:
    rule_id: str
    roles: tuple[str, ...]
    patterns: tuple[str, ...]
    weight: float


ISSUER_STATE_DIRECTION_RULES: tuple[IssuerStateDirectionRule, ...] = (
    IssuerStateDirectionRule(
        rule_id="ma_withdrawal_acquirer",
        roles=("acquirer",),
        patterns=(
            r"\bno\s+longer\s+pursue\b.{0,100}\b(?:acquisition|merger|offer|bid)\b",
            r"\b(?:withdraw(?:s|n|al)?|withdrew)\b.{0,100}\b(?:acquisition|offer|bid|proposal)\b",
            r"\b(?:terminate[ds]?|abandon(?:s|ed)?)\b.{0,100}\b(?:acquisition|merger|offer|bid|proposal)\b",
        ),
        weight=-0.75,
    ),
    IssuerStateDirectionRule(
        rule_id="ma_replacement_proposal_target",
        roles=("target",),
        patterns=(
            r"\b(?:superior|higher|competing)\b.{0,80}\b(?:acquisition\s+)?(?:proposal|offer|bid)\b",
            r"\benter(?:s|ed|ing)?\b.{0,80}\b(?:acquisition\s+proposal|alternative\s+acquisition|competing\s+offer)\b",
        ),
        weight=0.85,
    ),
    IssuerStateDirectionRule(
        rule_id="termination_fee_payer",
        roles=("target",),
        patterns=(
            r"\b(?:required|agreed)\s+to\s+pay\b.{0,60}\btermination\s+fee\b",
            r"\bpay(?:s|ing)?\b.{0,40}\btermination\s+fee\b",
        ),
        weight=-0.25,
    ),
    IssuerStateDirectionRule(
        rule_id="termination_fee_recipient",
        roles=("acquirer",),
        patterns=(
            r"\btermination\s+fee\b.{0,60}\bto\b",
            r"\bpay\b.{0,60}\btermination\s+fee\b.{0,60}\bto\b",
        ),
        weight=0.25,
    ),
)

# A signed agreement is not an active positive state after the same scoped
# event explicitly says that the acquisition, merger, offer, or bid ended.
MA_INACTIVE_PATTERNS: tuple[str, ...] = (
    r"\bno\s+longer\s+pursue\b.{0,100}\b(?:acquisition|merger|offer|bid)\b",
    r"\b(?:withdraw(?:s|n|al)?|withdrew)\b.{0,100}\b(?:acquisition|offer|bid|proposal)\b",
    r"\b(?:terminate[ds]?|abandon(?:s|ed)?)\b.{0,100}\b(?:acquisition|merger|offer|bid|proposal)\b",
)

# Explicit action is required to keep a positive signed-deal signal when the
# same issuer evidence also discusses a withdrawn deal. A bare historical
# phrase such as ``under the merger agreement`` is not a new active signing.
MA_ACTIVE_SIGNING_PATTERNS: tuple[str, ...] = (
    r"\bagree(?:s|d)?\s+to\s+acquire\b",
    r"\b(?:enter(?:s|ed)?\s+into|sign(?:s|ed)?)\b.{0,80}\b(?:definitive\s+)?(?:merger|acquisition)\s+agreement\b",
)

DIRECTION_BASE_SCALE = 1.0
POSITIVE_THRESHOLD = 0.20
NEGATIVE_THRESHOLD = -0.35
MIXED_COMPONENT_THRESHOLD = 0.40
MIXED_DOMINANCE_MARGIN = 1.50
