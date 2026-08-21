from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path


REVIEW_VERSION = "news_synthesis_funnel_holdout_review_v1"
ARTICLE_LABELS = frozenset(("eligible", "ineligible", "insufficient_information"))
SENTIMENTS = frozenset(("positive", "negative", "mixed", "neutral", "insufficient_information"))
CONTEXT_CLASSES = frozenset((
    "forecast_event", "analyst_opinion", "market_context", "trading_activity",
    "routine_communication", "other_context", "insufficient_information",
))


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    count = 0
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
            count += 1
    return count


def _review_id(source_id: str) -> str:
    return "H" + hashlib.sha256(f"{REVIEW_VERSION}\0{source_id}".encode()).hexdigest()[:24]


def prepare_review(root: Path, *, shards: int = 3) -> dict[str, Any]:
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    if manifest.get("status") != "sealed_unlabeled":
        raise RuntimeError("holdout is not sealed_unlabeled")
    sample = root / "SEALED_SAMPLE.jsonl"
    expected = manifest["files"][sample.name]
    if sha256_path(sample) != expected["sha256"]:
        raise RuntimeError("sealed sample hash mismatch")
    rows = list(iter_jsonl(sample))
    if len(rows) != int(expected["rows"]):
        raise RuntimeError("sealed sample row-count mismatch")
    review_root = root / "gold_review_v1"
    review_root.mkdir()
    controller = []
    packets: list[list[dict[str, Any]]] = [[] for _ in range(shards)]
    for index, row in enumerate(rows):
        review_id = _review_id(str(row["source_id"]))
        controller.append({
            "review_id": review_id, "source_id": row["source_id"],
            "rendered_text_hash": row["rendered_text_hash"], "sample_index": index,
        })
        packets[index % shards].append({
            "review_id": review_id,
            "published_at_utc": row["published_at_utc"],
            "provider": row["provider"],
            "title": row["title"],
            "rendered_text": row["rendered_text"],
            "rendered_text_hash": row["rendered_text_hash"],
            "tickers": row["tickers"],
            "channels": row["channels"],
            "provider_tags": row["provider_tags"],
        })
    controller_path = review_root / "CONTROLLER.jsonl"
    _write_jsonl(controller_path, controller)
    outputs: dict[str, Any] = {controller_path.name: {"rows": len(controller), "sha256": sha256_path(controller_path)}}
    for index, packet in enumerate(packets):
        path = review_root / f"PACKET_{index}.jsonl"
        _write_jsonl(path, packet)
        outputs[path.name] = {"rows": len(packet), "sha256": sha256_path(path)}
    instructions = """# Blind held-out review contract

Use only the assigned packet. Do not inspect predictions, source IDs, other reviews, reports, code, or external sources.

Eligibility requires a resolved focal tradable issuer, substantive supported evidence, a current issuer event or issuer forward guidance, positive or negative economic implication, report/news purpose, and non-analyst origin. Analyst opinions, price-target/rating actions, market/technical/options activity, historical-only summaries, scheduled previews, routine communications, unresolved identity, counterparty-only events, and evidence without directional implication are forecast-ineligible. A provider/company announcement can qualify when substantive and directional.

Write one JSON object per input row in identical order with: `review_id`; `article_label` (`eligible`, `ineligible`, or `insufficient_information`); `context_class` (`forecast_event`, `analyst_opinion`, `market_context`, `trading_activity`, `routine_communication`, `other_context`, or `insufficient_information`); `forecast_sentiment` (`positive`, `negative`, `mixed`, `neutral`, or `insufficient_information`); `context_sentiment` (same sentiment vocabulary); `ticker_labels` (one item for every supplied ticker, preserving order, with `ticker`, `forecast_eligibility`, and `sentiment`); `confidence_probability` (0..1); `evidence_excerpt` (a nonempty exact substring of rendered_text); `rationale`; and `isolation_attestation` exactly `{\"used_only_supplied_packet\":true,\"used_external_context\":false}`. Article eligibility is eligible iff at least one ticker is eligible. Ineligible ticker forecast sentiment must be neutral; use context_sentiment for analyst/market tone.
"""
    instructions_path = review_root / "INSTRUCTIONS.md"
    instructions_path.write_text(instructions, encoding="utf-8")
    report = {"review_version": REVIEW_VERSION, "status": "prepared", "articles": len(rows), "shards": shards, "outputs": outputs}
    (review_root / "PREPARE_MANIFEST.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def _validated_reviews(packet: list[dict[str, Any]], path: Path) -> dict[str, dict[str, Any]]:
    rows = list(iter_jsonl(path))
    expected_ids = [str(row["review_id"]) for row in packet]
    if [str(row.get("review_id") or "") for row in rows] != expected_ids:
        raise ValueError(f"review identity/order mismatch: {path}")
    packet_by_id = {str(row["review_id"]): row for row in packet}
    for row in rows:
        review_id = str(row["review_id"])
        label = row.get("article_label")
        if label not in ARTICLE_LABELS or row.get("context_class") not in CONTEXT_CLASSES:
            raise ValueError(f"invalid article label/context: {review_id}")
        if row.get("forecast_sentiment") not in SENTIMENTS or row.get("context_sentiment") not in SENTIMENTS:
            raise ValueError(f"invalid sentiment: {review_id}")
        expected_tickers = [str(value) for value in packet_by_id[review_id]["tickers"]]
        ticker_rows = row.get("ticker_labels")
        if not isinstance(ticker_rows, list) or [str(value.get("ticker") or "") for value in ticker_rows] != expected_tickers:
            raise ValueError(f"ticker identity/order mismatch: {review_id}")
        if any(value.get("forecast_eligibility") not in ARTICLE_LABELS or value.get("sentiment") not in SENTIMENTS for value in ticker_rows):
            raise ValueError(f"invalid ticker label: {review_id}")
        any_ticker_eligible = any(value["forecast_eligibility"] == "eligible" for value in ticker_rows)
        if (label == "eligible") != any_ticker_eligible:
            raise ValueError(f"article/ticker eligibility mismatch: {review_id}")
        if any(value["forecast_eligibility"] == "ineligible" and value["sentiment"] != "neutral" for value in ticker_rows):
            raise ValueError(f"ineligible ticker has forecast sentiment: {review_id}")
        if (label == "eligible") != (row.get("context_class") == "forecast_event"):
            raise ValueError(f"article/context-class mismatch: {review_id}")
        confidence = row.get("confidence_probability")
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise ValueError(f"invalid confidence: {review_id}")
        excerpt = str(row.get("evidence_excerpt") or "")
        if not excerpt or excerpt not in str(packet_by_id[review_id]["rendered_text"]):
            raise ValueError(f"evidence is not an exact substring: {review_id}")
        if row.get("isolation_attestation") != {"used_only_supplied_packet": True, "used_external_context": False}:
            raise ValueError(f"invalid isolation attestation: {review_id}")
    return {str(row["review_id"]): row for row in rows}


def _decision_signature(row: Mapping[str, Any]) -> str:
    return canonical_json({
        "article_label": row["article_label"],
        "context_class": row["context_class"],
        "forecast_sentiment": row["forecast_sentiment"],
        "context_sentiment": row["context_sentiment"],
        "ticker_labels": row["ticker_labels"],
    })


def prepare_disagreements(root: Path, shard: int, first_path: Path, second_path: Path) -> dict[str, Any]:
    review_root = root / "gold_review_v1"
    packet = list(iter_jsonl(review_root / f"PACKET_{shard}.jsonl"))
    first = _validated_reviews(packet, first_path)
    second = _validated_reviews(packet, second_path)
    disagreements = [
        row for row in packet
        if _decision_signature(first[str(row["review_id"])])
        != _decision_signature(second[str(row["review_id"])])
    ]
    path = review_root / f"ADJUDICATION_PACKET_{shard}.jsonl"
    _write_jsonl(path, disagreements)
    report = {"shard": shard, "articles": len(packet), "disagreements": len(disagreements), "packet": str(path), "sha256": sha256_path(path)}
    (review_root / f"ADJUDICATION_MANIFEST_{shard}.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def finalize_gold(root: Path, assignments: list[tuple[Path, Path, Path]]) -> dict[str, Any]:
    review_root = root / "gold_review_v1"
    controller = {str(row["review_id"]): row for row in iter_jsonl(review_root / "CONTROLLER.jsonl")}
    final_rows = []
    disagreement_count = 0
    for shard, (first_path, second_path, third_path) in enumerate(assignments):
        packet = list(iter_jsonl(review_root / f"PACKET_{shard}.jsonl"))
        first = _validated_reviews(packet, first_path)
        second = _validated_reviews(packet, second_path)
        disagreement_packet = [
            row for row in packet
            if _decision_signature(first[str(row["review_id"])])
            != _decision_signature(second[str(row["review_id"])])
        ]
        third = _validated_reviews(disagreement_packet, third_path)
        disagreement_count += len(disagreement_packet)
        for item in packet:
            review_id = str(item["review_id"])
            agreed = _decision_signature(first[review_id]) == _decision_signature(second[review_id])
            chosen = first[review_id] if agreed else third[review_id]
            final_rows.append({**controller[review_id], **{key: value for key, value in chosen.items() if key != "review_id"}, "decision_source": "reader_agreement" if agreed else "blind_adjudication"})
    final_rows.sort(key=lambda row: int(row["sample_index"]))
    gold_path = review_root / "GOLD_LABELS.jsonl"
    _write_jsonl(gold_path, final_rows)
    report = {
        "review_version": REVIEW_VERSION, "status": "gold_complete", "articles": len(final_rows),
        "disagreements": disagreement_count,
        "article_labels": dict(sorted(Counter(str(row["article_label"]) for row in final_rows).items())),
        "gold_path": str(gold_path), "gold_sha256": sha256_path(gold_path),
    }
    (review_root / "GOLD_MANIFEST.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report
