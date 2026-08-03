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
    sanitized_text = _remove_nonsemantic_link_blocks(sanitized_text)
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
        (match.group("ticker") or match.group("trade_ticker")).upper()
        for match in ANNOUNCED_TICKER_RE.finditer(f"{document.title}\n{document.text}")
    )
    for raw_source in raw_labels:
        source = raw_source.as_dict()
        if str(source.get("unit_role") or "") in DENIED_UNIT_ROLES:
            continue
        if not _retain_unit_v9(source, provider_tickers=provider_tickers):
            continue
        label = dict(source)
        classification = dict(label.get("classification") or {})
        evidence_text = str(label.get("semantic_evidence_text") or "")
        issuer_role = _refine_issuer_role_v9(
            str(label.get("issuer_role") or ""),
            evidence_text=evidence_text,
            content_role=role,
        )
        label["issuer_role"] = issuer_role
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
            issuer_role=issuer_role,
        )
        direction = _recalibrate_direction(
            classification,
            issuer_role=issuer_role,
            evidence_text=evidence_text,
        )
        direction = _compose_direction_v9(
            direction,
            evidence_text=evidence_text,
            issuer_role=issuer_role,
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
        history_eligible = bool(label.get("ticker"))
        # Keep the nested persisted classification and the label envelope on
        # one contract.  Divergent eligibility fields made UI/audit consumers
        # disagree even though the evaluator read the envelope correctly.
        classification.update({
            "forecast_trigger_eligible": forecast_eligible,
            "reaction_evaluation_eligible": reaction_eligible,
            "prior_primary_context_eligible": history_eligible,
            "episode_followup_eligible": history_eligible,
            "eligibility_basis": eligibility_basis,
        })
        label.update({
            "classification": classification,
            "forecast_trigger_eligible": forecast_eligible,
            "reaction_evaluation_eligible": reaction_eligible,
            "issuer_history_context_eligible": history_eligible,
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
    r"\bmovers?\s*&\s*shakers?\b|\ba\s+peek\s+into\s+the\s+markets\b|"
    r"\bmarket\s+primer\b|\bnews\s+summary\b|\bfintech\s+focus\b|"
    r"\b\d+\s+.+\s+stories\s+you\s+"
    r"(?:might(?:'ve|\s+have)|may\s+have)\s+missed\b",
    re.I,
)
_PREVIEW_ARTICLE_RE = re.compile(
    r"\b(?:earnings|results?)\s+preview\b|\bwhat\s+to\s+expect\b|"
    r"\bahead\s+of\s+(?:its\s+)?(?:earnings|results?)\b|"
    r"\bETFs?\s+for\s+.{0,80}\bearnings\s+season\b|"
    r"\b(?:economic|earnings)\s+calendar\b|\bcalendar\s+of\s+economic\s+events\b|"
    r"\b(?:reports?|earnings)\s+(?:due|expected|scheduled)\b",
    re.I,
)
_STOCKS_TO_WATCH_PREVIEW_RE = re.compile(
    r"\b(?:\d+\s+)?stocks?\s+to\s+watch\b.{0,100}"
    r"\b(?:heading\s+into|before|ahead\s+of|for)\b",
    re.I | re.S,
)
_EDITORIAL_ANALYSIS_TITLE_RE = re.compile(
    r"\b(?:here(?:'s|\s+is)\s+why|ways?\s+to|should\s+(?:investors|shareholders)|"
    r"would\s+have\s+\$?[\d,]+\s+today|strongest\s+.+\s+trend|"
    r"flood\s+of\s+unusual|remarks?\s+on|predicts?|explains?\s+why|"
    r"to\s+benefit\s+from|begins?\s+to\s+fail|in\s+reaction\s+to|"
    r"citron\s+(?:tweets?|says?))\b",
    re.I,
)
_PRICE_REACTION_FOLLOWUP_RE = re.compile(
    r"\b(?:crash|plummet|fall|drop|sink|slide|tumble)[a-z]*\b.{0,100}"
    r"\b(?:after|following|on)\b|"
    r"\b(?:shares?|stock)\s+(?:crash|plummet|fall|drop|sink|slide|tumble|"
    r"rise|jump|surge|soar)[a-z]*\b.{0,100}\b(?:after|following|on)\b|"
    r"\b(?:shares?|stock)\s+trading\s+(?:up|down)\b.{0,140}\bafter\b.{0,160}\bannounc|"
    r"\bwhy\b.{0,90}\b(?:shares?|stock)\b",
    re.I | re.S,
)
_DIRECT_ISSUER_TEXT_RE = re.compile(
    r"\b(?:PRNewswire|Business\s+Wire|Globe\s+Newswire)\b",
    re.I,
)
_PRIMARY_REGULATORY_SOURCE_RE = re.compile(
    r"^\s*(?:the\s+)?(?:nasdaq(?:\s+stock\s+market)?|nyse|sec|fda)\s+"
    r"(?:announc|report|state|notify|order|suspend|halt)",
    re.I,
)
_DIRECT_ANALYST_RESEARCH_RE = re.compile(
    r"\b(?:in\s+a\s+report\s+published|published\s+a\s+research\s+report|"
    r"analyst\s+.+?\s+(?:wrote|said)\s+in\s+a\s+(?:report|note)|"
    r"(?:maintain|upgrade|downgrade|reiterate|initiate)[a-z]*\s+.+?\s+(?:at|to)\b)",
    re.I | re.S,
)
_WHY_MOVING_RE = re.compile(
    r"\bwhy\s+(?:is|are|did)\b.{0,80}\bmoving\b|^\s*here(?:'s|\s+is)\s+why\b",
    re.I,
)
_REPORTED_EARLIER_RE = re.compile(
    r"^\s*(?:reported|announced|published)\s+(?:earlier|previously)\b|"
    r"^\s*reported\s+(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b|"
    r"\b(?:as|we)\s+(?:reported|noted)\s+(?:earlier|previously)\b",
    re.I,
)
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
    r"\b(?:fda|usda|sec|nasdaq|nyse)\b.{0,100}\b(?:approv|clear|subpoena|halt|resume|"
    r"noncompliance|fil(?:e|ing)|registration|investigat)|"
    r"\b(?:form\s+8-k|form\s+4|regulatory\s+approval|clinical\s+hold|"
    r"fast\s+track\s+designation|suspends?\s+trading|patent\s+infringement\s+lawsuit)\b|"
    r"^\s*fed(?:eral\s+reserve)?(?:'s)?\b.{0,80}\b(?:says?|remarks?|announces?)\b",
    re.I | re.S,
)
_AUTOMATED_ARTICLE_RE = re.compile(
    r"\bthis\s+article\s+was\s+generated\s+by\s+benzinga(?:'s)?\s+automated\s+"
    r"content\s+engine\b",
    re.I,
)
_PRIMARY_RESULT_TITLE_RE = re.compile(
    r"\b(?:revenue|earnings|eps|sales|bookings)\s+(?:beat|miss)\b|"
    r"\breports?\b.{0,100}\b(?:results?|earnings|eps|revenue|sales|profit|loss|bookings)\b|"
    r"\b(?:results?|earnings|eps|revenue|sales|profit|loss|bookings)\b.{0,100}"
    r"\b(?:beat|miss|guid(?:e|es|ance)|buyback|increase|decrease|rise|fall)\b|"
    r"\bsees?\b.{0,100}\b(?:eps|revenue|sales|profit|loss|guidance|outlook)\b",
    re.I | re.S,
)
_PRIMARY_OPERATION_TITLE_RE = re.compile(
    r"\b(?:appoints?|names?|hires?)\b.{0,100}\b(?:president|officer|director|chief)\b|"
    r"\b(?:suspends?|resumes?|restarts?|halts?|closes?|shuts?\s+down)\b.{0,100}"
    r"\b(?:flights?|operations?|production|facility|service|trading)\b|"
    r"\b(?:launches?|receives?\s+(?:fda|usda)|wins?|secures?|awarded)\b",
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
    channels = {
        str(value).strip().casefold() for value in document.metadata.get("channels") or ()
    }
    # A republished prior event is context about an already public catalyst,
    # not a new causal trigger at this article timestamp.
    if _REPORTED_EARLIER_RE.search(title):
        return "why_moving_followup", "structural_reported_earlier_title"
    if re.search(r"\b(?:from\s+earlier|earlier\s+announced|previously\s+announced)\b", title, re.I):
        return "why_moving_followup", "structural_prior_event_republication"
    if _STOCKS_TO_WATCH_PREVIEW_RE.search(title):
        return "preview", "structural_stocks_to_watch_preview"
    if re.search(r"\bmarket\s+primer\b", title, re.I) and (
        len(re.findall(r"\bexpected\s+to\s+report\b", document.text, re.I)) >= 5
        or re.search(r"\bearnings\s+releases?\s+expected\b", document.text, re.I)
    ):
        return "preview", "structural_market_primer_preview"
    if _MOVER_ARTICLE_RE.search(title):
        return "mover_recap", "structural_mover_title"
    if _ROUNDUP_ARTICLE_RE.search(title):
        return "market_roundup", "structural_roundup_title"
    if _is_verified_automated(document):
        return "automated_summary", "verified_automated_product"
    if _WHY_MOVING_RE.search(title):
        return "why_moving_followup", "structural_why_moving_title"
    if _EXPLICIT_ANALYST_ACTION_RE.search(title) or (
        channels & {"analyst color", "analyst ratings", "downgrades", "upgrades"}
        and re.search(
            r"\b(?:wall\s+street|analyst|research|outlook|trend|price\s+target|"
            r"our\s+(?:rating|target|estimates?)|we\s+(?:rate|maintain|expect))\b",
            f"{title}\n{document.text[:1200]}",
            re.I,
        )
    ):
        return "analyst_event", "explicit_analyst_action_title"
    if re.search(r"\bZacks\s+Investment\s+Research\b", document.text, re.I):
        return "editorial_analysis", "structural_syndicated_zacks_editorial"
    if _PRIMARY_RESULT_TITLE_RE.search(title):
        return "primary_event", "substantive_current_result_title"
    if _PRIMARY_OPERATION_TITLE_RE.search(title):
        return "primary_event", "substantive_current_operating_title"
    if _PRICE_REACTION_FOLLOWUP_RE.search(title):
        return "why_moving_followup", "structural_price_reaction_followup"
    if _PREVIEW_ARTICLE_RE.search(title):
        return "preview", "structural_preview_title"
    if _ANALYST_BLOG_RE.search(title):
        return "editorial_analysis", "structural_analyst_blog_title"
    if _EDITORIAL_ANALYSIS_TITLE_RE.search(title):
        return "editorial_analysis", "structural_editorial_analysis_title"
    if _REGULATORY_CURRENT_RE.search(title):
        return "regulatory_event", "explicit_current_regulatory_title"
    if base_role == "automated_summary":
        # Older rules matched any byline containing "Insights", including the
        # human-authored "Benzinga EV Insights" desk. Only the explicit
        # automated evidence above may retain the automated role.
        return "primary_event", "unverified_automated_role_rejected"
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
    title = document.title or ""
    complete_text = f"{title}\n{document.text[:1200]}"
    if role == "automated_summary":
        return "automated_summary", "verified_automated_product"
    if role in {"market_roundup", "mover_recap", "preview"}:
        return "editorial_aggregation", "aggregation_role"
    if role == "why_moving_followup":
        body = document.text.removeprefix(f"Title: {title}").strip()
        return (
            ("editorial_original", "reported_followup_with_body")
            if len(body) >= 80
            else ("editorial_aggregation", "title_only_followup")
        )
    if _ANALYST_BLOG_RE.search(document.title or ""):
        return "editorial_original", "syndicated_analyst_blog"
    if re.search(r"\bZacks\s+Investment\s+Research\b", document.text, re.I):
        return "editorial_aggregation", "syndicated_zacks_editorial"
    if role == "analyst_event" and _DIRECT_ANALYST_RESEARCH_RE.search(complete_text):
        return "analyst_research", "direct_analyst_research_evidence"
    if role == "regulatory_event" and _PRIMARY_REGULATORY_SOURCE_RE.search(complete_text):
        return "regulatory_primary", "regulatory_primary_title"
    if channels & _ISSUER_DIRECT_CHANNELS or _DIRECT_ISSUER_TEXT_RE.search(complete_text):
        return "issuer_direct", "issuer_distribution_evidence"
    if role == "analyst_event":
        return "editorial_original", "reported_analyst_commentary"
    if re.search(r"(?im)^\s*-?\s*Reuters\s*$", document.text):
        return "editorial_aggregation", "cited_wire_republication"
    if base_origin == "automated_summary" and not _is_verified_automated(document):
        return "editorial_original", "unverified_automated_origin_rejected"
    # Fall back only to the pre-existing metadata/source authority. Content
    # wording alone is not allowed to invent provenance here.
    return _override(base_origin, signals, SOURCE_ORIGIN_OVERRIDES)


def _is_verified_automated(document: SemanticDocument) -> bool:
    author = str(document.metadata.get("author") or "").strip().casefold()
    channels = {
        str(value).strip().casefold() for value in document.metadata.get("channels") or ()
    }
    tags = {
        str(value).strip().casefold() for value in document.metadata.get("provider_tags") or ()
    }
    return bool(
        author == "benzinga insights"
        or any(value.startswith("bzi-") for value in tags)
        or "macro notification" in channels
        or _AUTOMATED_ARTICLE_RE.search(document.text)
    )


def _normalize_provider_ticker(value: str) -> str:
    raw = str(value or "").upper().strip()
    if ":" in raw:
        exchange, _, ticker = raw.partition(":")
        if exchange in {"NASDAQ", "NYSE", "AMEX", "OTC", "OTCQX", "OTCQB", "TSX", "TSXV", "CSE"}:
            return ticker
    return raw


_NONSEMANTIC_LINK_LINE_RE = re.compile(
    r"(?im)^\s*(?:related\s+link|see\s+also|check\s+this\s+out|read\s+next)\s*:.*$"
)


def _remove_nonsemantic_link_blocks(text: str) -> str:
    """Remove navigation teasers without discarding later article sections."""
    return re.sub(r"\n{3,}", "\n\n", _NONSEMANTIC_LINK_LINE_RE.sub("", text)).strip()


def _retain_unit_v9(label: dict[str, Any], *, provider_tickers: set[str]) -> bool:
    """Retain fact-checked issuer passages even when provider links omit them.

    Provider tickers are candidate evidence, not the semantic universe.  The
    scoped extractor has already required an explicit symbol or an
    unambiguous point-in-time issuer alias.  V9 therefore keeps those passages
    while continuing to reject weak inherited or price-only mentions.
    """
    classification = label.get("classification") or {}
    unit_role = str(label.get("unit_role") or "")
    evidence = str(label.get("semantic_evidence_text") or "").strip()
    concepts = set(classification.get("event_concepts") or ())
    event_supported = bool(concepts) or _SUPPORTED_EVENT_EVIDENCE_RE.search(evidence)
    # Pure price tables, peer lists, fund holdings and incidental issuer names
    # are context, not issuer semantic units. Provider links do not override
    # the absence of an issuer event predicate.
    if unit_role in CONTEXT_ONLY_UNIT_ROLES_V9 and not event_supported:
        return False
    ticker = str(label.get("ticker") or "").upper()
    explicit_symbol = bool(re.search(
        rf"\b(?:NASDAQ|NYSE|AMEX|OTC(?:QX|QB)?):\s*{re.escape(ticker)}\b",
        evidence,
        re.I,
    )) if ticker else False
    if ticker not in provider_tickers and not explicit_symbol and not event_supported:
        return False
    if _retain_unit_v8(label, provider_tickers=provider_tickers):
        return True
    flags = set(
        (label.get("classification") or {}).get("quality_flags")
        or label.get("quality_flags")
        or ()
    )
    scope = str(label.get("evidence_scope") or "")
    return (
        "passage_explicit_issuer" in flags
        and scope in {"ticker_specific", "shared_relational", "shared_ambiguous"}
        and len(evidence.split()) >= 8
        and str(label.get("unit_role") or "") not in DENIED_UNIT_ROLES
        and event_supported
    )


_SUPPORTED_EVENT_EVIDENCE_RE = re.compile(
    r"\b(?:announc|report|file|submit|approv|accept|clear|designat|award|win|secure|"
    r"acquir|merge|partner|collaborat|settle|sue|investigat|halt|delist|bankrupt|"
    r"restructur|offer|placement|convertible|buyback|repurchase|dividend|guidance|"
    r"outlook|forecast|earnings|eps|revenue|sales|profit|loss|bookings|margin|trial|"
    r"endpoint|contract|order|appoint|resign|launch|deploy|recall|split|compliance|"
    r"noncompliance|resume|suspend|shutdown|closure|layoff|stake|ownership|usda)\w*\b",
    re.I,
)


def _refine_issuer_role_v9(
    issuer_role: str,
    *,
    evidence_text: str,
    content_role: str,
) -> str:
    """Resolve roles stated by the scoped predicate, including one-sided M&A."""
    if content_role == "analyst_event":
        return "analyst_subject"
    if re.search(
        r"\b(?:entered?\s+into\s+.{0,50})?(?:to\s+be|being|was|is)\s+acquired\s+by\b|"
        r"\bdefinitive\s+merger\s+agreement\b.{0,100}\bto\s+be\s+acquired\b",
        evidence_text,
        re.I | re.S,
    ):
        return "target"
    if re.search(
        r"\b(?:file[sd]?|bring(?:s|ing)?|commence[sd]?|initiate[sd]?)\b"
        r".{0,100}\b(?:lawsuit|legal\s+action|complaint)\b.{0,100}\bagainst\b",
        evidence_text,
        re.I | re.S,
    ):
        return "plaintiff"
    if re.search(
        r"\b(?:lawsuit|legal\s+action|complaint)\b.{0,100}\bagainst\b",
        evidence_text,
        re.I | re.S,
    ) and issuer_role not in {"plaintiff", "regulator"}:
        return "defendant"
    return issuer_role or "primary_subject"


def _refine_event_concepts(
    concepts: set[str],
    *,
    evidence_text: str,
    issuer_role: str,
) -> set[str]:
    """Apply event-local state and instrument semantics before direction."""
    text = evidence_text
    output = set(concepts)
    # Rating state and target state are separate.  A maintained rating with a
    # raised target is not a rating upgrade.
    maintained = bool(re.search(
        r"\b(?:maintain(?:s|ed)?|reiterate[sd]?)\b.{0,90}"
        r"\b(?:buy|sell|hold|overweight|underweight|outperform|underperform|neutral)\b",
        text,
        re.I | re.S,
    ))
    explicit_upgrade = bool(re.search(r"\bupgrade[sd]?\b", text, re.I))
    explicit_downgrade = bool(re.search(r"\bdowngrade[sd]?\b", text, re.I))
    if maintained and not explicit_upgrade and not explicit_downgrade:
        output = {
            value for value in output
            if value not in {"analyst.rating_upgrade", "analyst.rating_downgrade"}
        }
        output.add("analyst.rating_maintained")
    if re.search(
        r"\b(?:raises?|increases?)\b.{0,50}\bprice\s+target\b|"
        r"\bprice\s+target\b.{0,70}\b(?:raised|increased|to\s+\$?\d)\b",
        text,
        re.I | re.S,
    ):
        output.add("analyst.price_target_raised")
    if re.search(
        r"\b(?:lowers?|cuts?|reduces?)\b.{0,50}\bprice\s+target\b|"
        r"\bprice\s+target\b.{0,70}\b(?:lowered|cut|reduced)\b",
        text,
        re.I | re.S,
    ):
        output.add("analyst.price_target_lowered")
    target_change = re.search(
        r"\bprice\s+target\b.{0,50}\bfrom\s+\$?(?P<old>\d+(?:\.\d+)?)"
        r"\s+to\s+\$?(?P<new>\d+(?:\.\d+)?)",
        text,
        re.I | re.S,
    )
    if target_change:
        output.discard("analyst.price_target_raised")
        output.discard("analyst.price_target_lowered")
        old = float(target_change.group("old"))
        new = float(target_change.group("new"))
        if new > old:
            output.add("analyst.price_target_raised")
        elif new < old:
            output.add("analyst.price_target_lowered")
    if re.search(r"\b(?:initial\s+public\s+offering|ipo)\b", text, re.I):
        output.difference_update({"financing.public_offering", "financing"})
        output.add("listing_market_structure.ipo")
    if re.search(r"\b(?:awarded|won|secured)\b.{0,100}\b(?:contract|order|grant)\b", text, re.I | re.S):
        output.add("contract")
    if re.search(r"\b(?:share\s+repurchase|stock\s+repurchase|buyback)\b", text, re.I):
        output.add("capital_return")
    if re.search(r"\bdividend\b", text, re.I):
        output.add("capital_return")
    if re.search(r"\b(?:partner(?:ship|ed)?|collaborat(?:ion|ed)?)\b", text, re.I):
        output.add("commercial")
    if re.search(r"\b(?:public\s+offering|registered\s+direct|private\s+placement|at-the-market)\b", text, re.I):
        output.add("financing")
    if re.search(r"\b(?:guidance|outlook|forecast)\b|\b(?:sees?|guides?)\b.{0,100}\bvs\.?\b", text, re.I | re.S):
        output.add("guidance")
    if re.search(r"\b(?:earnings|eps|revenue|sales|profit|loss|bookings|quarterly\s+results?)\b", text, re.I):
        output.add("earnings")
    if re.search(r"\b(?:sees?|guides?|expects?|forecasts?|projects?)\b", text, re.I) and re.search(
        r"\b(?:vs\.?|versus)\b.{0,30}\b(?:est\.?|estimate|consensus)\b", text, re.I | re.S
    ) and not re.search(r"\b(?:reported|reports?|actual|came\s+in|quarterly\s+results?)\b", text, re.I):
        output.discard("earnings")
        output.add("guidance")
    if re.search(r"\b(?:fda|usda|regulator|regulatory|approval|clearance|clinical\s+hold)\b", text, re.I):
        output.add("regulatory")
    if re.search(r"\b(?:vaccine|drug|treatment|product|platform|software|service)\b.{0,100}"
                 r"\b(?:approv|launch|sell|commercializ|clear)\w*\b|"
                 r"\b(?:approv|launch|sell|commercializ|clear)\w*\b.{0,100}"
                 r"\b(?:vaccine|drug|treatment|product|platform|software|service)\b", text, re.I | re.S):
        output.add("product_commercial")
    if re.search(r"\b(?:trial|clinical|endpoint|patient|study\s+data)\b", text, re.I):
        output.add("clinical")
    if re.search(
        r"\b(?:agree[sd]?\s+to\s+acquire|acquisition\s+(?:agreement|offer|proposal)|"
        r"merger\s+agreement|offer\s+to\s+acquire|to\s+be\s+acquired|takeover\s+(?:offer|bid))\b",
        text,
        re.I,
    ):
        output.add("ma_transaction")
    if re.search(r"\b(?:settlement|lawsuit|litigation|legal\s+action|patent\s+infringement)\b", text, re.I):
        output.add("legal")
    if re.search(r"\b(?:stake|ownership|beneficial\s+owner|takes?\s+a\s+position)\b", text, re.I):
        output.add("ownership")
    if re.search(
        r"\b(?:launch(?:es|ed|ing)?|commercializ\w*|demand\s+(?:rose|grew|fell|declined|weakened)|"
        r"shipments?\s+(?:rose|grew|fell|declined)|opens?|closes?|suspends?|resumes?|restarts?)\b"
        r".{0,80}\b(?:product|service|store|facility|flight|production|operations?)\b|"
        r"\b(?:store|facility|flight|production|operations?)\b.{0,80}"
        r"\b(?:open|close|suspend|resume|restart|expand|reduce)\w*\b",
        text,
        re.I | re.S,
    ):
        output.add("operations")
    if re.search(r"\b(?:shares?|stock)\b.{0,70}\b(?:rose|gained|climbed|jumped|surged|fell|dropped|declined|slid|plunged|tumbled)\b|"
                 r"\b(?:rose|gained|climbed|jumped|surged|fell|dropped|declined|slid|plunged|tumbled)\b.{0,70}\b(?:shares?|stock)\b", text, re.I | re.S):
        output.add("market_reaction")
    if re.search(r"\b(?:delist(?:ed|ing)?|continued\s+listing|listing\s+compliance|noncompliance)\b", text, re.I):
        output.add("listing_market_structure")
    if _PRIMARY_ENDPOINT_FAILURE_RE.search(text):
        output = {value for value in output if "success" not in value and "positive_data" not in value}
        output.add("clinical.failure")
    if re.search(r"\b(?:plan\s+to\s+exit|emerg(?:e|ed|ing)\s+from|exit)\b.{0,80}\b(?:chapter\s+11|bankruptcy)\b|\b(?:court\s+)?approved\b.{0,100}\b(?:reorganization|bankruptcy)\s+plan\b", text, re.I | re.S):
        output.discard("credit_solvency.bankruptcy")
        output.add("credit_solvency.reorganization_approved")
    if re.search(r"\b(?:board|director|officer|president|chief\s+\w+\s+officer)\b.{0,100}\b(?:appoint|elect|name[sd]?|hire[sd]?)\b|"
                 r"\b(?:appoint|elect|name[sd]?|hire[sd]?)\b.{0,100}\b(?:board|director|officer|president|chief\s+\w+\s+officer)\b", text, re.I | re.S):
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
    initial_direction = str(direction.get("direction") or "neutral")
    # Transaction roles dominate generic financing/debt language.  A target
    # receiving a stated cash/share premium is positive even when the buyer
    # finances the deal with debt; that financing belongs to the acquirer.
    if issuer_role == "target" and re.search(
        r"\b(?:acquired|acquisition|merger|buyout|offer)\b.{0,180}"
        r"\b(?:\$\s*\d|per\s+share|premium|cash\s+consideration)\b|"
        r"\b(?:\$\s*\d|per\s+share|premium|cash\s+consideration)\b.{0,180}"
        r"\b(?:acquired|acquisition|merger|buyout|offer)\b",
        text,
        re.I | re.S,
    ):
        forced, basis = "positive", "ma_target_consideration"
    # Explicit analyst state change is the semantic action; surrounding thesis
    # language cannot reverse it.
    if re.search(r"\b(?:downgrade[sd]?|lowers?|cuts?)\b.{0,90}\b(?:rating|(?:price\s+)?target|to\s+(?:hold|sell|neutral|equal[- ]weight|underperform|underweight))\b", text, re.I | re.S):
        forced, basis = "negative", "explicit_analyst_negative_action"
    elif re.search(r"\b(?:upgrade[sd]?|raises?)\b.{0,90}\b(?:rating|(?:price\s+)?target|to\s+(?:buy|outperform|overweight))\b", text, re.I | re.S):
        forced, basis = "positive", "explicit_analyst_positive_action"
    if re.search(r"\b(?:maintain(?:s|ed)?|reiterate[sd]?)\b.{0,80}\b(?:sell|underperform|underweight)\b", text, re.I | re.S):
        forced, basis = "negative", "maintained_negative_analyst_rating"
    elif re.search(r"\b(?:maintain(?:s|ed)?|reiterate[sd]?)\b.{0,80}\b(?:buy|outperform|overweight)\b", text, re.I | re.S):
        forced, basis = "positive", "maintained_positive_analyst_rating"
    if _PRIMARY_ENDPOINT_FAILURE_RE.search(text):
        forced, basis = "negative", "primary_endpoint_failure_precedence"
    result_text = " ".join(
        clause for clause in re.split(r"(?<=[.!?])\s+|\n+", text)
        if not re.search(
            r"\b(?:shares?|stock)\b.{0,80}\b(?:rose|gained|climbed|jumped|surged|"
            r"rallied|fell|dropped|declined|slid|plunged|tumbled)\b|"
            r"\b(?:rose|gained|climbed|jumped|surged|rallied|fell|dropped|declined|"
            r"slid|plunged|tumbled)\b.{0,80}\b(?:shares?|stock)\b",
            clause,
            re.I | re.S,
        )
    )
    current_result_positive = bool(re.search(
        r"\b(?:eps|earnings|revenue|sales|profit|bookings)\b.{0,100}"
        r"\b(?:up\s+from|increase[sd]?\s+(?:over|from)|rose|grew|beat(?:s|ing)?|above)\b|"
        r"\b(?:beat(?:s|ing)?|above|up\s+from|increase[sd]?\s+(?:over|from))\b.{0,100}"
        r"\b(?:eps|earnings|revenue|sales|profit|bookings)\b",
        result_text, re.I | re.S,
    ))
    current_result_negative = bool(re.search(
        r"\b(?:eps|earnings|revenue|sales|profit|bookings)\b.{0,100}"
        r"\b(?:down\s+from|decrease[sd]?\s+(?:from|versus)|fell|declined|miss(?:es|ed)?|below)\b|"
        r"\b(?:miss(?:es|ed)?|below|down\s+from|decrease[sd]?\s+(?:from|versus))\b.{0,100}"
        r"\b(?:eps|earnings|revenue|sales|profit|bookings)\b",
        result_text, re.I | re.S,
    ))
    if current_result_positive and current_result_negative:
        forced, basis = "mixed", "current_results_mixed"
    elif current_result_positive:
        forced, basis = "positive", "current_results_positive"
    elif current_result_negative:
        forced, basis = "negative", "current_results_negative"
    if re.search(r"\b(?:guidance|outlook|forecast)\b.{0,120}\b(?:below|lower|cut|miss(?:es|ed)?)\b.{0,80}\b(?:estimate|consensus|prior|expectation)?", text, re.I | re.S):
        forced, basis = "negative", "forward_guidance_negative_precedence"
    if re.search(r"\b(?:guidance|outlook|forecast)\b.{0,120}\b(?:above|raise[sd]?|strong|better)\b.{0,80}\b(?:estimate|consensus|prior|expectation)?", text, re.I | re.S):
        forced, basis = "positive", "forward_guidance_positive_precedence"
    # Compact wire headlines frequently express guidance only as
    # ``sees/guides <range> vs <estimate>``.  Compare the complete range with
    # the estimate; do not infer direction merely from the word ``growth``.
    forecast_comparisons = _forecast_range_comparisons(text)
    forecast_only = not bool(re.search(
        r"\b(?:reported|actual|results?\s+(?:for|show)|quarter\s+ended|"
        r"revenue\s+(?:was|came\s+in)|earned)\b",
        text,
        re.I,
    ))
    if forecast_comparisons:
        comparison_directions = {value for value in forecast_comparisons}
        if comparison_directions == {"negative"}:
            forced, basis = "negative", "forward_numeric_range_below_estimate"
        elif comparison_directions == {"positive"}:
            forced, basis = "positive", "forward_numeric_range_above_estimate"
        elif comparison_directions >= {"positive", "negative"}:
            forced, basis = "mixed", "forward_numeric_ranges_mixed"
    # A current result and an adverse forward guide are distinct components;
    # preserve both rather than letting the last matching rule erase one.
    if (
        forced in {"positive", "negative"}
        and initial_direction in {"positive", "negative", "mixed"}
        and initial_direction != forced
        and not (basis.startswith("forward_numeric_range_") and forecast_only)
    ):
        forced, basis = "mixed", f"{basis}_offset"
    if "credit_solvency.reorganization_approved" in concepts:
        forced, basis = "positive", "bankruptcy_exit_state"
    if re.search(r"\b(?:board|director|officer|chief\s+\w+\s+officer)\b.{0,100}\b(?:appoint|elect|name[sd]?|hire[sd]?)\b", text, re.I | re.S):
        forced, basis = "neutral", "ordinary_management_appointment"
    if re.search(r"\b(?:reverse\s+stock\s+split|1-for-\d+\s+(?:reverse\s+)?split)\b", text, re.I):
        forced, basis = "negative", "reverse_split_state"
    if re.search(
        r"\b(?:announc(?:e[sd]?|ement)|authoriz(?:e[sd]?|ation)|restart(?:s|ed)?|"
        r"resume[sd]?|increase[sd]?)\b"
        r".{0,100}\b(?:share\s+repurchase|stock\s+repurchase|buyback)\b",
        text,
        re.I | re.S,
    ):
        # Do not erase a simultaneous adverse event; represent the composition.
        current_direction = forced or str(direction.get("direction") or "")
        forced = "mixed" if current_direction in {"negative", "mixed"} else "positive"
        basis = "buyback_offset" if forced == "mixed" else "buyback_positive"
    if re.search(r"\b(?:awarded|won|secured)\b.{0,100}\b(?:contract|order|grant)\b", text, re.I | re.S):
        forced, basis = "positive", "contract_award"
    if re.search(r"\b(?:fda|usda|regulator)\b.{0,100}\b(?:approv|clear|accept|designat)\w*\b|"
                 r"\b(?:approv|clear|accept|designat)\w*\b.{0,100}\b(?:fda|usda|regulator)\b", text, re.I | re.S):
        forced, basis = "positive", "regulatory_approval"
    if re.search(r"\b(?:regain(?:s|ed)?|receive[sd]?)\b.{0,80}\b(?:listing\s+)?compliance\b|"
                 r"\btrading\s+(?:will\s+)?resume[sd]?\b", text, re.I | re.S):
        forced, basis = "positive", "listing_or_trading_restoration"
    if re.search(r"\b(?:noncompliance|delist(?:ed|ing)?|listing\s+suspension)\b", text, re.I):
        forced, basis = "negative", "adverse_listing_state"
    # Observed price action is retained as reaction evidence by the scoped
    # extractor, but it never defines the semantic direction of the catalyst.
    if re.search(r"\b(?:weak|declining|lower)\b.{0,70}\b(?:demand|sales|orders?|shipments?)\b|"
                 r"\b(?:shutdown|closure|suspend(?:s|ed)?\s+(?:flights?|operations?|production)|layoffs?)\b", text, re.I | re.S):
        forced, basis = "negative", "adverse_operating_state"
    if re.search(r"\b(?:settle[sd]?|settlement)\b", text, re.I) and not re.search(
        r"\b(?:pay|payment|charge|expense|cost|fine|penalty|admit(?:s|ted)?\s+wrongdoing)\b",
        text,
        re.I,
    ):
        forced, basis = "positive", "legal_uncertainty_resolved"
    if re.search(r"\b(?:increase[sd]?|raise[sd]?)\b.{0,60}\bdividend\b|\bdividend\b.{0,60}\b(?:increase[sd]?|raise[sd]?)\b", text, re.I | re.S):
        forced = "mixed" if initial_direction in {"negative", "mixed"} else "positive"
        basis = "dividend_increase_offset" if forced == "mixed" else "dividend_increase"
    if re.search(r"\b(?:cut|reduce[sd]?|suspend(?:s|ed)?)\b.{0,60}\bdividend\b|\bdividend\b.{0,60}\b(?:cut|reduced|suspended)\b", text, re.I | re.S):
        forced, basis = "negative", "dividend_reduction"
    issuer_supply = bool(
        issuer_role not in {"target", "counterparty", "lender", "financing_provider"}
        and re.search(
            r"\b(?:public\s+offering|registered\s+direct|private\s+placement|"
            r"convertible\s+(?:note|debt)|preferred\s+stock|at-the-market|"
            r"issue|issuance|offering)\b.{0,140}\b(?:share|stock|warrant|note|debt|security|securities)\b|"
            r"\b(?:share|stock|warrant|note|debt|security|securities)\b.{0,140}"
            r"\b(?:public\s+offering|registered\s+direct|private\s+placement|issued?|offering)\b",
            text,
            re.I | re.S,
        )
    )
    selling_holder_secondary = bool(re.search(
        r"\bsecondary\s+(?:public\s+)?offering\b.{0,140}"
        r"\b(?:selling\s+stockholder|existing\s+shareholder)s?\b|"
        r"\b(?:selling\s+stockholder|existing\s+shareholder)s?\b.{0,140}"
        r"\bsecondary\s+(?:public\s+)?offering\b",
        text,
        re.I | re.S,
    )) or bool(re.search(
        r"\b(?:is|are|will)\s+not\s+selling\s+(?:any\s+)?shares\b|"
        r"\bwill\s+not\s+receive\s+any\s+proceeds\b",
        text,
        re.I,
    ))
    subsidiary_ipo = bool(re.search(
        r"\b(?:(?:its|the|a)\s+|[A-Za-z0-9&.-]+'s\s+)(?:wholly[- ]owned\s+)?subsidiar(?:y|ies)\b.{0,160}"
        r"\b(?:initial\s+public\s+offering|ipo)\b|"
        r"\b(?:initial\s+public\s+offering|ipo)\b.{0,160}"
        r"\b(?:(?:its|the|a)\s+|[A-Za-z0-9&.-]+'s\s+)(?:wholly[- ]owned\s+)?subsidiar(?:y|ies)\b",
        text,
        re.I | re.S,
    ))
    if issuer_supply and not selling_holder_secondary and not subsidiary_ipo:
        forced, basis = "negative", "issuer_financing_supply"
    elif selling_holder_secondary and re.search(
        r"\b(?:share\s+repurchase|stock\s+repurchase|buyback)\b", text, re.I
    ):
        forced, basis = "mixed", "selling_holder_overhang_with_buyback"
    elif subsidiary_ipo:
        forced, basis = "positive", "subsidiary_ipo_parent_value_realization"
    if issuer_supply and re.search(
        r"\b(?:completed|completion\s+of)\b.{0,100}\b(?:offering|at-the-market)\b",
        text,
        re.I | re.S,
    ) and re.search(r"\b(?:gross|net)\s+proceeds\b", text, re.I):
        forced, basis = "mixed", "completed_offering_dilution_and_cash"
    if issuer_role == "plaintiff" and re.search(r"\b(?:sue[sd]?|lawsuit|patent\s+infringement)\b", text, re.I):
        forced, basis = "neutral", "plaintiff_legal_role"
    if re.search(
        r"\b(?:settlement|lawsuit|legal)\b.{0,120}"
        r"\b(?:contribute|pay|payment|charge|expense|cost)\w*\b.{0,100}"
        r"\$\s*\d+(?:\.\d+)?\s*(?:million|billion|m|b)?\b|"
        r"\$\s*\d+(?:\.\d+)?\s*(?:million|billion|m|b)?\b.{0,100}"
        r"\b(?:settlement|lawsuit|legal)\b",
        text,
        re.I | re.S,
    ):
        forced, basis = "negative", "material_legal_payment"
    if re.search(
        r"\b(?:trading\s+halt|(?:was|is|remains?)\s+halted|halted\s+(?:solely\s+)?on|"
        r"suspends?\s+trading|"
        r"listing\s+suspension)\b",
        text,
        re.I,
    ):
        forced, basis = "negative", "trading_halt_or_suspension"
    if re.search(r"\bstrategic\s+investment\b", text, re.I) and re.search(
        r"\b(?:board|director)\b.{0,100}\b(?:appoint\w*|elect\w*|name[sd]?|seat)\b|"
        r"\b(?:appoint\w*|elect\w*|name[sd]?|nominee)\b.{0,100}\b(?:board|director)\b",
        text,
        re.I | re.S,
    ):
        forced, basis = "mixed", "strategic_investment_governance_mix"
    if forced == "negative" and re.search(
        r"\b(?:regulatory\s+clearance|fda\b.{0,30}\bclearance|regain(?:s|ed)?\s+compliance|"
        r"convert(?:s|ed)?\s+(?:its\s+)?debt)\b",
        text,
        re.I,
    ) and re.search(r"\b(?:revenue|earnings|results?|financing|debt)\b", text, re.I):
        forced, basis = "mixed", "adverse_results_with_positive_state_change"
    if re.search(
        r"\b(?:sales|revenue|earnings|profit)\s+growth\b.{0,90}"
        r"\b(?:struggle|difficult|weak|slow|declin|fall|miss)\w*\b|"
        r"\b(?:struggle|difficult|weak|slow|declin|fall|miss)\w*\b.{0,90}"
        r"\b(?:sales|revenue|earnings|profit)\s+growth\b",
        text,
        re.I | re.S,
    ):
        forced, basis = "negative", "adverse_growth_predicate"
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


_FORECAST_RANGE_VS_ESTIMATE_RE = re.compile(
    r"\b(?:sees?|guides?|expects?|forecasts?|projects?)\b"
    r"(?P<context>.{0,120}?)"
    r"(?P<low>\$?\d+(?:\.\d+)?)\s*(?:[MBK%]|million|billion)?\s*(?:-|to)?\s*"
    r"(?P<high>\$?\d+(?:\.\d+)?)?\s*(?:[MBK%]|million|billion)?\s*"
    r"(?:vs\.?|versus)\s*\$?(?P<estimate>\d+(?:\.\d+)?)\s*(?:[MBK%]|million|billion)?\s*"
    r"(?:est\.?|estimate|consensus)",
    re.I | re.S,
)


def _forecast_range_comparisons(text: str) -> tuple[str, ...]:
    """Return directions only when an entire forecast range clears the estimate."""
    output: list[str] = []
    for match in _FORECAST_RANGE_VS_ESTIMATE_RE.finditer(text[:1200]):
        low = float(match.group("low").lstrip("$"))
        high_raw = match.group("high")
        high = float(high_raw.lstrip("$")) if high_raw else low
        estimate = float(match.group("estimate"))
        lower, upper = sorted((low, high))
        if upper < estimate:
            output.append("negative")
        elif lower > estimate:
            output.append("positive")
    return tuple(output)


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
    current = unit_role not in CONTEXT_ONLY_UNIT_ROLES_V9 or (
        role in {"primary_event", "regulatory_event"}
        and str((label.get("classification") or {}).get("time_orientation") or "") == "current"
    )
    speculative = bool(re.search(
        r"\b(?:rumou?r(?:ed)?|reportedly|in\s+talks|unconfirmed|explor(?:e|ing)\s+(?:a\s+)?potential)\b",
        evidence_text,
        re.I,
    ))
    # Retrospection is a document/event property, not any historical phrase
    # anywhere in a long article. A current event may legitimately reference a
    # previously announced program without becoming a republished trigger.
    first_evidence_clause = re.split(r"(?<=[.!?])\s+|\n", evidence_text, maxsplit=1)[0]
    event_lead = f"{document.title}\n{first_evidence_clause}"
    retrospective = bool(
        _REPORTED_EARLIER_RE.search(document.title or "")
        or re.search(
            r"\b(?:previously|earlier|last\s+(?:week|month|quarter|year)|"
            r"had\s+(?:announced|reported|filed|agreed))\b",
            event_lead,
            re.I,
        )
    )
    high_value = any(
        concept == prefix or concept.startswith(prefix + ".")
        for concept in concepts
        for prefix in HIGH_VALUE_TRIGGER_CONCEPT_PREFIXES
    )
    tradable = True
    ticker = str(label.get("ticker") or "")
    announced = {
        (match.group("ticker") or match.group("trade_ticker")).upper()
        for match in ANNOUNCED_TICKER_RE.finditer(f"{document.title}\n{document.text}")
    }
    if re.search(
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
    # Scoped extraction may conservatively call a title-only or parent-company
    # passage editorial context.  Once the article authority establishes a
    # current primary/regulatory event, that same role cannot remain a blocker.
    if unit_role in CONTEXT_ONLY_UNIT_ROLES_V9 and not current:
        reasons.append(f"context_only_unit_role:{unit_role}")
    if not current:
        reasons.append("historical_or_retrospective_evidence")
    if speculative:
        reasons.append("speculative_or_unconfirmed_event")
    if retrospective:
        reasons.append("retrospective_or_republished_event")
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
