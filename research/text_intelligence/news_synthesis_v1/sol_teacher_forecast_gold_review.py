from __future__ import annotations

import hashlib
import html
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .contracts import canonical_json, sha256_json, validate_document
from .sol_teacher_evaluation import load_json, write_json_atomic


GOLD_REVIEW_VERSION = "news_synthesis_sol_forecast_gold_review_v1"


def create_gold_review_packets(
    split_root: Path,
    evaluation_root: Path,
    teacher_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    split_manifest = load_json(split_root / "split_manifest.json")
    audit_set = load_json(split_root / "audit_set.json")
    if sha256_json(audit_set) != str(
        split_manifest.get("authority", {}).get("audit_set_sha256") or ""
    ):
        raise RuntimeError("Audit set does not match the frozen split authority")
    packet_root = output_root / "audit_files"
    packet_root.mkdir(parents=True, exist_ok=True)
    index: list[dict[str, Any]] = []
    packet_hashes: dict[str, str] = {}
    for unit in audit_set["units"]:
        sample_id = str(unit["sample_id"])
        ticker = str(unit["ticker"])
        article = load_json(teacher_root / "items" / f"{sample_id}.json")
        document = load_json(
            evaluation_root / "converted_labels" / f"{sample_id}.json"
        )
        validation = validate_document(document)
        if not validation.valid:
            raise RuntimeError(
                f"Invalid converted document {sample_id}: {validation.issues}"
            )
        packet = render_gold_review_packet(article, document, unit)
        relative = Path("audit_files") / f"{sample_id}__{_safe(ticker)}.md"
        target = output_root / relative
        target.write_text(packet, encoding="utf-8", newline="\n")
        digest = hashlib.sha256(packet.encode("utf-8")).hexdigest()
        packet_hashes[str(unit["unit_id"])] = digest
        index.append(
            {
                "unit_id": str(unit["unit_id"]),
                "sample_id": sample_id,
                "ticker": ticker,
                "source_chars": len(
                    str(article.get("rendered_product", {}).get("text") or "")
                ),
                "packet_chars": len(packet),
                "relative_path": relative.as_posix(),
                "packet_sha256": digest,
            }
        )
    index.sort(key=lambda row: row["unit_id"])
    write_json_atomic(output_root / "review_index.json", index)
    manifest = {
        "version": GOLD_REVIEW_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "prediction_blind": True,
        "partition": "audit",
        "articles": len(audit_set["article_ids"]),
        "issuer_units": len(index),
        "packet_count": len(index),
        "review_status": "unreviewed",
        "authority": {
            "split_version": str(split_manifest.get("version") or ""),
            "audit_set_sha256": str(
                split_manifest.get("authority", {}).get("audit_set_sha256") or ""
            ),
            "converted_labels_sha256": str(
                split_manifest.get("authority", {}).get(
                    "converted_labels_sha256"
                )
                or ""
            ),
            "review_index_sha256": sha256_json(index),
            "packet_set_sha256": sha256_json(packet_hashes),
        },
    }
    write_json_atomic(output_root / "manifest.json", manifest)
    (output_root / "SUMMARY.md").write_text(
        render_gold_review_summary(manifest), encoding="utf-8"
    )
    return manifest


def render_gold_review_packet(
    article: Mapping[str, Any],
    document: Mapping[str, Any],
    unit: Mapping[str, Any],
) -> str:
    sample_id = str(unit["sample_id"])
    ticker = str(unit["ticker"])
    entity = next(
        row
        for row in document.get("entities", ())
        if str(row.get("ticker") or "").upper() == ticker.upper()
    )
    view = next(
        row
        for row in document.get("issuer_views", ())
        if str(row.get("entity_id")) == str(entity.get("entity_id"))
    )
    statements = {
        str(row["statement_id"]): row for row in document.get("statements", ())
    }
    evidence = [
        {
            "statement_id": statement_id,
            "concept": statements[statement_id].get("concept_leaf"),
            "statement_kind": statements[statement_id].get("statement_kind"),
            "time_relation": statements[statement_id].get("time_relation"),
            "evidence_spans": statements[statement_id].get("evidence_spans", []),
        }
        for statement_id in view.get("statement_ids", ())
        if statement_id in statements
    ]
    publication = article.get("publication", {})
    metadata = {
        "sample_id": sample_id,
        "source_id": article.get("source_id"),
        "source_timestamp": article.get("source_timestamp"),
        "source_text_sha256": article.get("source_text_sha256"),
        "title": publication.get("title"),
        "provider": publication.get("provider"),
        "provider_tickers": publication.get("provider_tickers", []),
        "channels": publication.get("channels", []),
        "content_quality_flags": publication.get("content_quality_flags", []),
    }
    gold = {
        "unit_id": unit["unit_id"],
        "ticker": ticker,
        "issuer": entity.get("display_name"),
        "identity_evidence": entity.get("identity_evidence", []),
        "sol_derived_direction": view.get("composite_sentiment"),
        "positive_strength": view.get("positive_strength"),
        "negative_strength": view.get("negative_strength"),
        "concepts": unit.get("concepts", []),
        "evidence": evidence,
        "migration_status": document.get("migration", {}).get("status"),
    }
    source_text = str(article.get("rendered_product", {}).get("text") or "")
    return "\n".join(
        (
            f"# Prediction-blind forecast gold review: {sample_id} / {ticker}",
            "",
            "## Source metadata",
            "",
            "```json",
            json.dumps(metadata, indent=2, ensure_ascii=False),
            "```",
            "",
            "## Complete rendered source text",
            "",
            "<pre>",
            html.escape(source_text),
            "</pre>",
            "",
            "## Sol-derived converted label",
            "",
            "```json",
            json.dumps(gold, indent=2, ensure_ascii=False),
            "```",
            "",
            "## Review policy",
            "",
            "- Judge the direct trading direction for this issuer from the source as a whole.",
            "- Positive and negative evidence may coexist; use mixed only when their economic importance is genuinely balanced.",
            "- Do not infer issuer impact from a contextual, peer, counterparty, or historical mention.",
            "- Mark policy_uncertain when a reasonable answer depends on an unresolved labeling boundary.",
            "- News Synthesis predictions are intentionally absent from this packet.",
            "",
            "## Required review output",
            "",
            "```json",
            json.dumps(
                {
                    "unit_id": unit["unit_id"],
                    "reviewed_direction": "positive|negative|neutral|mixed",
                    "gold_verdict": "correct|wrong|policy_uncertain",
                    "positive_strength": "0|1|2|3",
                    "negative_strength": "0|1|2|3",
                    "dominant_evidence": "",
                    "countervailing_evidence": "",
                    "issuer_attribution": "supported|unsupported|uncertain",
                    "confidence": "high|medium|low",
                    "rationale": "",
                },
                indent=2,
            ),
            "```",
            "",
        )
    )


def render_gold_review_summary(manifest: Mapping[str, Any]) -> str:
    return "\n".join(
        (
            "# Prediction-blind Sol forecast gold review",
            "",
            f"- Audit articles: {manifest['articles']:,}",
            f"- Audit issuer units: {manifest['issuer_units']:,}",
            f"- Markdown packets: {manifest['packet_count']:,}",
            "- Predictions included: no",
            f"- Review status: {manifest['review_status']}",
            "",
        )
    )


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "UNKNOWN"
