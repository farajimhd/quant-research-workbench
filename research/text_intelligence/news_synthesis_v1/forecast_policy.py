from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .reviewed_title_policy import ReviewedTitlePolicy


FORECAST_POLICY_VERSION = "news_forecast_event_policy_v4"


@dataclass(frozen=True, slots=True)
class ForecastPolicyDecision:
    label: str
    family: str
    evidence_mode: str
    rule_id: str
    reason_codes: tuple[str, ...]


_NEGATED_TRANSACTION_RE = re.compile(
    r"\b(?:no discussions?|not in discussions?|denies?|denied|rumou?r|reportedly|"
    r"considering|exploring|advanced talks?|in talks?)\b.{0,160}"
    r"\b(?:acquisition|acquire|merger|takeover|deal)\b|"
    r"\b(?:acquisition|acquire|merger|takeover|deal)\b.{0,160}"
    r"\b(?:denied|rumou?r|no discussions?|not in discussions?)\b",
    re.I,
)
_SPECULATIVE_TRANSACTION_RE = re.compile(
    r"\b(?:may|might|could|potential|possible)\b.{0,80}"
    r"\b(?:acquisition|acquire|merger|takeover|deal)\b",
    re.I,
)
_DIRECT_TRANSACTION_RE = re.compile(
    r"\b(?:enters? into|signs?|signed|definitive agreement|agree(?:s|d)? to|will acquire|"
    r"to acquire|will buy|to buy|set to buy|completes?|completed|closes?|closed)\b.{0,180}"
    r"\b(?:acquisition|acquire|merger|asset sale|business combination|transaction)\b|"
    r"\b(?:acquires?|merges? with|sells? (?:its )?.{0,80}(?:assets?|business))\b",
    re.I,
)
_TRANSACTION_TITLE_RE = re.compile(
    r"\b(?:acquisition|acquire[sd]?|acquiring|merger|(?<!account )takeover|buyout|"
    r"(?:will |to |set to )buy|business combination|"
    r"asset sale|sells? (?:its )?.{0,80}(?:assets?|business))\b",
    re.I,
)
_DIRECT_INVESTMENT_RE = re.compile(
    r"\b(?:invests?|invested|investment of|bets?)\b.{0,80}\$?\d",
    re.I,
)
_DIRECT_MATERIAL_TITLE_RE = re.compile(
    r"\b(?:wins?|won(?!['’]t)|awarded|secures?|secured|signs?|signed|enters?|entered|"
    r"partners?|partnered|partnership|teams? up|teamed up|collaborates?|collaborated|"
    r"contract|agreement|cybersecurity incident|cyberattack|data breach|ransomware|"
    r"defaults?|defaulted|solvency|bankruptcy)\b",
    re.I,
)
_DIRECT_MIXED_EVENT_RE = re.compile(
    r"\b(?:announces?|launches?|opens?|expands?|appoints?|names?|elects?|resigns?|"
    r"retires?|prices?|closes?|completes?|secures?|wins?|awarded|signs?|enters?|"
    r"issues?|authorizes?|declares?|reinstates?|regains?|receives?|increases?|raises?|"
    r"boosts?|reduces?|cuts?|renews?|extends?|redeems?|repurchases?|buys? back|invests?|"
    r"orders?|unveils?|reveals?|deploys?|builds?|creates?|forms?|teams? up|collaborates?|"
    r"licenses?|grants?|files?|registers?|submits?|commences?|begins?|starts?|terminates?|"
    r"suspends?|resumes?|divests?|sells?\b(?!\s+out)|purchases?|converts?|exchanges?|"
    r"announced|launched|opened|expanded|appointed|named|elected|resigned|retired|"
    r"priced|closed|completed|secured|won|signed|entered|issued|authorized|declared|"
    r"reinstated|regained|received|increased|raised|boosted|reduced|renewed|extended|"
    r"redeemed|repurchased|bought back|invested|ordered|unveiled|revealed|deployed|"
    r"built|created|formed|teamed up|collaborated|licensed|granted|filed|registered|"
    r"submitted|commenced|began|started|terminated|suspended|resumed|divested|sold\b(?!\s+out)|"
    r"purchased|converted|exchanged|launch)\b",
    re.I,
)
_DIRECT_GUIDANCE_RE = re.compile(
    r"\b(?:issues?|provides?|raises?|increases?|lowers?|cuts?|reduces?|reaffirms?|"
    r"reiterates?|maintains?|updates?|narrows?|widens?|expects?|forecasts?|projects?)\b"
    r".{0,140}\b(?:guidance|outlook|forecast|FY\s?\d{2,4}|fiscal[- ]year)\b|"
    r"\b(?:guidance|outlook|forecast)\b.{0,100}\b(?:raised|lowered|cut|reaffirmed|"
    r"reiterated|maintained|updated|narrowed|widened)\b",
    re.I,
)
_MATERIAL_OWNERSHIP_RE = re.compile(
    r"\b(?:Schedule\s+13D|Schedule\s+13G|beneficial ownership|beneficial owner|"
    r"activist investor|activist stake|\d+(?:\.\d+)?%\s+(?:ownership|stake))\b",
    re.I,
)

_CLINICAL = frozenset(("clinical.regulatory_milestone", "clinical.trial_result"))
_GUIDANCE = frozenset(("guidance.issued",))
_OWNERSHIP = frozenset(("ownership.position", "ownership.position_change"))
_EARNINGS = frozenset((
    "earnings.performance", "earnings.release_schedule", "earnings.restatement",
    "financial.cash_flow", "financial.credit_quality", "financial.interest_rate",
    "financial.internal_control", "financial.liquidity", "financial.loss_exposure",
    "financial.margin", "financial.operating_performance",
))
_LEGAL_REGULATORY = frozenset(("legal.proceeding", "regulatory.action"))
_CONTEXT_ONLY = frozenset((
    "analyst.issuer_assessment", "analyst.price_target_action", "analyst.rating_action",
    "analyst.short_thesis", "estimate.revision", "strategy.valuation_assessment",
    "market.context", "market.currency_move_observed", "market.money_flow_observed",
    "market.options_activity", "market.price_move_observed", "market.short_interest_observed",
    "market.technical_analysis", "market.trading_status", "market.volume_move_observed",
    "macro.economic_outlook", "macro.employment", "macro.inflation", "macro.policy_outlook",
    "commodity.inventory", "commercial.competitive_position", "commercial.demand_condition",
))
_DIRECT_ELIGIBLE = frozenset((
    "commercial.contract", "commercial.partnership", "credit.solvency",
    "technology.cybersecurity_incident",
))
_MIXED_DIRECT = frozenset((
    "capital.deleveraging", "capital.financing", "capital.return", "capital.structure",
    "corporate_transaction.asset_sale", "governance.auditor_change",
    "governance.management_change", "governance.shareholder_vote", "index.membership",
    "listing.market_structure", "operations.business_update", "operations.capacity_change",
    "operations.cost_efficiency", "operations.workforce", "product.milestone",
    "strategy.operational_priority", "strategy.strategic_alternatives",
))


def _decision(
    label: str,
    family: str,
    evidence_mode: str,
    *reasons: str,
) -> ForecastPolicyDecision:
    return ForecastPolicyDecision(
        label=label,
        family=family,
        evidence_mode=evidence_mode,
        rule_id=f"{FORECAST_POLICY_VERSION}:{family}",
        reason_codes=tuple(reasons),
    )


def resolve_forecast_policy(
    *,
    title: str,
    text: str,
    tickers: Sequence[str],
    envelope: Mapping[str, Any],
    statements: Iterable[Mapping[str, Any]],
    reviewed_title_policy: ReviewedTitlePolicy | None,
    provider_context: Mapping[str, Any],
    earnings_call_material: bool,
) -> ForecastPolicyDecision:
    """Resolve title, metadata, and semantic event policy with explicit precedence."""
    concepts = frozenset(str(row.get("concept_leaf") or "") for row in statements)
    structure = str(envelope["document_structure"]["value"])
    purpose = str(envelope["communication_purpose"]["value"])
    origin = str(envelope["information_origin"]["value"])
    normalized_title = " ".join(str(title or "").split())

    if earnings_call_material:
        return _decision("eligible", "earnings_call_transcript", "source", "approved_transcript_policy")
    if reviewed_title_policy and reviewed_title_policy.family == "live_broadcast":
        return _decision("eligible", "live_broadcast", "source", "approved_live_broadcast_policy")
    if reviewed_title_policy and reviewed_title_policy.family == "single_issuer_clinical_conference":
        return _decision(
            "eligible", "single_issuer_clinical_conference", "source",
            "approved_clinical_conference_policy",
        )
    if reviewed_title_policy and reviewed_title_policy.family == "material_ownership":
        return _decision(
            "eligible", "material_ownership", "current_event",
            "approved_material_ownership_policy",
        )
    if reviewed_title_policy and reviewed_title_policy.family == "issuer_guidance":
        return _decision(
            "eligible", "issuer_guidance", "current_event",
            "approved_direct_issuer_guidance_policy",
        )
    if reviewed_title_policy and reviewed_title_policy.label == "ineligible":
        return _decision(
            "ineligible", reviewed_title_policy.family, "none", "approved_title_policy"
        )
    if str(provider_context.get("route") or "") == "context_only":
        return _decision(
            "ineligible", f"provider_context:{provider_context.get('content_family')}", "none",
            "validated_provider_metadata_policy",
        )
    if structure != "single_subject":
        return _decision(
            "ineligible", "multi_subject_or_reference_document", "none",
            "approved_multi_subject_policy",
        )
    if purpose in {"analyze", "explain_move", "preview", "recap"}:
        return _decision(
            "ineligible", f"communication_purpose:{purpose}", "none",
            "contextual_communication_purpose",
        )
    if origin == "analyst":
        return _decision("ineligible", "analyst_origin", "none", "approved_analyst_policy")

    if concepts & _CLINICAL:
        return _decision("eligible", "clinical_event", "current_event", "approved_clinical_event_policy")
    if concepts & _GUIDANCE and _DIRECT_GUIDANCE_RE.search(normalized_title):
        return _decision("eligible", "issuer_guidance", "current_event", "approved_guidance_policy")
    if "corporate_transaction.acquisition" in concepts and _TRANSACTION_TITLE_RE.search(normalized_title):
        if _NEGATED_TRANSACTION_RE.search(normalized_title):
            return _decision(
                "ineligible", "speculative_or_negated_transaction", "none",
                "transaction_not_definitive",
            )
        if _DIRECT_TRANSACTION_RE.search(normalized_title):
            return _decision(
                "eligible", "definitive_transaction", "current_event",
                "direct_definitive_transaction",
            )
        if _SPECULATIVE_TRANSACTION_RE.search(normalized_title):
            return _decision(
                "ineligible", "speculative_or_negated_transaction", "none",
                "transaction_not_definitive",
            )
        return _decision(
            "ineligible", "mixed_transaction_unconfirmed", "none",
            "mixed_event_fail_closed",
        )
    if (
        "corporate_transaction.acquisition" in concepts
        and _DIRECT_INVESTMENT_RE.search(normalized_title)
    ):
        return _decision(
            "eligible", "direct_investment_event", "current_event",
            "direct_investment_title_evidence",
        )
    if concepts & _DIRECT_ELIGIBLE and _DIRECT_MATERIAL_TITLE_RE.search(normalized_title):
        return _decision(
            "eligible", "direct_material_issuer_event", "current_event",
            "material_event_concept_with_direct_title_evidence",
        )
    if concepts & _MIXED_DIRECT:
        if _DIRECT_MIXED_EVENT_RE.search(normalized_title):
            return _decision(
                "eligible", "direct_mixed_family_event", "current_event",
                "direct_issuer_action_required_for_mixed_family",
            )
        return _decision(
            "ineligible", "mixed_family_without_direct_action", "none",
            "mixed_event_fail_closed",
        )
    if concepts & _GUIDANCE:
        if concepts & _EARNINGS:
            return _decision(
                "ineligible", "guidance_with_earnings_results", "none",
                "reviewed_guidance_earnings_precedence",
            )
        return _decision(
            "ineligible", "guidance_without_direct_title_evidence", "none",
            "guidance_event_fail_closed",
        )
    if concepts & _OWNERSHIP:
        if _MATERIAL_OWNERSHIP_RE.search(normalized_title):
            return _decision(
                "eligible", "material_ownership", "current_event",
                "approved_material_ownership_policy",
            )
        return _decision(
            "ineligible", "routine_or_unconfirmed_ownership", "none",
            "routine_holdings_fail_closed",
        )
    if concepts & _EARNINGS:
        return _decision("ineligible", "earnings_results", "none", "approved_earnings_results_policy")
    if concepts & _LEGAL_REGULATORY:
        return _decision(
            "ineligible", "legal_or_regulatory_action", "none",
            "approved_legal_regulatory_policy",
        )
    if concepts & _CONTEXT_ONLY:
        return _decision("ineligible", "context_or_analysis", "none", "approved_context_policy")
    return _decision(
        "ineligible", "unclassified_or_nonmaterial", "none",
        "no_approved_material_event_policy",
    )
