from __future__ import annotations


DETERMINISTIC_V9_VERSION = "news_deterministic_v9_candidate_1"
CALIBRATION_VERSION = "news_deterministic_v9_calibration_1"
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

DENIED_UNIT_ROLES: frozenset[str] = frozenset()

ELIGIBILITY_TRUE_KEYS: frozenset[str] = frozenset({
    "regulatory_event|issuer_direct|primary_or_editorial_document|1",
    "primary_event|issuer_direct|primary_or_editorial_document|0",
    "editorial_analysis|editorial_original|primary_or_editorial_document|0",
    "primary_event|issuer_direct|primary_or_editorial_document|1",
    "primary_event|editorial_original|primary_or_editorial_document|0",
    "regulatory_event|regulatory_primary|primary_or_editorial_document|1",
    "regulatory_event|editorial_original|primary_or_editorial_document|1",
    "primary_event|issuer_direct|issuer_event_document|1",
    "primary_event|editorial_original|primary_or_editorial_document|1",
})

ELIGIBILITY_FALSE_KEYS: frozenset[str] = frozenset({
    "mover_recap|editorial_aggregation|ticker_market_observation|1",
    "mover_recap|editorial_aggregation|issuer_event_document|1",
    "mover_recap|editorial_aggregation|ticker_scoped_editorial_context|0",
    "mover_recap|editorial_aggregation|issuer_event_document|0",
    "preview|editorial_original|issuer_event_document|0",
    "mover_recap|editorial_aggregation|ticker_scoped_editorial_context|1",
    "mover_recap|editorial_aggregation|analyst_opinion|1",
    "regulatory_event|editorial_original|analyst_opinion|0",
    "mover_recap|editorial_aggregation|analyst_opinion|0",
    "editorial_analysis|editorial_original|analyst_opinion|0",
    "mover_recap|editorial_aggregation|ticker_market_observation|0",
    "market_roundup|editorial_aggregation|analyst_opinion|0",
    "analyst_event|analyst_research|analyst_opinion|0",
    "market_roundup|editorial_aggregation|analyst_opinion|1",
    "preview|editorial_original|analyst_opinion|1",
    "market_roundup|editorial_aggregation|ticker_market_observation|0",
    "market_roundup|editorial_aggregation|ticker_scoped_editorial_context|0",
    "analyst_event|analyst_research|analyst_opinion|1",
    "market_roundup|editorial_aggregation|ticker_scoped_editorial_context|1",
    "market_roundup|editorial_aggregation|ticker_market_observation|1",
    "analyst_event|analyst_research|ticker_scoped_editorial_context|1",
    "market_roundup|editorial_aggregation|issuer_event_document|0",
    "preview|editorial_original|issuer_event_document|1",
    "market_roundup|editorial_aggregation|issuer_event_document|1",
    "regulatory_event|issuer_direct|issuer_event_document|0",
    "analyst_event|analyst_research|issuer_event_document|0",
    "editorial_analysis|editorial_original|ticker_scoped_editorial_context|0",
    "analyst_event|analyst_research|primary_or_editorial_document|1",
    "why_moving_followup|editorial_original|analyst_opinion|1",
})

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

DIRECTION_BASE_SCALE = 1.0
POSITIVE_THRESHOLD = 0.20
NEGATIVE_THRESHOLD = -0.35
MIXED_COMPONENT_THRESHOLD = 0.40
MIXED_DOMINANCE_MARGIN = 1.50
