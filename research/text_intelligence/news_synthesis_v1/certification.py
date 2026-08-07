from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from research.text_intelligence.news_synthesis_v1.contracts import (
    CERTIFICATION_VERSION,
    canonical_json,
    sha256_json,
    validate_document,
)
from research.text_intelligence.news_synthesis_v1.registry import ConceptRegistry
from research.text_intelligence.news_synthesis_v1.source_authority import (
    default_source_authority_config,
    discover_pairs,
    load_json,
    sha256_file,
)


@dataclass(frozen=True, slots=True)
class CertificationConfig:
    draft_path: Path | None
    collection_roots: tuple[Path, ...]
    output_root: Path
    expected_articles: int = 2_000


def default_certification_config() -> CertificationConfig:
    authority = default_source_authority_config()
    news_root = authority.runtime_root / "text_intelligence" / "news_synthesis_v1"
    return CertificationConfig(
        draft_path=None,
        collection_roots=authority.collection_roots,
        output_root=news_root / "manual_certification_v1",
    )


def initialize_workspace(config: CertificationConfig) -> dict[str, Any]:
    if config.draft_path is not None:
        drafts = _load_drafts(config.draft_path)
        input_authority = "explicit_bootstrap_draft"
        input_authority_sha256 = sha256_file(config.draft_path)
    else:
        drafts = {
            path.stem: load_json(path)
            for path in (config.output_root / "certified_labels").glob("*.json")
        }
        input_authority = "certified_labels"
        input_authority_sha256 = sha256_json(
            [
                {"sample_id": sample_id, "sha256": sha256_json(drafts[sample_id])}
                for sample_id in sorted(drafts, key=_sample_sort_key)
            ]
        )
    pairs = discover_pairs(config.collection_roots)
    if len(drafts) != config.expected_articles or len(pairs) != config.expected_articles:
        raise RuntimeError(
            f"Certification authority mismatch: drafts={len(drafts)} pairs={len(pairs)} expected={config.expected_articles}"
        )
    articles: dict[str, dict[str, Any]] = {}
    source_files = []
    for _annotation_path, article_path, collection in pairs:
        article = load_json(article_path)
        sample_id = str(article["sample_id"])
        if sample_id in articles:
            raise RuntimeError(f"Duplicate article sample: {sample_id}")
        articles[sample_id] = article
        source_files.append(
            {"sample_id": sample_id, "collection": collection, "article_sha256": sha256_file(article_path)}
        )
    if drafts.keys() != articles.keys():
        raise RuntimeError("Draft and source article identities differ")
    if config.draft_path is None:
        for sample_id in sorted(drafts, key=_sample_sort_key):
            validate_certified_document(drafts[sample_id], articles[sample_id])

    review_root = config.output_root / "review_packets"
    certified_root = config.output_root / "certified_labels"
    review_root.mkdir(parents=True, exist_ok=True)
    certified_root.mkdir(parents=True, exist_ok=True)
    ledger_rows = []
    for sample_id in sorted(drafts, key=_sample_sort_key):
        draft, article = drafts[sample_id], articles[sample_id]
        _validate_identity(draft, article)
        packet_path = review_root / f"{sample_id}.md"
        packet_path.write_text(render_review_packet(article, draft), encoding="utf-8")
        certified_path = certified_root / f"{sample_id}.json"
        ledger_rows.append(
            {
                "sample_id": sample_id,
                "source_id": draft["source_id"],
                "status": "certified" if certified_path.is_file() else "pending",
                "draft_sha256": sha256_json(draft),
                "review_packet_sha256": sha256_file(packet_path),
                "certified_sha256": sha256_file(certified_path) if certified_path.is_file() else "",
            }
        )
    ledger_path = config.output_root / "certification_ledger.jsonl"
    _atomic_jsonl(ledger_path, ledger_rows)
    manifest = {
        "certification_version": CERTIFICATION_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_articles": config.expected_articles,
        "input_authority": input_authority,
        "input_authority_sha256": input_authority_sha256,
        "source_files_sha256": sha256_json(source_files),
        "review_packets": len(ledger_rows),
        "certified": sum(row["status"] == "certified" for row in ledger_rows),
        "pending": sum(row["status"] == "pending" for row in ledger_rows),
        "ledger_sha256": sha256_file(ledger_path),
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    (config.output_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def certify_document(
    config: CertificationConfig,
    sample_id: str,
    document: Mapping[str, Any],
    *,
    reviewer: str,
    review_notes: str,
) -> dict[str, Any]:
    sources = _load_source_articles(config)
    return _certify_document(config, sample_id, document, reviewer=reviewer, review_notes=review_notes, source=sources.get(sample_id))


def certify_documents(
    config: CertificationConfig,
    reviews: list[Mapping[str, Any]],
    *,
    reviewer: str,
) -> list[dict[str, Any]]:
    sources = _load_source_articles(config)
    results = []
    for review in reviews:
        sample_id = str(review["sample_id"])
        results.append(
            _prepare_certified_document(
                sample_id,
                review["document"],
                reviewer=reviewer,
                review_notes=str(review["review_notes"]),
                source=sources.get(sample_id),
            )
        )
    temporary_paths = []
    for clean in results:
        target = config.output_root / "certified_labels" / f"{clean['sample_id']}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary_paths.append((temporary, target))
    for temporary, target in temporary_paths:
        temporary.replace(target)
    refresh_certification_state(config)
    return results


def _certify_document(
    config: CertificationConfig,
    sample_id: str,
    document: Mapping[str, Any],
    *,
    reviewer: str,
    review_notes: str,
    source: Mapping[str, Any] | None,
) -> dict[str, Any]:
    clean = _prepare_certified_document(
        sample_id,
        document,
        reviewer=reviewer,
        review_notes=review_notes,
        source=source,
    )
    target = config.output_root / "certified_labels" / f"{sample_id}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(clean, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(target)
    return clean


def _prepare_certified_document(
    sample_id: str,
    document: Mapping[str, Any],
    *,
    reviewer: str,
    review_notes: str,
    source: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if document.get("sample_id") != sample_id:
        raise RuntimeError("Certification sample identity mismatch")
    if source is None:
        raise RuntimeError(f"Missing preserved source article for {sample_id}")
    _validate_identity(document, source)
    _validate_source_bound_semantics(document, source)
    clean = dict(document)
    clean.pop("migration", None)
    clean["certification"] = {
        "certification_version": CERTIFICATION_VERSION,
        "reviewer": reviewer,
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "review_notes": review_notes,
        "status": "certified",
    }
    validation = validate_document(clean)
    if not validation.valid:
        raise RuntimeError(f"Cannot certify invalid V1 document: {validation.issues}")
    if clean.get("quality_flags"):
        raise RuntimeError(f"Cannot certify with unresolved quality flags: {clean['quality_flags']}")
    return clean


def validate_certified_document(
    document: Mapping[str, Any], source: Mapping[str, Any]
) -> None:
    """Validate one stored gold document against the current complete authority."""
    validation = validate_document(document)
    if not validation.valid:
        raise RuntimeError(
            f"Invalid certified V1 document {document.get('sample_id')}: {validation.issues}"
        )
    certification = document.get("certification", {})
    if (
        certification.get("certification_version") != CERTIFICATION_VERSION
        or certification.get("status") != "certified"
    ):
        raise RuntimeError(
            f"Invalid certification provenance for {document.get('sample_id')}"
        )
    if document.get("quality_flags"):
        raise RuntimeError(
            f"Certified document has unresolved quality flags: {document.get('sample_id')}"
        )
    _validate_identity(document, source)
    _validate_source_bound_semantics(document, source)


def _validate_source_bound_semantics(document: Mapping[str, Any], article: Mapping[str, Any]) -> None:
    rendered_text = str(article.get("rendered_product", {}).get("text", ""))
    registry = ConceptRegistry.load()
    failures: list[str] = []
    if document.get("concept_registry_version") != registry.version:
        failures.append(
            f"concept_registry_version_mismatch:{document.get('concept_registry_version')}:{registry.version}"
        )
    for statement in document.get("statements", []):
        statement_id = str(statement.get("statement_id", "<missing>"))
        if not registry.contains(str(statement.get("concept_leaf", ""))):
            failures.append(f"unregistered_concept:{statement_id}:{statement.get('concept_leaf')}")
        for span in statement.get("evidence_spans", []):
            if span.get("source_field") != "rendered_text":
                failures.append(f"unsupported_statement_source:{statement_id}:{span.get('source_field')}")
                continue
            start, end = span.get("start"), span.get("end")
            if not isinstance(start, int) or not isinstance(end, int) or rendered_text[start:end] != span.get("quote"):
                failures.append(f"evidence_mismatch:{statement_id}:{start}:{end}")
    for field, decision in document.get("envelope", {}).items():
        for span in decision.get("evidence", []):
            source_field = str(span.get("source_field", ""))
            try:
                source_text = _source_value(article, source_field)
            except RuntimeError:
                failures.append(f"unsupported_envelope_source:{field}:{source_field}")
                continue
            start, end = span.get("start"), span.get("end")
            if not isinstance(start, int) or not isinstance(end, int) or source_text[start:end] != span.get("quote"):
                failures.append(f"envelope_evidence_mismatch:{field}:{start}:{end}")
    if failures:
        raise RuntimeError(f"Cannot certify source-unbound V1 document: {failures[:20]}")


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
    raise RuntimeError(source_field)


def _load_source_articles(config: CertificationConfig) -> dict[str, dict[str, Any]]:
    sources: dict[str, dict[str, Any]] = {}
    for _annotation_path, article_path, _collection in discover_pairs(config.collection_roots):
        article = load_json(article_path)
        sample_id = str(article.get("sample_id"))
        if sample_id in sources:
            raise RuntimeError(f"Duplicate preserved source article for {sample_id}")
        sources[sample_id] = article
    return sources


def refresh_certification_state(config: CertificationConfig) -> dict[str, Any]:
    ledger_path = config.output_root / "certification_ledger.jsonl"
    if not ledger_path.is_file():
        return initialize_workspace(config)
    rows = []
    with ledger_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            certified_path = config.output_root / "certified_labels" / f"{row['sample_id']}.json"
            row["status"] = "certified" if certified_path.is_file() else "pending"
            row["certified_sha256"] = sha256_file(certified_path) if certified_path.is_file() else ""
            rows.append(row)
    _atomic_jsonl(ledger_path, rows)
    manifest_path = config.output_root / "manifest.json"
    prior = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    manifest = {
        **{key: value for key, value in prior.items() if key not in {"generated_at_utc", "certified", "pending", "ledger_sha256", "manifest_sha256"}},
        "certification_version": CERTIFICATION_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "expected_articles": config.expected_articles,
        "certified": sum(row["status"] == "certified" for row in rows),
        "pending": sum(row["status"] == "pending" for row in rows),
        "ledger_sha256": sha256_file(ledger_path),
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def render_review_packet(article: Mapping[str, Any], draft: Mapping[str, Any], *, certified: bool = False) -> str:
    publication = article.get("publication", {})
    rendered = article.get("rendered_product", {})
    lines = [
        f"# {draft['sample_id']} — News Synthesis V1 {'certified review' if certified else 'certification'}",
        "",
        "## Preserved source identity",
        "",
        f"- Source ID: `{draft['source_id']}`",
        f"- Published: `{draft['source_timestamp']}`",
        f"- Text SHA-256: `{draft['source_text_sha256']}`",
        f"- Title: {publication.get('title', '')}",
        f"- Author: {publication.get('author', '')}",
        f"- Provider: {publication.get('provider', '')}",
        f"- Provider tickers: {', '.join(publication.get('provider_tickers', [])) or 'none'}",
        f"- Channels: {', '.join(publication.get('channels', [])) or 'none'}",
        f"- Provider tags: {', '.join(publication.get('provider_tags', [])) or 'none'}",
        "",
        "## Original rendered news",
        "",
        str(rendered.get("text", "")) or "[no rendered text]",
        "",
        f"## {'Certified' if certified else 'Draft'} V1 envelope",
        "",
    ]
    for field, decision in draft["envelope"].items():
        lines.append(f"- `{field}`: **{decision['value']}**")
    lines.extend(("", f"## {'Certified' if certified else 'Draft'} V1 entities", ""))
    for entity in draft["entities"]:
        lines.append(
            f"- `{entity['entity_id']}` — {entity['display_name']} — {entity['identity_status']} — "
            f"evidence: {', '.join(entity['identity_evidence']) or 'none'}"
        )
    lines.extend(("", f"## {'Certified' if certified else 'Draft'} V1 atomic statements", ""))
    participation = {(row["statement_id"], row["entity_id"]): row for row in draft["participations"]}
    for statement in draft["statements"]:
        lines.extend(
            (
                f"### {statement['statement_id']} — `{statement['concept_leaf']}`",
                "",
                f"- Kind: `{statement['statement_kind']}`",
                f"- Epistemic status: `{statement['epistemic_status']}`",
                f"- Time relation: `{statement['time_relation']}`",
            )
        )
        for entity in draft["entities"]:
            row = participation.get((statement["statement_id"], entity["entity_id"]))
            if row:
                lines.append(
                    f"- {entity.get('ticker') or entity['display_name']}: role `{row['semantic_role']}`, "
                    f"discourse `{row['discourse_role']}`, sentiment `{row['semantic_sentiment']}` "
                    f"strength `{row['sentiment_strength']}`"
                )
        for span in statement["evidence_spans"]:
            lines.append(
                f"- Evidence `{span['source_field']}:{span['start']}-{span['end']}`: “{span['quote']}”"
            )
        if statement["typed_facts"]:
            lines.append(f"- Typed facts: `{canonical_json(statement['typed_facts'])}`")
        lines.append("")
    lines.extend((f"## {'Certified' if certified else 'Draft'} V1 issuer synthesis", ""))
    for view in draft["issuer_views"]:
        lines.append(
            f"- `{view['entity_id']}`: **{view['composite_sentiment']}**, positive `{view['positive_strength']}`, "
            f"negative `{view['negative_strength']}`; statements {', '.join(view['statement_ids'])}"
        )
    lines.extend(("", f"## {'Certified' if certified else 'Draft'} V1 eligibility", ""))
    for row in draft["eligibility"]:
        lines.append(
            f"- `{row['entity_id']}` / `{row['product']}`: **{row['eligible']}** — "
            f"{'; '.join(row['reasons']) or 'no reason'}"
        )
    if certified:
        certification = draft.get("certification", {})
        lines.extend(
            (
                "",
                "## Certification result",
                "",
                f"- Reviewer: {certification.get('reviewer', '')}",
                f"- Reviewed: {certification.get('reviewed_at_utc', '')}",
                f"- Notes: {certification.get('review_notes', '')}",
                "- Unresolved quality flags: none",
                "",
                "This certified review contains only preserved source evidence and the approved News Synthesis V1 contract.",
                "",
            )
        )
        return "\n".join(lines)
    lines.extend(("", "## Certification issues to resolve", ""))
    checks = [
        "Verify the document envelope against the preserved title, metadata and full text.",
        "Verify that every substantive claim is represented by one atomic statement and exact evidence span.",
        "Verify entity identity, statement participation, semantic sentiment and strength independently.",
        "Verify typed facts, issuer synthesis and each downstream eligibility result.",
    ]
    if any(
        statement["concept_leaf"] == "unclassified.semantic_claim"
        for statement in draft["statements"]
    ):
        checks.append("Replace every unclassified concept with an approved concept leaf.")
    if any(entity["identity_status"] != "resolved" for entity in draft["entities"]):
        checks.append("Resolve or explicitly certify every non-resolved identity status.")
    if draft["envelope"]["production_method"]["value"] == "unknown":
        checks.append("Determine production method from source provenance or certify it as unknown with evidence.")
    lines.extend(f"- {check}" for check in checks)
    lines.extend(
        (
            "",
            "This packet intentionally excludes every prior V3 label field. It contains preserved source evidence and the V1 draft only.",
            "",
        )
    )
    return "\n".join(lines)


def _load_drafts(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            sample_id = str(row["sample_id"])
            if sample_id in rows:
                raise RuntimeError(f"Duplicate draft sample: {sample_id}")
            rows[sample_id] = row
    return rows


def _validate_identity(draft: Mapping[str, Any], article: Mapping[str, Any]) -> None:
    for field in ("sample_id", "source_id", "source_timestamp", "source_text_sha256"):
        if draft.get(field) != article.get(field):
            raise RuntimeError(f"Certification identity mismatch for {draft.get('sample_id')}: {field}")


def _sample_sort_key(value: str) -> tuple[str, int, str]:
    prefix = value.rstrip("0123456789")
    suffix = value[len(prefix):]
    return prefix, int(suffix) if suffix else -1, value


def _atomic_jsonl(path: Path, rows: list[Mapping[str, Any]]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
    temporary.replace(path)
