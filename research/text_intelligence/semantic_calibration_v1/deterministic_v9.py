from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from pipelines.news.benzinga.core.content_quality import sanitize_packed_news_text
from research.text_intelligence.scoped_labeling_v1.news_identity import (
    ANNOUNCED_TICKER_RE,
    NewsIssuerResolver,
)
from research.text_intelligence.scoped_labeling_v1.pipeline import classify_news_document
from research.text_intelligence.scoped_labeling_v1.schema import NEWS_EXTRACTOR_VERSION
from research.text_intelligence.semantic_label_authority_v1.schema import SemanticDocument

from .deterministic_v6 import _deduplicate_labels
from .deterministic_v6_config import DIRECTION_RULES
from .deterministic_v7 import _extraction_decision
from .deterministic_v8 import (
    _classify_origin_v8,
    _classify_role_v8,
    _direction_v8,
    _retain_unit_v8,
)
from .deterministic_v8_config import DIRECTION_RULES_V8
from .deterministic_v9_config import (
    ARTICLE_ROLE_OVERRIDES,
    CALIBRATION_VERSION,
    CONTEXT_ONLY_UNIT_ROLES_V9,
    DENIED_UNIT_ROLES,
    DETERMINISTIC_V9_VERSION,
    DIRECTION_BASE_SCALE,
    DIRECTION_RULE_WEIGHTS,
    HIGH_VALUE_TRIGGER_CONCEPT_PREFIXES,
    MIXED_COMPONENT_THRESHOLD,
    MIXED_DOMINANCE_MARGIN,
    ISSUER_STATE_DIRECTION_RULES,
    MA_ACTIVE_SIGNING_PATTERNS,
    MA_INACTIVE_PATTERNS,
    NEGATIVE_THRESHOLD,
    NON_TRIGGER_ARTICLE_ROLES,
    POSITIVE_THRESHOLD,
    SINGLE_TICKER_CONCEPT_ADDITIONS,
    SHARED_EVENT_CONCEPT_ADDITIONS,
    SOURCE_ORIGIN_OVERRIDES,
)
from .deterministic_v9_signals import article_signals_from_parts


_DEFAULT_DIRECTION_WEIGHTS = {
    rule.rule_id: float(rule.weight) for rule in (*DIRECTION_RULES, *DIRECTION_RULES_V8)
}


@dataclass(frozen=True, slots=True)
class DeterministicNewsResultV9:
    version: str
    calibration_version: str
    extraction_decision: str
    content_role: str
    source_origin: str
    labels: tuple[dict[str, Any], ...]
    evidence: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "calibration_version": self.calibration_version,
            "scope_extractor_version": NEWS_EXTRACTOR_VERSION,
            "extraction_decision": self.extraction_decision,
            "content_role": self.content_role,
            "source_origin": self.source_origin,
            "labels": list(self.labels),
            "evidence": list(self.evidence),
        }


def classify_news_document_v9(
    document: SemanticDocument,
    *,
    issuer_resolver: NewsIssuerResolver | None = None,
) -> DeterministicNewsResultV9:
    """Classify one article through ordered deterministic V9 authorities.

    Article structure and provenance are fixed before issuer-unit semantics.
    Provider links remain identity candidates; issuer direction and eligibility
    are then composed only from each unit's scoped evidence and event role.
    """
    sanitized_text, _rejected_sources = sanitize_packed_news_text(document.text)
    if sanitized_text != document.text:
        document = replace(document, text=sanitized_text)
    raw_labels = classify_news_document(document, issuer_resolver=issuer_resolver)
    base_role, base_role_rule = _classify_role_v8(document, raw_labels)
    base_origin, base_origin_rule = _classify_origin_v8(
        document, base_role, raw_labels
    )
    signals = article_signals_from_parts(
        title=document.title,
        provider_tickers=document.tickers,
        provider_tags=document.metadata.get("provider_tags") or (),
        channels=document.metadata.get("channels") or (),
        evidence=(f"role:{base_role_rule}", f"origin:{base_origin_rule}"),
    )
    role, role_signal = _classify_article_role_v9(
        document, base_role=base_role, signals=signals
    )
    origin, origin_signal = _classify_source_origin_v9(
        document, role=role, base_origin=base_origin, signals=signals
    )
    concept_additions = set()
    if len(tuple(value for value in document.tickers if value)) == 1:
        for signal in signals:
            concept_additions.update(SINGLE_TICKER_CONCEPT_ADDITIONS.get(signal, ()))

    labels: list[dict[str, Any]] = []
    provider_tickers = {
        _normalize_provider_ticker(value) for value in document.tickers if value
    }
    provider_tickers.update(
        match.group("ticker").upper()
        for match in ANNOUNCED_TICKER_RE.finditer(f"{document.title}\n{document.text}")
    )
    for raw_source in raw_labels:
        source = raw_source.as_dict()
        if str(source.get("unit_role") or "") in DENIED_UNIT_ROLES:
            continue
        if not _retain_unit_v8(source, provider_tickers=provider_tickers):
            continue
        label = dict(source)
        classification = dict(label.get("classification") or {})
        evidence_text = str(label.get("semantic_evidence_text") or "")
        v8_direction = _direction_v8(evidence_text, classification)
        concepts = set(classification.get("event_concepts") or ())
        concepts.update(v8_direction["concept_families"])
        classification.update({
            "semantic_score_raw": v8_direction["raw_score"],
            "deterministic_direction_evidence": v8_direction["matched_rules"],
        })
        concepts = _refine_event_concepts(
            concepts,
            evidence_text=evidence_text,
            issuer_role=str(label.get("issuer_role") or ""),
        )
        direction = _recalibrate_direction(
            classification,
            issuer_role=str(label.get("issuer_role") or ""),
            evidence_text=evidence_text,
        )
        direction = _compose_direction_v9(
            direction,
            evidence_text=evidence_text,
            issuer_role=str(label.get("issuer_role") or ""),
            concepts=concepts,
        )
        concepts.update(concept_additions)
        if str(label.get("evidence_scope") or "") == "shared_relational":
            for signal in signals:
                concepts.update(SHARED_EVENT_CONCEPT_ADDITIONS.get(signal, ()))
        classification.update({
            "content_role": role,
            "source_origin": origin,
            "event_concepts": sorted(concepts),
            "semantic_direction": direction["direction"],
            "semantic_score": direction["normalized_score"],
            "semantic_score_raw": direction["raw_score"],
            "direction_confidence": direction["confidence"],
            "deterministic_direction_evidence": direction["matched_rules"],
            "quality_flags": list(dict.fromkeys((
                *(classification.get("quality_flags") or ()),
                "deterministic_v9_teacher_calibrated_rule_only",
            ))),
        })
        forecast_eligible, reaction_eligible, eligibility_basis = _eligibility_v9(
            document=document,
            role=role,
            origin=origin,
            label=label,
            concepts=concepts,
            direction=direction["direction"],
            evidence_text=evidence_text,
            issuer_resolver=issuer_resolver,
        )
        classification["eligibility_basis"] = eligibility_basis
        label.update({
            "classification": classification,
            "forecast_trigger_eligible": forecast_eligible,
            "reaction_evaluation_eligible": reaction_eligible,
            "issuer_history_context_eligible": bool(label.get("ticker")),
        })
        labels.append(label)
    labels = _deduplicate_labels(labels)
    decision = _extraction_decision(document, role, labels)
    return DeterministicNewsResultV9(
        version=DETERMINISTIC_V9_VERSION,
        calibration_version=CALIBRATION_VERSION,
        extraction_decision=decision,
        content_role=role,
        source_origin=origin,
        labels=tuple(labels),
        evidence=tuple(filter(None, (
            f"role:{base_role_rule}",
            f"origin:{base_origin_rule}",
            f"v9_role:{role_signal}" if role_signal else "",
            f"v9_origin:{origin_signal}" if origin_signal else "",
        ))),
    )


def _override(current: str, signals: tuple[str, ...], table: dict[str, str]) -> tuple[str, str]:
    for signal, value in table.items():
        if signal in signals:
            return value, signal
    return current, ""


_MOVER_ARTICLE_RE = re.compile(
    r"\b(?:\d+\s+)?stocks?\s+moving\b|\b(?:biggest|top)\s+(?:stock\s+)?"
    r"(?:movers|gainers|losers)\b|\b(?:pre[- ]?market|after[- ]?hours|mid[- ]?day)"
    r"\s+(?:session\s+)?(?:movers|gainers|losers)\b",
    re.I,
)
_ROUNDUP_ARTICLE_RE = re.compile(
    r"\bmarket\s+(?:wrap|update|today|recap)\b|\bstocks?\s+to\s+watch\b|"
    r"\bdaily\s+(?:biotech\s+)?pulse\b|\bweekend\s+m\s*&\s*a\s+chatter\b|"
    r"\bmovers?\s*&\s*shakers?\b|\ba\s+peek\s+into\s+the\s+markets\b",
    re.I,
)
_PREVIEW_ARTICLE_RE = re.compile(
    r"\b(?:earnings|results?)\s+preview\b|\bwhat\s+to\s+expect\b|"
    r"\bahead\s+of\s+(?:its\s+)?(?:earnings|results?)\b",
    re.I,
)
_WHY_MOVING_RE = re.compile(r"\bwhy\s+(?:is|are|did)\b.{0,80}\bmoving\b", re.I)
_ANALYST_BLOG_RE = re.compile(r"(?:^|[-:|]\s*)analyst\s+blog\s*$", re.I)
_EXPLICIT_ANALYST_ACTION_RE = re.compile(
    r"\b(?:analyst|securities|capital|research)\b.{0,100}"
    r"\b(?:upgrade[sd]?|downgrade[sd]?|maintain(?:s|ed)?|reiterate[sd]?|"
    r"initiate[sd]?|resume[sd]?|price\s+target)\b|"
    r"\b(?:upgrade[sd]?|downgrade[sd]?|maintain(?:s|ed)?|reiterate[sd]?|"
    r"initiate[sd]?|resume[sd]?)\b.{0,100}"
    r"\b(?:buy|sell|hold|overweight|underweight|outperform|underperform|neutral)\b|"
    r"\b(?:raises?|lowers?|cuts?)\s+(?:the\s+)?price\s+target\b|"
    r"\bsays?\s+(?:this\s+)?analyst\b",
    re.I | re.S,
)
_REGULATORY_CURRENT_RE = re.compile(
    r"\b(?:fda|sec|nasdaq|nyse)\b.{0,100}\b(?:approv|clear|subpoena|halt|"
    r"noncompliance|fil(?:e|ing)|registration|investigat)|"
    r"\b(?:form\s+8-k|form\s+4|regulatory\s+approval|clinical\s+hold)\b",
    re.I | re.S,
)
_ISSUER_DIRECT_CHANNELS = {"press releases", "press release", "company news"}


def _classify_article_role_v9(
    document: SemanticDocument,
    *,
    base_role: str,
    signals: tuple[str, ...],
) -> tuple[str, str]:
    """Resolve document structure before interpreting issuer evidence."""
    title = document.title or ""
    if _MOVER_ARTICLE_RE.search(title):
        return "mover_recap", "structural_mover_title"
    if _ROUNDUP_ARTICLE_RE.search(title):
        return "market_roundup", "structural_roundup_title"
    if _WHY_MOVING_RE.search(title):
        return "why_moving_followup", "structural_why_moving_title"
    if _PREVIEW_ARTICLE_RE.search(title):
        return "preview", "structural_preview_title"
    if _ANALYST_BLOG_RE.search(title):
        return "editorial_analysis", "structural_analyst_blog_title"
    if _EXPLICIT_ANALYST_ACTION_RE.search(title):
        return "analyst_event", "explicit_analyst_action_title"
    if _REGULATORY_CURRENT_RE.search(title):
        return "regulatory_event", "explicit_current_regulatory_title"
    return _override(base_role, signals, ARTICLE_ROLE_OVERRIDES)


def _classify_source_origin_v9(
    document: SemanticDocument,
    *,
    role: str,
    base_origin: str,
    signals: tuple[str, ...],
) -> tuple[str, str]:
    channels = {
        str(value).casefold() for value in document.metadata.get("channels") or ()
    }
    if channels & _ISSUER_DIRECT_CHANNELS:
        return "issuer_direct", "issuer_distribution_channel"
    if role == "analyst_event":
        return "analyst_research", "analyst_role"
    if _ANALYST_BLOG_RE.search(document.title or ""):
        return "editorial_original", "syndicated_analyst_blog"
    if role in {"market_roundup", "mover_recap"}:
        return "editorial_aggregation", "aggregation_role"
    if role == "regulatory_event" and _REGULATORY_CURRENT_RE.search(document.title):
        return "regulatory_primary", "regulatory_primary_title"
    return _override(base_origin, signals, SOURCE_ORIGIN_OVERRIDES)


def _normalize_provider_ticker(value: str) -> str:
    raw = str(value or "").upper().strip()
    if ":" in raw:
        exchange, _, ticker = raw.partition(":")
        if exchange in {"NASDAQ", "NYSE", "AMEX", "OTC", "OTCQX", "OTCQB", "TSX", "TSXV", "CSE"}:
            return ticker
    return raw


def _refine_event_concepts(
    concepts: set[str],
    *,
    evidence_text: str,
    issuer_role: str,
) -> set[str]:
    """Apply event-local state and instrument semantics before direction."""
    text = evidence_text
    output = set(concepts)
    if re.search(r"\b(?:initial\s+public\s+offering|ipo)\b", text, re.I):
        output.difference_update({"financing.public_offering", "financing"})
        output.add("listing_market_structure.ipo")
    if _PRIMARY_ENDPOINT_FAILURE_RE.search(text):
        output = {value for value in output if "success" not in value and "positive_data" not in value}
        output.add("clinical.failure")
    if re.search(r"\b(?:plan\s+to\s+exit|emerg(?:e|ed|ing)\s+from|exit)\b.{0,80}\b(?:chapter\s+11|bankruptcy)\b|\b(?:court\s+)?approved\b.{0,100}\b(?:reorganization|bankruptcy)\s+plan\b", text, re.I | re.S):
        output.discard("credit_solvency.bankruptcy")
        output.add("credit_solvency.reorganization_approved")
    if re.search(r"\b(?:board|director|officer|chief\s+\w+\s+officer)\b.{0,100}\b(?:appoint|elect|name[sd]?|hire[sd]?)\b", text, re.I | re.S):
        output.add("management_governance.appointment")
    if issuer_role in {"lender", "financing_provider"}:
        output.discard("financing.dilutive")
    return output


def _compose_direction_v9(
    direction: dict[str, Any],
    *,
    evidence_text: str,
    issuer_role: str,
    concepts: set[str],
) -> dict[str, Any]:
    """Apply deterministic precedence after scoped component scoring."""
    text = evidence_text
    forced = ""
    basis = ""
    # Explicit analyst state change is the semantic action; surrounding thesis
    # language cannot reverse it.
    if re.search(r"\b(?:downgrade[sd]?|lowers?|cuts?)\b.{0,90}\b(?:rating|price\s+target|to\s+(?:hold|sell|underperform|underweight))\b", text, re.I | re.S):
        forced, basis = "negative", "explicit_analyst_negative_action"
    elif re.search(r"\b(?:upgrade[sd]?|raises?)\b.{0,90}\b(?:rating|price\s+target|to\s+(?:buy|outperform|overweight))\b", text, re.I | re.S):
        forced, basis = "positive", "explicit_analyst_positive_action"
    if _PRIMARY_ENDPOINT_FAILURE_RE.search(text):
        forced, basis = "negative", "primary_endpoint_failure_precedence"
    if re.search(r"\b(?:guidance|outlook|forecast)\b.{0,120}\b(?:below|lower|cut|miss(?:es|ed)?)\b.{0,80}\b(?:estimate|consensus|prior|expectation)?", text, re.I | re.S):
        forced, basis = "negative", "forward_guidance_negative_precedence"
    if re.search(r"\b(?:guidance|outlook|forecast)\b.{0,120}\b(?:above|raise[sd]?|strong|better)\b.{0,80}\b(?:estimate|consensus|prior|expectation)?", text, re.I | re.S):
        forced, basis = "positive", "forward_guidance_positive_precedence"
    if "credit_solvency.reorganization_approved" in concepts:
        forced, basis = "positive", "bankruptcy_exit_state"
    if re.search(r"\b(?:board|director|officer|chief\s+\w+\s+officer)\b.{0,100}\b(?:appoint|elect|name[sd]?|hire[sd]?)\b", text, re.I | re.S):
        forced, basis = "neutral", "ordinary_management_appointment"
    if re.search(r"\b(?:reverse\s+stock\s+split|1-for-\d+\s+(?:reverse\s+)?split)\b", text, re.I):
        forced, basis = "negative", "reverse_split_state"
    if (
        issuer_role not in {"counterparty", "lender", "financing_provider"}
        and re.search(
            r"\b(?:public\s+offering|registered\s+direct|private\s+placement|"
            r"convertible\s+(?:note|debt)|preferred\s+stock|at-the-market|"
            r"issue|issuance|offering)\b.{0,140}\b(?:share|stock|warrant|note|debt|security|securities)\b|"
            r"\b(?:share|stock|warrant|note|debt|security|securities)\b.{0,140}"
            r"\b(?:public\s+offering|registered\s+direct|private\s+placement|issued?|offering)\b",
            text,
            re.I | re.S,
        )
    ):
        forced, basis = "negative", "issuer_financing_supply"
    if issuer_role == "plaintiff" and re.search(r"\b(?:sue[sd]?|lawsuit|patent\s+infringement)\b", text, re.I):
        forced, basis = "neutral", "plaintiff_legal_role"
    if not forced:
        return direction
    result = dict(direction)
    result["direction"] = forced
    result["matched_rules"] = [*direction["matched_rules"], f"precedence:{basis}"]
    # Preserve signed evidence magnitude for audit; a neutral precedence has no
    # directional normalized score.
    if forced == "neutral":
        result.update(raw_score=0.0, normalized_score=0.0)
    elif forced == "positive" and result["raw_score"] <= 0:
        result.update(raw_score=0.5, normalized_score=0.125)
    elif forced == "negative" and result["raw_score"] >= 0:
        result.update(raw_score=-0.5, normalized_score=-0.125)
    return result


_PRIMARY_ENDPOINT_FAILURE_RE = re.compile(
    r"(?:\bprimary\s+endpoint\b.{0,140}\b(?:failed|missed|was\s+not\s+met)\b|"
    r"\b(?:did\s+not|failed\s+to)\s+(?:meet|achieve)\b.{0,60}\bprimary\s+endpoint\b)",
    re.I | re.S,
)


def _eligibility_v9(
    *,
    document: SemanticDocument,
    role: str,
    origin: str,
    label: dict[str, Any],
    concepts: set[str],
    direction: str,
    evidence_text: str,
    issuer_resolver: NewsIssuerResolver | None,
) -> tuple[bool, bool, tuple[str, ...]]:
    reasons: list[str] = []
    unit_role = str(label.get("unit_role") or "")
    current = unit_role not in CONTEXT_ONLY_UNIT_ROLES_V9
    speculative = bool(re.search(
        r"\b(?:rumou?r(?:ed)?|reportedly|in\s+talks|unconfirmed|explor(?:e|ing)\s+(?:a\s+)?potential)\b",
        evidence_text,
        re.I,
    ))
    high_value = any(
        concept == prefix or concept.startswith(prefix + ".")
        for concept in concepts
        for prefix in HIGH_VALUE_TRIGGER_CONCEPT_PREFIXES
    )
    tradable = True
    ticker = str(label.get("ticker") or "")
    announced = {
        match.group("ticker").upper()
        for match in ANNOUNCED_TICKER_RE.finditer(f"{document.title}\n{document.text}")
    }
    if ticker.upper() in announced and re.search(
        r"\b(?:initial\s+public\s+offering|IPO|registration\s+statement)\b",
        evidence_text,
        re.I,
    ):
        # Absence from the identity graph is not evidence of non-tradability in
        # general, but an explicitly announced pre-listing symbol is.
        snapshot = issuer_resolver.reference_snapshot((ticker,), timestamp=document.timestamp) if issuer_resolver else ()
        tradable = bool(snapshot)
    if role in NON_TRIGGER_ARTICLE_ROLES:
        reasons.append(f"non_trigger_article_role:{role}")
    if unit_role in CONTEXT_ONLY_UNIT_ROLES_V9:
        reasons.append(f"context_only_unit_role:{unit_role}")
    if not current:
        reasons.append("historical_or_retrospective_evidence")
    if speculative:
        reasons.append("speculative_or_unconfirmed_event")
    if not high_value:
        reasons.append("no_high_value_current_event")
    if not tradable:
        reasons.append("not_point_in_time_tradable")
    forecast = not reasons and direction in {"positive", "negative", "mixed"}
    if direction == "neutral":
        reasons.append("no_directional_semantic_edge")
    # Reaction-study membership is stricter: it requires a confirmed current
    # event and is intentionally independent from the forecast decision.
    reaction = forecast and not speculative and current
    return forecast, reaction, tuple(reasons or ("current_supported_tradable_event",))


def _recalibrate_direction(
    classification: dict[str, Any],
    *,
    issuer_role: str = "",
    evidence_text: str = "",
) -> dict[str, Any]:
    matched_values = list(classification.get("deterministic_direction_evidence") or ())
    matched_ids = [str(value).split(":", 1)[0] for value in matched_values]
    v8_added = sum(_DEFAULT_DIRECTION_WEIGHTS.get(rule_id, 0.0) for rule_id in matched_ids)
    base = float(classification.get("semantic_score_raw") or 0.0) - v8_added
    raw = DIRECTION_BASE_SCALE * base
    positive = max(raw, 0.0)
    negative = max(-raw, 0.0)
    evidence = []
    inactive_ma = any(
        re.search(pattern, evidence_text, re.I | re.S)
        for pattern in MA_INACTIVE_PATTERNS
    )
    active_ma = any(
        re.search(pattern, evidence_text, re.I | re.S)
        for pattern in MA_ACTIVE_SIGNING_PATTERNS
    )
    for rule_id in matched_ids:
        if inactive_ma and not active_ma and rule_id == "ma_signed":
            evidence.append("ma_signed:suppressed_inactive_transaction")
            continue
        weight = DIRECTION_RULE_WEIGHTS.get(rule_id, _DEFAULT_DIRECTION_WEIGHTS.get(rule_id, 0.0))
        raw += weight
        positive += max(weight, 0.0)
        negative += max(-weight, 0.0)
        evidence.append(f"{rule_id}:{weight:+.2f}")
    for rule in ISSUER_STATE_DIRECTION_RULES:
        if issuer_role not in rule.roles:
            continue
        if not any(
            re.search(pattern, evidence_text, re.I | re.S)
            for pattern in rule.patterns
        ):
            continue
        weight = rule.weight
        raw += weight
        positive += max(weight, 0.0)
        negative += max(-weight, 0.0)
        evidence.append(f"{rule.rule_id}:{weight:+.2f}")
    if (
        positive >= MIXED_COMPONENT_THRESHOLD
        and negative >= MIXED_COMPONENT_THRESHOLD
        and abs(positive - negative) < MIXED_DOMINANCE_MARGIN
    ):
        direction = "mixed"
    elif raw >= POSITIVE_THRESHOLD:
        direction = "positive"
    elif raw <= NEGATIVE_THRESHOLD:
        direction = "negative"
    else:
        direction = "neutral"
    strength = positive + negative if direction == "mixed" else abs(raw)
    return {
        "direction": direction,
        "raw_score": round(raw, 4),
        "normalized_score": round(max(-1.0, min(1.0, raw / 4.0)), 4),
        "confidence": round(min(0.99, 0.50 + min(strength, 4.0) / 8.0), 4),
        "matched_rules": evidence,
    }
