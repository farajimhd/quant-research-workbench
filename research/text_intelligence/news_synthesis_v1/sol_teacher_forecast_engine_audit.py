from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .contracts import sha256_json
from .engine import _normalize_ticker_identifier
from .sol_teacher_evaluation import load_json, write_json_atomic
from .sol_teacher_forecast_gold_review import build_review_batches


ENGINE_AUDIT_VERSION = "news_synthesis_sol_forecast_engine_audit_v1"


def create_engine_audit(
    reviewed_gold_root: Path,
    gold_review_root: Path,
    evaluation_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    gold_set = load_json(reviewed_gold_root / "reviewed_audit_set.json")
    gold_manifest = load_json(reviewed_gold_root / "manifest.json")
    evaluation_manifest = load_json(evaluation_root / "manifest.json")
    review_index = load_json(gold_review_root / "review_index.json")
    index = {str(row["unit_id"]): row for row in review_index}
    units = list(gold_set.get("units", ()))
    if set(index) != {str(row["unit_id"]) for row in units}:
        raise RuntimeError("Gold packet index and reviewed gold identities differ")

    prediction_paths = {
        path.stem: path for path in (evaluation_root / "predictions").glob("*.json")
    }
    output_root.mkdir(parents=True, exist_ok=True)
    audit_root = output_root / "audit_files"
    audit_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    packet_rows: list[dict[str, Any]] = []
    document_cache: dict[str, dict[str, Any]] = {}
    for unit in units:
        sample_id = str(unit["sample_id"])
        ticker = str(unit["ticker"])
        if sample_id not in prediction_paths:
            prediction_document: Mapping[str, Any] = {}
        else:
            prediction_document = document_cache.setdefault(
                sample_id, load_json(prediction_paths[sample_id])
            )
        context = prediction_context(prediction_document, ticker)
        predicted = str(context.get("predicted_sentiment") or "missing")
        gold = str(unit["gold_sentiment"])
        exact = predicted == gold
        record = {
            "unit_id": str(unit["unit_id"]),
            "sample_id": sample_id,
            "ticker": ticker,
            "gold_sentiment": gold,
            "predicted_sentiment": predicted,
            "exact_direction": exact,
            "predicted_forecast_eligible": bool(
                context.get("predicted_forecast_eligible", False)
            ),
            "gold_resolution": str(unit.get("gold_resolution") or ""),
            "gold_review_sha256": str(unit.get("gold_review_sha256") or ""),
            "prediction_document_sha256": (
                sha256_json(prediction_document) if prediction_document else ""
            ),
        }
        records.append(record)
        if exact:
            continue
        transition = f"{gold}_to_{predicted}"
        relative = Path("audit_files") / transition / (
            f"{sample_id}__{_safe(ticker)}.md"
        )
        source_packet_path = gold_review_root / str(index[record["unit_id"]]["relative_path"])
        source_packet = source_packet_path.read_text(encoding="utf-8")
        packet = render_mismatch_packet(source_packet, unit, record, context)
        target = output_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(packet, encoding="utf-8", newline="\n")
        packet_rows.append({
            **record,
            "transition": transition,
            "relative_path": relative.as_posix(),
            "packet_sha256": _sha256_text(packet),
            "packet_chars": len(packet),
        })

    records.sort(key=lambda row: row["unit_id"])
    packet_rows.sort(key=lambda row: row["unit_id"])
    mismatch_batches = build_review_batches(packet_rows)
    for index_value, batch in enumerate(mismatch_batches, start=1):
        batch["batch_id"] = f"M{index_value:04d}"
    metrics = _metrics(records)
    manifest = {
        "version": ENGINE_AUDIT_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "partition": "audit",
        "test_partition_read": False,
        "engine_version": str(
            evaluation_manifest.get("authority", {}).get("engine_version") or ""
        ),
        "population": {
            "articles": len(gold_set.get("articles", ())),
            "issuer_units": len(records),
            "mismatches": len(packet_rows),
            "review_batches": len(mismatch_batches),
        },
        "metrics": metrics,
        "authority": {
            "reviewed_gold_version": str(gold_manifest.get("version") or ""),
            "reviewed_audit_set_sha256": sha256_json(gold_set),
            "prediction_documents_sha256": str(
                evaluation_manifest.get("authority", {}).get(
                    "prediction_documents_sha256"
                ) or ""
            ),
            "records_sha256": sha256_json(records),
            "mismatch_index_sha256": sha256_json(packet_rows),
            "mismatch_batches_sha256": sha256_json(mismatch_batches),
            "packet_set_sha256": sha256_json([
                {"relative_path": row["relative_path"], "sha256": row["packet_sha256"]}
                for row in packet_rows
            ]),
        },
    }
    write_json_atomic(output_root / "all_predictions.json", records)
    write_json_atomic(output_root / "mismatch_index.json", packet_rows)
    write_json_atomic(output_root / "mismatch_batches.json", mismatch_batches)
    write_json_atomic(output_root / "manifest.json", manifest)
    (output_root / "SUMMARY.md").write_text(
        render_engine_audit_summary(manifest), encoding="utf-8", newline="\n"
    )
    return manifest


def prediction_context(document: Mapping[str, Any], ticker: str) -> dict[str, Any]:
    normalized = _normalize_ticker_identifier(ticker)
    entities = {
        str(row.get("entity_id") or ""): row for row in document.get("entities", ())
    }
    candidates = [
        (entity_id, entity)
        for entity_id, entity in entities.items()
        if entity.get("ticker")
        and _normalize_ticker_identifier(str(entity["ticker"])) == normalized
    ]
    if len(candidates) > 1:
        raise RuntimeError(f"Ambiguous prediction entity for ticker: {ticker}")
    if not candidates:
        return {"predicted_sentiment": "missing"}
    entity_id, entity = candidates[0]
    views = [
        row for row in document.get("issuer_views", ())
        if str(row.get("entity_id") or "") == entity_id
    ]
    if len(views) != 1:
        return {"predicted_sentiment": "missing", "entity": entity}
    view = views[0]
    statement_ids = {str(value) for value in view.get("statement_ids", ())}
    statements = [
        row for row in document.get("statements", ())
        if str(row.get("statement_id") or "") in statement_ids
    ]
    participations = [
        row for row in document.get("participations", ())
        if str(row.get("entity_id") or "") == entity_id
        and str(row.get("statement_id") or "") in statement_ids
    ]
    eligibility = [
        row for row in document.get("eligibility", ())
        if str(row.get("entity_id") or "") == entity_id
    ]
    forecast = next((bool(row.get("eligible")) for row in eligibility
                     if row.get("product") == "forecast_trigger"), False)
    return {
        "predicted_sentiment": str(view.get("composite_sentiment") or "missing"),
        "predicted_forecast_eligible": forecast,
        "entity": entity,
        "issuer_view": view,
        "statements": statements,
        "participations": participations,
        "eligibility": eligibility,
        "envelope": document.get("envelope", {}),
        "production": document.get("production", {}),
    }


def render_mismatch_packet(
    source_packet: str,
    unit: Mapping[str, Any],
    record: Mapping[str, Any],
    context: Mapping[str, Any],
) -> str:
    heading = source_packet.replace(
        "# Prediction-blind forecast gold review:",
        "# Forecast engine mismatch:",
        1,
    )
    reviewed = {
        "unit_id": unit["unit_id"],
        "original_gold_sentiment": unit.get("original_gold_sentiment"),
        "reviewed_gold_sentiment": unit.get("reviewed_gold_sentiment"),
        "resolved_gold_sentiment": unit["gold_sentiment"],
        "gold_resolution": unit.get("gold_resolution"),
        "gold_review_sha256": unit.get("gold_review_sha256"),
    }
    comparison = dict(record)
    prediction = {key: value for key, value in context.items()
                  if key not in {"predicted_sentiment", "predicted_forecast_eligible"}}
    return (
        heading.rstrip()
        + "\n\n## Reviewed gold authority\n\n```json\n"
        + json.dumps(reviewed, indent=2, ensure_ascii=False)
        + "\n```\n\n## Engine comparison\n\n```json\n"
        + json.dumps(comparison, indent=2, ensure_ascii=False)
        + "\n```\n\n## Relevant engine output\n\n```json\n"
        + json.dumps(prediction, indent=2, ensure_ascii=False)
        + "\n```\n"
    )


def render_engine_audit_summary(manifest: Mapping[str, Any]) -> str:
    metrics = manifest["metrics"]
    return (
        "# Sol forecast reviewed-gold engine audit\n\n"
        "The sealed test partition was not read.\n\n"
        f"- Audit issuer units: {metrics['units']:,}\n"
        f"- Exact direction: {metrics['exact']:,}\n"
        f"- Direction accuracy: {metrics['accuracy']:.4f}\n"
        f"- Missing predictions: {metrics['missing_predictions']:,}\n"
        f"- Mismatch packets: {manifest['population']['mismatches']:,}\n"
    )


def _metrics(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    exact = sum(bool(row["exact_direction"]) for row in records)
    confusion = Counter(
        (str(row["gold_sentiment"]), str(row["predicted_sentiment"]))
        for row in records
    )
    return {
        "units": len(records),
        "exact": exact,
        "accuracy": exact / len(records) if records else 0.0,
        "missing_predictions": sum(
            row["predicted_sentiment"] == "missing" for row in records
        ),
        "predicted_forecast_eligible": sum(
            bool(row["predicted_forecast_eligible"]) for row in records
        ),
        "confusion": [
            {"gold": gold, "predicted": predicted, "units": count}
            for (gold, predicted), count in sorted(confusion.items())
        ],
    }


def _safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "unknown"


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
