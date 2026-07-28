from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research.mlops.clickhouse import ClickHouseHttpClient
from research.mlops.paths import MLOpsPathConfig
from research.text_intelligence.candidate_inventory_v1.audit_samples import (
    AuditCase,
    document_from_case,
    fetch_news_cases,
    fetch_sec_cases,
)
from research.text_intelligence.candidate_inventory_v1.config import CandidateInventoryConfig

from .labeler import label_document
from .schema import LABEL_AUTHORITY_VERSION, SemanticDocument


AUDIT_VERSION = "text_semantic_label_audit_v1"


def create_audits(
    client: ClickHouseHttpClient,
    source_config: CandidateInventoryConfig,
    output_root: Path,
) -> list[Path]:
    assert_runtime_root(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    cases = [*fetch_news_cases(client, source_config), *fetch_sec_cases(client, source_config)]
    if len(cases) != 10:
        raise RuntimeError(f"expected ten cases, received {len(cases)}")
    files: list[Path] = []
    manifest_cases: list[dict[str, Any]] = []
    for index, case in enumerate(cases, 1):
        source = document_from_case(case)
        document = SemanticDocument(
            corpus=source.corpus,
            source_id=source.source_id,
            timestamp=source.timestamp,
            title=source.title,
            text=source.text,
            entity_terms=source.entity_terms,
            tickers=tuple(str(value) for value in source.metadata.get("tickers") or []),
            metadata=source.metadata,
        )
        result = label_document(document)
        filename = f"{index:02d}_{case.corpus}_{slug(case.stratum)}.md"
        path = output_root / filename
        write_atomic(path, render_audit(case, document, result))
        files.append(path)
        manifest_cases.append({
            "file": filename,
            "corpus": case.corpus,
            "stratum": case.stratum,
            "source_id": document.source_id,
            "source_sha256": hashlib.sha256(document.text.encode("utf-8")).hexdigest(),
            "label_count": len(result.labels),
            "span_count": len(result.spans),
            "content_role": result.content_role,
            "sentiment": result.sentiment,
            "quality_flags": result.quality_flags,
        })
        print(
            f"[{index}/10] {case.corpus.upper()} {case.stratum}"
            f" spans={len(result.spans):,} labels={len(result.labels):,} -> {filename}",
            flush=True,
        )
    manifest = {
        "audit_version": AUDIT_VERSION,
        "authority_version": LABEL_AUTHORITY_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "case_count": len(files),
        "cases": manifest_cases,
    }
    write_atomic(output_root / "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return files


def render_audit(case: AuditCase, document: SemanticDocument, result) -> str:
    suppressed = [block for block in result.blocks if not block.semantic and block.text.strip()]
    lines = [
        f"# {document.corpus.upper()} semantic authority audit — {case.stratum}",
        "",
        "## Verdict contract",
        "",
        f"- Authority: `{result.authority_version}`.",
        f"- Source: `{document.source_id}` at `{document.timestamp}`.",
        f"- Sampling rationale: {case.rationale}.",
        f"- Content role: `{result.content_role}`.",
        f"- Origin: `{result.origin}`.",
        f"- Sentiment: `{result.sentiment}` (score `{result.sentiment_score:+.2f}`).",
        f"- Modality: `{result.modality}`.",
        f"- Time orientation: `{result.time_orientation}`.",
        f"- Quality flags: {', '.join(f'`{value}`' for value in result.quality_flags) or 'none'}.",
        "",
        "Canonical labels below are deterministic outputs supported by exact retained "
        "text spans. Candidate phrases are discovery evidence only and cannot create labels.",
        "",
        "## Final cleaned labels",
        "",
        label_table(result.labels),
        "",
        "## Exact label evidence",
        "",
        evidence_table(result.labels),
        "",
        "## Detected typed spans",
        "",
        span_table(result.spans),
        "",
        "## Structural blocks",
        "",
        block_table(result.blocks),
        "",
        "## Suppressed non-semantic content",
        "",
        suppressed_table(suppressed),
        "",
        "## Normalized semantic text",
        "",
        fence(result.normalized_semantic_text),
        "",
        "## Cleaned keywords",
        "",
        ", ".join(f"`{value}`" for value in result.keywords) or "_None._",
        "",
        "## Candidate phrase evidence",
        "",
        candidate_table(result.candidates),
        "",
        "## Source metadata",
        "",
        key_value_table(document.metadata),
        "",
        "## Original rendered input",
        "",
        fence(document.text),
        "",
        "## Auditor checklist",
        "",
        "- [ ] Dates and times are whole temporal spans, not fragmented numbers.",
        "- [ ] Exchange-qualified tickers and supplied ticker identities are detected.",
        "- [ ] SEC form, item, accession, CIK, EIN, CUSIP, and ISIN identifiers remain whole.",
        "- [ ] Table quantities inherit available scale/currency context.",
        "- [ ] Contact, signature, provenance, boilerplate, and duplicate paragraphs are suppressed.",
        "- [ ] Every canonical label has exact supporting evidence.",
        "- [ ] Candidate phrases are not presented as canonical labels.",
        "- [ ] Sentiment, modality, and time orientation agree with the cited event evidence.",
        "",
    ]
    return "\n".join(lines)


def label_table(labels) -> str:
    if not labels:
        return "_No supported canonical event label._"
    lines = ["| Family | Subtype | Direction | Modality | Time | Confidence |", "|---|---|---|---|---|---:|"]
    for value in labels:
        lines.append(
            f"| `{esc(value.family)}` | `{esc(value.subtype)}` | {value.direction}"
            f" | {value.modality} | {value.time_orientation} | {value.confidence:.2f} |"
        )
    return "\n".join(lines)


def evidence_table(labels) -> str:
    rows = [
        (label.family, label.subtype, evidence)
        for label in labels for evidence in label.evidence
    ]
    if not rows:
        return "_No label evidence._"
    lines = ["| Label | Exact text | Offsets |", "|---|---|---:|"]
    for family, subtype, evidence in rows:
        lines.append(
            f"| `{family}.{subtype}` | {esc(evidence.text)}"
            f" | {evidence.start}:{evidence.end} |"
        )
    return "\n".join(lines)


def span_table(spans) -> str:
    if not spans:
        return "_No typed spans._"
    lines = [
        "| Type | Subtype | Raw | Normalized | Unit | Offsets | Context / attributes |",
        "|---|---|---|---|---|---:|---|",
    ]
    for value in spans:
        attrs = json.dumps(value.attributes, ensure_ascii=False, sort_keys=True) if value.attributes else ""
        lines.append(
            f"| {value.span_type} | `{value.subtype}` | {esc(value.raw)}"
            f" | {esc(value.normalized)} | {esc(value.unit) or '—'}"
            f" | {value.start}:{value.end} | {esc(value.context)} {esc(attrs)} |"
        )
    return "\n".join(lines)


def block_table(blocks) -> str:
    lines = ["| Kind | Semantic | Reason | Table context | Text |", "|---|---|---|---|---|"]
    for value in blocks:
        if not value.text.strip():
            continue
        table = (
            f"columns={list(value.table_columns)!r}; currency={value.table_currency or '-'};"
            f" multiplier={value.table_multiplier}"
        )
        lines.append(
            f"| {value.kind} | {'yes' if value.semantic else 'no'} | {esc(value.reason)}"
            f" | {esc(table)} | {esc(value.text[:400])} |"
        )
    return "\n".join(lines)


def suppressed_table(blocks) -> str:
    if not blocks:
        return "_No content suppressed._"
    return "\n".join(
        ["| Kind | Reason | Text |", "|---|---|---|"]
        + [f"| {value.kind} | {esc(value.reason)} | {esc(value.text[:500])} |" for value in blocks]
    )


def candidate_table(values) -> str:
    if not values:
        return "_No candidates._"
    return "\n".join(
        ["| Phrase | Tokens | Occurrences | Candidate concept |", "|---|---:|---:|---|"]
        + [
            f"| `{esc(value.phrase)}` | {value.token_count} | {value.count}"
            f" | {f'`{esc(value.seed_concept)}`' if value.seed_concept else '—'} |"
            for value in values
        ]
    )


def key_value_table(values: dict[str, Any]) -> str:
    lines = ["| Field | Value |", "|---|---|"]
    for key in sorted(values):
        value = values[key]
        rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)
        lines.append(f"| `{esc(key)}` | {esc(rendered)} |")
    return "\n".join(lines)


def fence(value: str) -> str:
    marker = "````" if "```" in value else "```"
    return f"{marker}text\n{value}\n{marker}"


def esc(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def slug(value: str) -> str:
    return "".join(character if character.isalnum() else "_" for character in value).strip("_")


def assert_runtime_root(path: Path) -> None:
    resolved = path.resolve()
    required = MLOpsPathConfig.from_env().runtimes_root.resolve()
    if required not in (resolved, *resolved.parents):
        raise RuntimeError(f"audit output must be under {required}, received {resolved}")


def write_atomic(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)
