from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from research.text_intelligence.news_synthesis_v1.contracts import CONTRACT_VERSION, validate_document
from research.text_intelligence.news_synthesis_v1.registry import ConceptRegistry
from research.text_intelligence.news_synthesis_v1.facts import extract_typed_facts
from research.text_intelligence.news_synthesis_v1.synthesis import (
    derive_eligibility,
    derive_issuer_views,
    derive_synthesis,
)


def compile_review_spec(article: Mapping[str, Any], spec: Mapping[str, Any]) -> dict[str, Any]:
    sample_id = str(article["sample_id"])
    if spec.get("sample_id") != sample_id:
        raise RuntimeError(f"Review specification identity mismatch for {sample_id}")
    text = str(article.get("rendered_product", {}).get("text", ""))
    registry = ConceptRegistry.load()
    envelope = {
        field: _compile_envelope_decision(article, decision)
        for field, decision in spec["envelope"].items()
    }
    entities = [_expand_entity(article, row) for row in spec.get("entities", [])]
    entity_ids = {str(row["entity_id"]) for row in entities}
    ticker_entities = {
        str(row.get("ticker", "")).upper(): str(row["entity_id"])
        for row in entities
        if row.get("ticker")
    }
    statement_sources = list(spec.get("statements", []))
    statement_sources.extend(_compile_observed_market_move_sources(spec))
    statements: list[dict[str, Any]] = []
    participations: list[dict[str, Any]] = []
    for index, source in enumerate(statement_sources, start=1):
        statement_id = str(source.get("statement_id") or f"S{index:04d}")
        concept = str(source["concept_leaf"])
        if not registry.contains(concept):
            raise RuntimeError(f"Unregistered concept in {sample_id}/{statement_id}: {concept}")
        spans = [_resolve_evidence(article, value, statement=True) for value in source["evidence"]]
        statements.append(
            {
                "statement_id": statement_id,
                "statement_kind": str(source["statement_kind"]),
                "concept_leaf": concept,
                "epistemic_status": str(source["epistemic_status"]),
                "time_relation": str(source["time_relation"]),
                "evidence_spans": spans,
                "typed_facts": extract_typed_facts(spans),
            }
        )
        for participation in source.get("participations", []):
            requested_id = str(participation.get("entity_id", ""))
            entity_id = str(
                requested_id
                if requested_id in entity_ids
                else ticker_entities.get(
                    str(participation.get("ticker") or requested_id).upper(),
                    "",
                )
            )
            if entity_id not in entity_ids:
                raise RuntimeError(f"Unknown entity in {sample_id}/{statement_id}: {entity_id}")
            participations.append(
                {
                    "statement_id": statement_id,
                    "entity_id": entity_id,
                    "semantic_role": str(participation["semantic_role"]),
                    "discourse_role": str(participation["discourse_role"]),
                    "semantic_sentiment": str(participation["semantic_sentiment"]),
                    "sentiment_strength": int(participation["sentiment_strength"]),
                }
            )
    quality_flags = sorted({str(value) for value in spec.get("quality_flags", [])})
    issuer_views = derive_issuer_views(entities, participations)
    document = {
        "contract_version": CONTRACT_VERSION,
        "concept_registry_version": registry.version,
        "sample_id": sample_id,
        "source_id": str(article["source_id"]),
        "source_timestamp": str(article["source_timestamp"]),
        "source_text_sha256": str(article["source_text_sha256"]),
        "envelope": envelope,
        "entities": entities,
        "statements": statements,
        "participations": participations,
        "issuer_views": issuer_views,
        "synthesis": derive_synthesis(
            entities=entities,
            statements=statements,
            participations=participations,
            issuer_views=issuer_views,
        ),
        "eligibility": derive_eligibility(
            entities=entities,
            statements=statements,
            participations=participations,
            envelope=envelope,
            quality_flags=quality_flags,
        ),
        "quality_flags": quality_flags,
        "migration": {
            "source_contract": "news_semantic_ground_truth_annotation_v3",
            "status": "review_required",
            "issues": ["manual_review_spec_not_yet_certified"],
        },
    }
    validation = validate_document(document)
    if not validation.valid:
        raise RuntimeError(f"Invalid V1 review specification for {sample_id}: {validation.issues}")
    return document


def _compile_observed_market_move_sources(spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Expand manually selected mover rows into ordinary V1 statements.

    This is review ergonomics, not semantic inference. The reviewer must supply
    both the ticker and exact source evidence. The expansion is deliberately
    fixed to a confirmed, current, neutral market observation so an already
    observed gain or loss cannot leak into issuer semantic sentiment.
    """
    sources: list[dict[str, Any]] = []
    for row in spec.get("observed_market_moves", []):
        if not isinstance(row, Mapping) or set(row) != {"ticker", "evidence"}:
            raise RuntimeError(
                "Each observed_market_moves row must contain only ticker and evidence"
            )
        evidence = row["evidence"]
        sources.append(
            {
                "statement_kind": "market_observation",
                "concept_leaf": "market.price_move_observed",
                "epistemic_status": "confirmed",
                "time_relation": "current",
                "evidence": evidence if isinstance(evidence, list) else [evidence],
                "participations": [
                    {
                        "ticker": str(row["ticker"]),
                        "semantic_role": "affected_subject",
                        "discourse_role": "none",
                        "semantic_sentiment": "neutral",
                        "sentiment_strength": 0,
                    }
                ],
            }
        )
    return sources


def _compile_envelope_decision(
    article: Mapping[str, Any],
    decision: Mapping[str, Any] | str,
) -> dict[str, Any]:
    """Compile the compact manual-review envelope DSL.

    Reviewers usually need only select an approved taxonomy value. A mapping
    remains available when exact source evidence is needed for an ambiguous
    envelope decision.
    """
    if isinstance(decision, Mapping):
        value = str(decision["value"])
        evidence = decision.get("evidence", [])
    else:
        value = str(decision)
        evidence = []
    return {
        "value": value,
        "rule_id": "manual_review_v1",
        "evidence": [_resolve_evidence(article, item) for item in evidence],
    }


def compile_approved_draft(
    article: Mapping[str, Any],
    draft: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompile a manually approved V1 draft for certification.

    This path is intentionally narrow: the reviewer may approve an unchanged
    V1 semantic draft, but the compiler still refreshes typed facts, the active
    concept-registry version, issuer views, synthesis and eligibility. It never
    copies prior annotation fields into the certified authority. Temporary
    migration provenance remains only until the certifier replaces it with the
    required certification provenance.
    """
    if str(article.get("sample_id")) != str(draft.get("sample_id")):
        raise RuntimeError("Approved-draft sample identity mismatch")
    registry = ConceptRegistry.load()
    document = deepcopy(dict(draft))
    document["concept_registry_version"] = registry.version
    for decision in document.get("envelope", {}).values():
        if isinstance(decision, dict):
            decision["rule_id"] = "manual_review_v1_approved_draft"
    for statement in document.get("statements", []):
        concept = str(statement.get("concept_leaf", ""))
        if concept == registry.fallback_leaf or not registry.contains(concept):
            raise RuntimeError(f"Approved draft has unresolved concept: {concept}")
        statement["evidence_spans"] = [
            _rebind_existing_statement_evidence(article, span)
            for span in statement.get("evidence_spans", [])
        ]
        statement["typed_facts"] = extract_typed_facts(
            list(statement.get("evidence_spans", []))
        )
    entities = list(document.get("entities", []))
    statements = list(document.get("statements", []))
    participations = list(document.get("participations", []))
    issuer_views = derive_issuer_views(entities, participations)
    document["issuer_views"] = issuer_views
    document["synthesis"] = derive_synthesis(
        entities=entities,
        statements=statements,
        participations=participations,
        issuer_views=issuer_views,
    )
    document["eligibility"] = derive_eligibility(
        entities=entities,
        statements=statements,
        participations=participations,
        envelope=document["envelope"],
        quality_flags=document.get("quality_flags", []),
    )
    validation = validate_document(document)
    if not validation.valid:
        raise RuntimeError(f"Invalid manually approved V1 draft for {draft.get('sample_id')}: {validation.issues}")
    return document


def _rebind_existing_statement_evidence(
    article: Mapping[str, Any],
    span: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify an existing rendered-text span without losing its occurrence.

    Rendered products commonly repeat the same provider sentence in the teaser
    and body. The stored offset is therefore authoritative only after the
    quote is verified against the current source text and source hash.
    """
    quote = str(span.get("quote", ""))
    source_field = str(span.get("source_field", "rendered_text"))
    rendered_text = _source_value(article, "rendered_text")
    start = int(span.get("start", -1))
    if source_field == "rendered_text":
        source_text = rendered_text
        rendered_start = start
    else:
        legacy_field = (
            source_field
            if source_field.startswith("publication.")
            else f"publication.{source_field}"
        )
        source_text = _source_value(article, legacy_field)
        rendered_matches = [
            match.start() for match in re.finditer(re.escape(quote), rendered_text)
        ]
        if not rendered_matches:
            raise RuntimeError(
                "Stored statement evidence no longer matches: it is absent from rendered text; "
                f"source_field={source_field} quote={quote[:120]!r}"
            )
        # The verified source-field offset disambiguates repeated teaser/body
        # evidence. Identical rendered occurrences carry the same evidence;
        # use the first occurrence deterministically.
        rendered_start = rendered_matches[0]
    source_matches = start >= 0 and source_text[start:start + len(quote)] == quote
    if not source_matches and source_field != "rendered_text" and len(rendered_matches) == 1:
        # Some draft migrations carried stale legacy field coordinates even
        # though the evidence quote remained uniquely bound in the immutable
        # rendered product. The unique rendered occurrence is sufficient.
        source_matches = True
    if not source_matches:
        raise RuntimeError(
            "Stored statement evidence no longer matches its source field; "
            f"source_field={source_field} start={start} quote={quote[:120]!r}"
        )
    return {
        "source_field": "rendered_text",
        "start": rendered_start,
        "end": rendered_start + len(quote),
        "quote": quote,
    }


def _expand_entity(article: Mapping[str, Any], source: Mapping[str, Any] | str) -> dict[str, Any]:
    # The review DSL permits a bare ticker only as compact input. It is never
    # copied into the certified authority without a unique point-in-time match.
    row = {"ticker": source} if isinstance(source, str) else dict(source)
    ticker = str(row.get("ticker", "")).upper().strip()
    if not ticker or all(field in row for field in ("entity_id", "entity_kind", "display_name", "identity_status", "identity_evidence")):
        return row
    candidates = [
        candidate
        for candidate in article.get("point_in_time_issuer_candidates", [])
        if _candidate_symbol(candidate) == ticker
    ]
    if len(candidates) != 1:
        raise RuntimeError(f"Ticker shorthand requires exactly one point-in-time identity candidate: {ticker} matches={len(candidates)}")
    candidate = candidates[0]
    evidence = [str(value) for value in candidate.get("identity_evidence", [])]
    display_name = str(row.get("display_name") or _candidate_display_name(candidate, ticker))
    return {
        "entity_id": str(row.get("entity_id") or f"security:{ticker}"),
        "entity_kind": str(row.get("entity_kind") or "security"),
        "display_name": display_name,
        "ticker": ticker,
        "identity_status": str(row.get("identity_status") or "resolved"),
        "identity_evidence": list(row.get("identity_evidence") or evidence),
    }


def _candidate_display_name(candidate: Mapping[str, Any], ticker: str) -> str:
    for value in candidate.get("identity_evidence", []):
        text = str(value)
        if text.startswith("issuer_alias:"):
            return text.split(":", 1)[1].replace("_", " ").strip().title()
    return ticker


def _candidate_symbol(candidate: Mapping[str, Any]) -> str:
    return str(
        candidate.get("ticker")
        or candidate.get("display_symbol")
        or candidate.get("canonical_instrument_id")
        or ""
    ).upper().strip()


def _resolve_evidence(article: Mapping[str, Any], value: Any, *, statement: bool = False) -> dict[str, Any]:
    request = {"quote": value} if isinstance(value, str) else dict(value)
    quote = str(request["quote"])
    source_field = str(request.get("source_field", "rendered_text"))
    if statement and source_field != "rendered_text":
        raise RuntimeError(f"Atomic statement evidence must use rendered_text, received {source_field}")
    text = _source_value(article, source_field)
    starts = [match.start() for match in re.finditer(re.escape(quote), text)]
    occurrence = request.get("occurrence")
    if occurrence is None:
        if not starts:
            raise RuntimeError(f"Evidence quote was not found; quote={quote[:120]!r}")
        if len(starts) > 1 and statement:
            raise RuntimeError(f"Evidence quote must be unique or specify occurrence; matches={len(starts)} quote={quote[:120]!r}")
        start = starts[0]
    else:
        occurrence = int(occurrence)
        if occurrence < 1 or occurrence > len(starts):
            raise RuntimeError(f"Evidence occurrence out of range; occurrence={occurrence} matches={len(starts)}")
        start = starts[occurrence - 1]
    return {"source_field": source_field, "start": start, "end": start + len(quote), "quote": quote}


def _source_value(article: Mapping[str, Any], source_field: str) -> str:
    if source_field == "rendered_text":
        return str(article.get("rendered_product", {}).get("text", ""))
    if source_field.startswith("publication."):
        value: Any = article.get("publication", {})
        for part in source_field.split(".")[1:]:
            value = value.get(part) if isinstance(value, Mapping) else None
        if isinstance(value, list):
            return "\n".join(str(item) for item in value)
        return str(value or "")
    raise RuntimeError(f"Unsupported evidence source field: {source_field}")
