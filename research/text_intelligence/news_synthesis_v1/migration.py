from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.text_intelligence.news_synthesis_v1.contracts import (
    CONTRACT_VERSION,
    MIGRATION_VERSION,
    canonical_json,
    sha256_json,
    validate_document,
)
from research.text_intelligence.news_synthesis_v1.registry import ConceptRegistry
from research.text_intelligence.news_synthesis_v1.synthesis import (
    derive_eligibility,
    derive_issuer_views,
)
from research.text_intelligence.news_synthesis_v1.taxonomy_audit import (
    DEFAULT_COLLECTIONS,
    discover_pairs,
    load_json,
    sha256_file,
)


MONEY_RE = re.compile(r"(?<!\w)\$\s?(?P<value>\d[\d,]*(?:\.\d+)?)\s?(?P<unit>billion|million|thousand|[BMK])?\b", re.I)
PERCENT_RE = re.compile(r"(?<!\w)(?P<value>-?\d+(?:\.\d+)?)\s*(?:%|percent)\b", re.I)
DATE_RE = re.compile(r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}(?:,\s+\d{4})?\b", re.I)


@dataclass(frozen=True, slots=True)
class MigrationConfig:
    collection_roots: tuple[Path, ...]
    output_root: Path
    expected_articles: int = 2_000


def default_config() -> MigrationConfig:
    runtime_root = Path(os.environ.get("QW_MLOPS_ROOT", "D:/TradingML")) / "runtimes"
    calibration = runtime_root / "text_intelligence" / "semantic_calibration_v1"
    return MigrationConfig(
        collection_roots=tuple(calibration / name for name in DEFAULT_COLLECTIONS),
        output_root=runtime_root / "text_intelligence" / "news_synthesis_v1" / "gold_migration_v1",
    )


def migrate_record(
    annotation: Mapping[str, Any],
    article: Mapping[str, Any],
    registry: ConceptRegistry,
) -> tuple[dict[str, Any], dict[str, Any]]:
    _validate_source_identity(annotation, article)
    issues: list[str] = []
    quality_flags: set[str] = set()
    rendered_text = str(article.get("rendered_product", {}).get("text", ""))
    publication = article.get("publication", {})
    title = str(publication.get("title", ""))
    envelope = _migrate_envelope(annotation, article, title, rendered_text, issues, quality_flags)
    candidate_by_ticker = {
        str(row.get("ticker", "")).upper(): row
        for row in article.get("point_in_time_issuer_candidates", [])
        if isinstance(row, Mapping) and row.get("ticker")
    }
    entities: list[dict[str, Any]] = []
    statements: list[dict[str, Any]] = []
    participations: list[dict[str, Any]] = []
    concept_stats = Counter()
    entity_ids: dict[str, str] = {}

    for unit_index, unit in enumerate(annotation.get("issuer_units", []), start=1):
        ticker = str(unit.get("ticker", "")).upper().strip()
        if not ticker:
            issues.append(f"unit_{unit_index}:missing_ticker")
            continue
        entity_id = entity_ids.get(ticker)
        if entity_id is None:
            entity_id = f"security:{ticker}"
            entity_ids[ticker] = entity_id
            candidate = candidate_by_ticker.get(ticker)
            evidence = tuple(str(value) for value in (candidate or {}).get("identity_evidence", []))
            status = "resolved" if candidate else "unresolved"
            if not candidate:
                quality_flags.add("unresolved_identity")
                issues.append(f"identity_unresolved:{ticker}")
            entities.append(
                {
                    "entity_id": entity_id,
                    "entity_kind": "security",
                    "display_name": _display_name(evidence, ticker),
                    "ticker": ticker,
                    "identity_status": status,
                    "identity_evidence": list(evidence),
                }
            )
        spans = _validated_spans(unit, rendered_text, unit_index, issues, quality_flags)
        if not spans:
            continue
        concepts = [str(value) for value in unit.get("event_concepts", []) if str(value).strip()]
        if not concepts:
            concepts = [""]
            issues.append(f"unit_{unit_index}:missing_concept")
        sentiment_variants = _sentiment_variants(unit, unit_index, issues)
        for concept_index, legacy_concept in enumerate(concepts, start=1):
            concept_leaf, resolution_kind = registry.resolve(legacy_concept)
            concept_stats[resolution_kind] += 1
            if resolution_kind != "exact_alias":
                issues.append(f"concept_review:{legacy_concept or '<missing>'}->{concept_leaf}")
            kind = _statement_kind(unit, concept_leaf)
            epistemic = _epistemic_status(unit, unit_index, issues)
            time_relation = _time_relation(unit, unit_index, issues)
            for sentiment_index, (sentiment, strength) in enumerate(sentiment_variants, start=1):
                statement_id = f"S{unit_index:04d}.{concept_index:02d}.{sentiment_index}"
                statements.append(
                    {
                        "statement_id": statement_id,
                        "statement_kind": kind,
                        "concept_leaf": concept_leaf,
                        "epistemic_status": epistemic,
                        "time_relation": time_relation,
                        "evidence_spans": spans,
                        "typed_facts": _typed_facts(spans),
                    }
                )
                semantic_role, discourse_role = _roles(unit)
                if semantic_role == "none":
                    sentiment, strength = "neutral", 0
                participations.append(
                    {
                        "statement_id": statement_id,
                        "entity_id": entity_id,
                        "semantic_role": semantic_role,
                        "discourse_role": discourse_role,
                        "semantic_sentiment": sentiment,
                        "sentiment_strength": strength,
                    }
                )

    if annotation.get("content_role") == "analyst_event":
        issues.append("analyst_claim_source_requires_entity_migration")
    if annotation.get("extraction_decision") != "labeled" and entities:
        issues.append("non_labeled_record_contains_entities")
    issuer_views = derive_issuer_views(entities, participations)
    eligibility = derive_eligibility(
        entities=entities,
        statements=statements,
        participations=participations,
        envelope=envelope,
        quality_flags=quality_flags,
    )
    status = _migration_status(issues, quality_flags)
    document = {
        "contract_version": CONTRACT_VERSION,
        "sample_id": str(annotation["sample_id"]),
        "source_id": str(annotation["source_id"]),
        "source_timestamp": str(annotation["source_timestamp"]),
        "source_text_sha256": str(annotation["source_text_sha256"]),
        "envelope": envelope,
        "entities": entities,
        "statements": statements,
        "participations": participations,
        "issuer_views": issuer_views,
        "eligibility": eligibility,
        "quality_flags": sorted(quality_flags),
        "migration": {
            "source_contract": "news_semantic_ground_truth_annotation_v3",
            "status": status,
            "issues": sorted(set(issues)),
        },
    }
    validation = validate_document(document)
    if not validation.valid:
        document["migration"]["status"] = "rejected"
        document["migration"]["issues"] = sorted(set(document["migration"]["issues"]) | {f"schema:{value}" for value in validation.issues})
    audit = {
        "sample_id": document["sample_id"],
        "status": document["migration"]["status"],
        "issues": document["migration"]["issues"],
        "entities": len(entities),
        "statements": len(statements),
        "participations": len(participations),
        "concept_mapping": dict(concept_stats),
        "legacy_eligibility": _legacy_eligibility(annotation),
        "v1_eligibility": _v1_eligibility(document),
        "document_sha256": sha256_json(document),
    }
    return document, audit


def run_migration(config: MigrationConfig) -> dict[str, Any]:
    pairs = discover_pairs(config.collection_roots)
    if len(pairs) != config.expected_articles:
        raise RuntimeError(f"Expected {config.expected_articles} paired records, found {len(pairs)}")
    registry = ConceptRegistry.load()
    config.output_root.mkdir(parents=True, exist_ok=True)
    records_tmp = config.output_root / "news_synthesis_v1.jsonl.tmp"
    audit_tmp = config.output_root / "migration_audit.jsonl.tmp"
    status_counts = Counter()
    issue_counts = Counter()
    concept_counts = Counter()
    eligibility = Counter()
    source_files = []
    with records_tmp.open("w", encoding="utf-8", newline="\n") as record_handle, audit_tmp.open("w", encoding="utf-8", newline="\n") as audit_handle:
        for annotation_path, article_path, collection in pairs:
            annotation, article = load_json(annotation_path), load_json(article_path)
            document, audit = migrate_record(annotation, article, registry)
            record_handle.write(canonical_json(document) + "\n")
            audit_handle.write(canonical_json(audit) + "\n")
            status_counts[audit["status"]] += 1
            issue_counts.update(audit["issues"])
            concept_counts.update(audit["concept_mapping"])
            for product, old_value in audit["legacy_eligibility"].items():
                new_value = audit["v1_eligibility"].get(product)
                eligibility[(product, old_value, new_value)] += 1
            source_files.append({"collection": collection, "annotation": sha256_file(annotation_path), "article": sha256_file(article_path)})
    records_path = config.output_root / "news_synthesis_v1.jsonl"
    audit_path = config.output_root / "migration_audit.jsonl"
    records_tmp.replace(records_path)
    audit_tmp.replace(audit_path)
    manifest = {
        "migration_version": MIGRATION_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract_version": CONTRACT_VERSION,
        "concept_registry_version": registry.version,
        "records": len(pairs),
        "status_counts": dict(status_counts),
        "top_issues": [{"issue": key, "count": value} for key, value in issue_counts.most_common(100)],
        "concept_mapping": dict(concept_counts),
        "eligibility_comparison": [
            {"product": key[0], "legacy": key[1], "v1": key[2], "count": count}
            for key, count in sorted(
                eligibility.items(),
                key=lambda row: (
                    row[0][0],
                    str(row[0][1]),
                    str(row[0][2]),
                ),
            )
        ],
        "source_files_sha256": sha256_json(source_files),
        "records_sha256": sha256_file(records_path),
        "audit_sha256": sha256_file(audit_path),
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    (config.output_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (config.output_root / "migration_report.md").write_text(render_report(manifest), encoding="utf-8")
    return manifest


def _validate_source_identity(
    annotation: Mapping[str, Any], article: Mapping[str, Any]
) -> None:
    if annotation.get("annotation_version") != "news_semantic_ground_truth_annotation_v3":
        raise RuntimeError(
            f"Unsupported source contract for {annotation.get('sample_id', '<unknown>')}: "
            f"{annotation.get('annotation_version')}"
        )
    for field in ("sample_id", "source_id", "source_timestamp", "source_text_sha256"):
        if annotation.get(field) != article.get(field):
            raise RuntimeError(
                f"Source identity mismatch for {annotation.get('sample_id', '<unknown>')}: {field}"
            )


def render_report(manifest: Mapping[str, Any]) -> str:
    lines = [
        "# News Synthesis V1 Draft Gold Migration",
        "",
        f"Records: **{manifest['records']:,}**",
        "",
        "## Migration status",
        "",
        "| Status | Records |",
        "|---|---:|",
    ]
    for status, count in sorted(manifest["status_counts"].items()):
        lines.append(f"| `{status}` | {count:,} |")
    lines.extend(("", "## Concept mapping", "", "| Mapping | Statements |", "|---|---:|"))
    for key, count in sorted(manifest["concept_mapping"].items()):
        lines.append(f"| `{key}` | {count:,} |")
    lines.extend(("", "## Eligibility comparison", "", "| Product | V3 | V1 | Articles |", "|---|---|---|---:|"))
    for row in manifest["eligibility_comparison"]:
        lines.append(f"| `{row['product']}` | {row['legacy']} | {row['v1']} | {row['count']:,} |")
    lines.extend(("", "## Highest-frequency review issues", "", "| Issue | Records |", "|---|---:|"))
    for row in manifest["top_issues"][:30]:
        lines.append(f"| `{row['issue']}` | {row['count']:,} |")
    lines.extend(("", "This is a non-destructive draft. V3 remains authoritative until every `review_required` record is manually certified.", ""))
    return "\n".join(lines)


def _migrate_envelope(annotation: Mapping[str, Any], article: Mapping[str, Any], title: str, text: str, issues: list[str], flags: set[str]) -> dict[str, Any]:
    role = str(annotation.get("content_role", ""))
    issuer_units = annotation.get("issuer_units", [])
    structure = {
        "market_roundup": "market_overview",
        "mover_recap": "multi_subject_digest",
        "automated_summary": "multi_subject_digest" if len(issuer_units) > 1 else "single_subject",
    }.get(role, "single_subject")
    purpose = {
        "market_roundup": "recap",
        "mover_recap": "recap",
        "automated_summary": "recap",
        "why_moving_followup": "explain_move",
        "preview": "preview",
        "editorial_analysis": "analyze",
        "analyst_event": "analyze",
    }.get(role, "report")
    origin_value = {
        "issuer_direct": "issuer",
        "regulatory_primary": "regulator",
        "analyst_research": "analyst",
        "editorial_original": "editorial",
        "editorial_aggregation": "editorial",
        "automated_summary": "editorial",
    }.get(str(annotation.get("source_origin", "")), "unknown")
    source_origin = str(annotation.get("source_origin", ""))
    production = "automated" if source_origin == "automated_summary" or role == "automated_summary" else "aggregated" if source_origin == "editorial_aggregation" else "original" if source_origin == "editorial_original" else "unknown"
    quality = set(article.get("publication", {}).get("content_quality_flags", [])) | set(article.get("rendered_product", {}).get("quality_flags", []))
    if text:
        availability = "rendered"
    elif title:
        availability = "title_only"
        flags.add("title_only_text")
    elif "invalid" in " ".join(quality).lower():
        availability = "invalid"
        flags.add("invalid_text")
    else:
        availability = "unrendered"
        flags.add("unrendered_text")
    issues.append(f"envelope_rule_mapped:{role or '<missing>'}")
    if role != "preview":
        issues.append(f"envelope_review_required:{role or '<missing>'}")
    if production == "unknown":
        issues.append("production_method_requires_provenance_review")
    evidence = _title_evidence(title, text)
    return {
        "document_structure": _decision(structure, "migration_v1:content_role_to_structure", evidence),
        "communication_purpose": _decision(purpose, "migration_v1:content_role_to_purpose", evidence),
        "information_origin": _decision(origin_value, "migration_v1:source_origin_to_information_origin", evidence),
        "production_method": _decision(production, "migration_v1:source_origin_to_production_method", evidence),
        "text_availability": _decision(availability, "migration_v1:rendered_product_presence", evidence),
    }


def _decision(value: str, rule_id: str, evidence: list[dict[str, Any]]) -> dict[str, Any]:
    return {"value": value, "rule_id": rule_id, "evidence": evidence}


def _title_evidence(title: str, text: str) -> list[dict[str, Any]]:
    if not title:
        return []
    start = text.find(title)
    if start < 0:
        return []
    return [{"source_field": "rendered_text", "start": start, "end": start + len(title), "quote": title}]


def _validated_spans(unit: Mapping[str, Any], text: str, index: int, issues: list[str], flags: set[str]) -> list[dict[str, Any]]:
    rows = []
    for span_index, span in enumerate(unit.get("evidence_spans", []), start=1):
        try:
            start, end, quote = int(span["start"]), int(span["end"]), str(span["quote"])
            source_field = str(span.get("source_field", "rendered_text"))
        except (KeyError, TypeError, ValueError):
            issues.append(f"unit_{index}:invalid_evidence_{span_index}")
            continue
        if source_field == "rendered_text" and text[start:end] != quote:
            issues.append(f"unit_{index}:evidence_mismatch_{span_index}")
            flags.add("evidence_integrity_failure")
            continue
        rows.append({"source_field": source_field, "start": start, "end": end, "quote": quote})
    if not rows:
        issues.append(f"unit_{index}:no_valid_evidence")
        flags.add("missing_evidence")
    return rows


def _display_name(evidence: Sequence[str], ticker: str) -> str:
    aliases = [value.split(":", 1)[1] for value in evidence if value.startswith("issuer_alias:")]
    return max(aliases, key=len) if aliases else ticker


def _roles(unit: Mapping[str, Any]) -> tuple[str, str]:
    role = str(unit.get("issuer_role", ""))
    if role == "mentioned_subject":
        return "none", "context_mention"
    return ({"acquirer": "acquirer", "target": "target", "counterparty": "counterparty"}.get(role, "affected_subject"), "none")


def _sentiment_variants(unit: Mapping[str, Any], index: int, issues: list[str]) -> list[tuple[str, int]]:
    old = str(unit.get("semantic_direction", "neutral"))
    positive = max(0, min(4, int(unit.get("positive_evidence_level", 0) or 0)))
    negative = max(0, min(4, int(unit.get("negative_evidence_level", 0) or 0)))
    if old == "mixed":
        issues.append(f"unit_{index}:mixed_sentiment_decomposed")
        rows = []
        if positive:
            rows.append(("positive", positive))
        if negative:
            rows.append(("negative", negative))
        return rows or [("neutral", 0)]
    if old == "positive":
        return [("positive", positive or 1)]
    if old == "negative":
        return [("negative", negative or 1)]
    return [("neutral", 0)]


def _epistemic_status(unit: Mapping[str, Any], index: int, issues: list[str]) -> str:
    value = str(unit.get("modality", "confirmed"))
    if value in {"confirmed", "planned", "expected", "rumored"}:
        return value
    if value == "opinion":
        issues.append(f"unit_{index}:opinion_mapped_to_confirmed_assessment")
        return "confirmed"
    issues.append(f"unit_{index}:mixed_epistemic_requires_review")
    return "confirmed"


def _time_relation(unit: Mapping[str, Any], index: int, issues: list[str]) -> str:
    value = str(unit.get("time_orientation", "current"))
    if value in {"historical", "current", "forward"}:
        return value
    issues.append(f"unit_{index}:mixed_time_requires_review")
    return "current"


def _statement_kind(unit: Mapping[str, Any], concept: str) -> str:
    role = str(unit.get("issuer_role", ""))
    modality = str(unit.get("modality", ""))
    time_relation = str(unit.get("time_orientation", ""))
    if role == "mentioned_subject":
        return "background"
    if concept.startswith("market."):
        return "market_observation"
    if modality == "opinion" or concept.startswith("analyst."):
        return "assessment"
    if time_relation == "forward" and modality in {"expected", "planned"}:
        return "forecast"
    return "event"


def _typed_facts(spans: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    facts = []
    for span in spans:
        quote = str(span["quote"])
        for match in MONEY_RE.finditer(quote):
            facts.append({"fact_kind": "money", "raw": match.group(0), "value": match.group("value"), "unit": (match.group("unit") or "currency_units").lower()})
        for match in PERCENT_RE.finditer(quote):
            facts.append({"fact_kind": "percentage", "raw": match.group(0), "value": match.group("value")})
        for match in DATE_RE.finditer(quote):
            facts.append({"fact_kind": "date", "raw": match.group(0)})
    return facts


def _migration_status(issues: Sequence[str], flags: set[str]) -> str:
    if {"evidence_integrity_failure", "missing_evidence"} & flags:
        return "rejected"
    review_prefixes = (
        "concept_review:",
        "identity_unresolved:",
        "analyst_claim_source",
        "envelope_review_required:",
        "production_method_requires_provenance_review",
        "unit_",
    )
    return "review_required" if any(issue.startswith(review_prefixes) for issue in issues) else "rule_mapped"


def _legacy_eligibility(annotation: Mapping[str, Any]) -> dict[str, bool]:
    units = annotation.get("issuer_units", [])
    return {
        "forecast_trigger": any(bool(row.get("forecast_trigger_eligible")) for row in units),
        "reaction_study": any(bool(row.get("reaction_evaluation_eligible")) for row in units),
        "issuer_history": any(bool(row.get("issuer_history_context_eligible")) for row in units),
        "analyst_evaluation": any(bool(row.get("analyst_evaluation_eligible")) for row in units),
    }


def _v1_eligibility(document: Mapping[str, Any]) -> dict[str, bool]:
    by_product: dict[str, bool] = defaultdict(bool)
    for row in document["eligibility"]:
        by_product[str(row["product"])] |= bool(row["eligible"])
    return dict(by_product)
