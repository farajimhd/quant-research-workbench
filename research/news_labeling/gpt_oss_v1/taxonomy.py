from __future__ import annotations

from dataclasses import dataclass


LABEL_VERSION = "news_semantic_labels_gpt_oss_v1"
PROMPT_VERSION = "news_semantic_prompt_gpt_oss_v1"

ORIGINS = (
    "issuer_direct",
    "regulatory_primary",
    "analyst_research",
    "editorial_original",
    "editorial_aggregation",
    "automated_summary",
    "market_notice",
    "unknown",
)
CONTENT_ROLES = (
    "primary_event",
    "follow_up",
    "preview",
    "recap",
    "reaction_commentary",
    "roundup_listicle",
    "correction_update",
    "administrative",
    "unknown",
)
ISSUER_RELATIONSHIPS = (
    "direct_announcement",
    "reported_issuer_event",
    "third_party_about_issuer",
    "analyst_opinion",
    "market_reaction_story",
    "sector_macro_context",
    "unrelated_or_ambiguous",
)
NOVELTY = ("new_event", "material_update", "repeat", "recap", "preview", "unknown")
IMPACT_HORIZONS = ("immediate", "near_term", "long_term", "mixed", "unknown")
SENTIMENT_LABELS = ("negative", "neutral", "positive", "mixed", "not_applicable")
OVERALL_SENTIMENT = ("negative", "neutral", "positive", "mixed")
DIRECTIONS = ("negative", "neutral", "positive", "mixed")
TIME_ORIENTATIONS = ("historical", "current", "forward", "structural", "mixed")
MODALITIES = ("confirmed", "planned", "proposed", "rumored", "estimated", "opinion", "mixed")
QUALITY_FLAGS = (
    "insufficient_text",
    "contradictory_claims",
    "boilerplate_dominant",
    "table_parse_issue",
    "possible_duplicate",
    "ambiguous_subject",
    "unsupported_language",
)

SENTIMENT_DIMENSIONS = (
    "historical_performance",
    "forward_outlook",
    "commercial_demand",
    "operations_execution",
    "balance_sheet_liquidity",
    "capital_dilution",
    "regulatory_legal",
    "management_governance",
    "reported_market_reaction",
)


@dataclass(frozen=True, slots=True)
class EventFamily:
    code: str
    label: str
    subtypes: tuple[str, ...]


# This catalog is deliberately versioned and extensible. "other" prevents
# unsupported concepts from being silently forced into a plausible category.
EVENT_FAMILIES = (
    EventFamily("earnings", "Earnings and reported performance", (
        "eps", "revenue", "margin", "cash_flow", "unit_metric", "estimate_comparison", "restatement",
    )),
    EventFamily("guidance", "Guidance and outlook", (
        "raise", "cut", "reaffirm", "withdraw", "initiate", "profit_warning",
    )),
    EventFamily("capital_return", "Capital return", ("dividend", "buyback")),
    EventFamily("financing", "Financing and capital raising", (
        "public_offering", "registered_direct", "atm", "private_placement", "debt", "refinancing",
        "warrant", "liquidity",
    )),
    EventFamily("capital_structure", "Capital structure", (
        "stock_split", "reverse_split", "share_authorization", "conversion", "warrant_exercise",
    )),
    EventFamily("ma_transaction", "Mergers and strategic transactions", (
        "acquisition", "merger", "takeover_offer", "divestiture", "asset_sale", "joint_venture",
        "termination", "regulatory_clearance",
    )),
    EventFamily("contract_order", "Contracts, orders, customers, and partnerships", (
        "award", "renewal", "cancellation", "backlog", "customer_win", "partnership",
    )),
    EventFamily("product_commercial", "Product and commercial events", (
        "launch", "approval", "pricing", "adoption", "delay", "recall", "discontinuation",
    )),
    EventFamily("clinical", "Clinical and biotechnology", (
        "trial_result", "endpoint", "enrollment", "safety", "clinical_hold", "designation",
    )),
    EventFamily("regulatory", "Regulatory decisions and compliance", (
        "approval", "clearance", "rejection", "filing", "enforcement", "investigation", "compliance",
    )),
    EventFamily("legal", "Legal and litigation", (
        "lawsuit", "settlement", "dismissal", "subpoena", "charge", "sanction",
    )),
    EventFamily("management_governance", "Management and governance", (
        "appointment", "resignation", "board_change", "compensation", "control_change",
    )),
    EventFamily("operations", "Operations and workforce", (
        "expansion", "shutdown", "disruption", "restructuring", "layoff", "capacity",
    )),
    EventFamily("credit_solvency", "Credit, liquidity, and solvency", (
        "upgrade", "downgrade", "default", "bankruptcy", "going_concern",
    )),
    EventFamily("analyst_action", "Analyst action", (
        "upgrade", "downgrade", "initiation", "reiteration", "price_target_raise", "price_target_cut",
    )),
    EventFamily("ownership", "Insider and institutional ownership", (
        "insider_buy", "insider_sell", "stake_increase", "stake_decrease", "activist",
    )),
    EventFamily("accounting_audit", "Accounting and audit", (
        "audit_opinion", "restatement", "internal_controls", "accounting_change",
    )),
    EventFamily("listing_market_structure", "Listing and market structure", (
        "listing", "delisting", "compliance_notice", "halt", "resume",
    )),
    EventFamily("cybersecurity_privacy", "Cybersecurity, data, and privacy", (
        "breach", "attack", "outage", "privacy_action", "remediation",
    )),
    EventFamily("intellectual_property", "Intellectual property", (
        "patent_grant", "patent_dispute", "license", "royalty",
    )),
    EventFamily("macro_sector", "Macro, policy, geopolitical, and sector", (
        "rates", "inflation", "employment", "government_policy", "geopolitical", "commodity", "sector",
    )),
    EventFamily("market_activity", "Reported market activity", (
        "price_move", "volume", "options", "short_interest", "technical", "volatility",
    )),
    EventFamily("media_corporate", "Media and ordinary corporate activity", (
        "conference", "presentation", "award", "interview", "brand_campaign",
    )),
    EventFamily("other", "Other or unsupported event", ("other",)),
)

EVENT_FAMILY_CODES = tuple(item.code for item in EVENT_FAMILIES)
EVENT_SUBTYPES = {item.code: item.subtypes for item in EVENT_FAMILIES}


def taxonomy_summary() -> dict[str, object]:
    return {
        "label_version": LABEL_VERSION,
        "origins": ORIGINS,
        "content_roles": CONTENT_ROLES,
        "issuer_relationships": ISSUER_RELATIONSHIPS,
        "novelty": NOVELTY,
        "impact_horizons": IMPACT_HORIZONS,
        "sentiment_labels": SENTIMENT_LABELS,
        "sentiment_dimensions": SENTIMENT_DIMENSIONS,
        "directions": DIRECTIONS,
        "time_orientations": TIME_ORIENTATIONS,
        "modalities": MODALITIES,
        "quality_flags": QUALITY_FLAGS,
        "event_families": {
            item.code: {"label": item.label, "subtypes": item.subtypes}
            for item in EVENT_FAMILIES
        },
    }
