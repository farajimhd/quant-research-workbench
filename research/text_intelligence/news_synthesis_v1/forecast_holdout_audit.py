from __future__ import annotations

import hashlib
import csv
import json
import math
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import joblib
import numpy as np
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from research.mlops.clickhouse import ClickHouseHttpClient, sql_string

from .provider_filter_analysis import (
    NEW_YORK,
    attach_text_flags,
    attach_ticker_history,
    canonical_json,
    parse_utc,
    session_date,
    session_segment,
    sha256_path,
    text_flags,
    write_json_new,
)
from .provider_market_cap_analysis import enrich_rows
from .structured_metadata_rf import _build_matrix
from .structured_rf_disagreement_audit import COMPACT_LABELS, FULL_LABELS, validate_review
from .trading_ideas_blind_audit import ALLOWED_REASONS, compact_preview


AUDIT_VERSION = "forecast_eligibility_august_2026_temporal_holdout_v1"
EXPECTED_ARTICLES = 5_044
EXPOSED_THROUGH_UTC = "2026-08-13 21:04:05"
FREEZE_THROUGH_UTC = "2026-08-23 20:28:07"
COMPACT_REVIEWERS = ("C1", "C2", "C3", "C4", "C5", "C6")
PACKET_ARTICLES = 80
PACKET_CHARACTERS = 80_000
FULL_PACKET_ARTICLES = 16
FULL_PACKET_CHARACTERS = 100_000
QA_SAMPLE_MODULUS = 10
FAST_LANE_MIN_AGREEMENT = 0.99
FAST_LANE_MIN_WILSON_LOWER = 0.98
FULL_REVIEWERS = ("F1", "F2")
SECONDARY_REVIEWERS = ("C1", "C2", "C3")
TERTIARY_REVIEWERS = ("C4", "C5", "C6")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_rows(client: ClickHouseHttpClient, sql: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]


def _write_jsonl_new(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
            count += 1
    return count


def _write_json_idempotent(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != dict(value):
            raise FileExistsError(f"existing artifact differs: {path}")
        return
    write_json_new(path, value)


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL {path}:{line_number}") from exc


def _packetize(
    rows: Sequence[dict[str, Any]], *, text_field: str = "preview_text",
    article_limit: int = PACKET_ARTICLES, character_limit: int = PACKET_CHARACTERS,
) -> list[list[dict[str, Any]]]:
    packets: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    characters = 0
    for row in rows:
        size = len(str(row[text_field]))
        if current and (len(current) >= article_limit or characters + size > character_limit):
            packets.append(current)
            current = []
            characters = 0
        current.append(row)
        characters += size
    if current:
        packets.append(current)
    return packets


def _event_sql() -> str:
    return f"""
SELECT canonical_news_id AS source_id, toString(published_at_utc) AS published_at_text,
       provider, provider_article_id, title, teaser, author, url_domain, tickers,
       channels, provider_tags, content_quality_flags, raw_payload_hash,
       source_revision_key, toString(last_updated_at_utc) AS last_updated_at_utc
FROM q_live.benzinga_news_event_v2 FINAL
PREWHERE published_date >= toDate('2026-08-01') AND published_date < toDate('2026-09-01')
WHERE published_at_utc >= toDateTime64('2026-08-01',9,'UTC')
  AND published_at_utc <= toDateTime64({sql_string(FREEZE_THROUGH_UTC)},9,'UTC')
ORDER BY published_at_utc, canonical_news_id
FORMAT JSONEachRow
"""


def _rendered_sql() -> str:
    return f"""
SELECT canonical_news_id AS source_id, toString(published_at_utc) AS published_at_text,
       rendered_text, rendered_text_hash, source_revision_key, renderer_version,
       text_contract, quality_flags
FROM q_live.benzinga_news_rendered_v2 FINAL
PREWHERE published_date >= toDate('2026-08-13') AND published_date < toDate('2026-09-01')
WHERE published_at_utc > toDateTime64({sql_string(EXPOSED_THROUGH_UTC)},9,'UTC')
  AND published_at_utc <= toDateTime64({sql_string(FREEZE_THROUGH_UTC)},9,'UTC')
  AND notEmpty(rendered_text)
ORDER BY published_at_utc, canonical_news_id
FORMAT JSONEachRow
"""


def freeze_population(*, client: ClickHouseHttpClient, output_root: Path) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    events = _json_rows(client, _event_sql())
    if len(events) != len({str(row["source_id"]) for row in events}):
        raise ValueError("duplicate event source IDs")
    rendered_rows = _json_rows(client, _rendered_sql())
    rendered = {str(row["source_id"]): row for row in rendered_rows}
    if len(rendered) != len(rendered_rows):
        raise ValueError("duplicate rendered source IDs")

    history_rows: list[dict[str, Any]] = []
    event_by_id: dict[str, dict[str, Any]] = {}
    tail_ids: set[str] = set()
    cutoff = parse_utc(EXPOSED_THROUGH_UTC)
    for source in events:
        source_id = str(source["source_id"])
        timestamp = parse_utc(str(source["published_at_text"]))
        tickers = tuple(sorted({str(value).strip().upper() for value in source.get("tickers") or () if str(value).strip()}))
        row = {
            "source_id": source_id, "published_at_utc": timestamp,
            "published_at_text": timestamp.isoformat(), "published_month": "2026-08",
            "split": "holdout_august_2026_post_cutoff", "label": "unlabeled",
            "provider": str(source.get("provider") or "").strip().casefold(),
            "tickers": tickers, "ticker_count": len(tickers),
            "provider_tags": tuple(sorted({str(value).strip().casefold() for value in source.get("provider_tags") or () if str(value).strip()})),
            "channels": tuple(sorted({str(value).strip().casefold() for value in source.get("channels") or () if str(value).strip()})),
            "content_quality_flags": tuple(sorted({str(value).strip().casefold() for value in source.get("content_quality_flags") or () if str(value).strip()})),
            "session_segment": session_segment(timestamp), "session_date": session_date(timestamp),
            "hour_et": timestamp.astimezone(NEW_YORK).hour,
            "weekday_et": timestamp.astimezone(NEW_YORK).strftime("%a").casefold(),
        }
        history_rows.append(row)
        event_by_id[source_id] = source
        if timestamp > cutoff:
            tail_ids.add(source_id)
    attach_ticker_history(history_rows)
    tail = [row for row in history_rows if str(row["source_id"]) in tail_ids]
    if len(tail) != EXPECTED_ARTICLES or set(rendered) != tail_ids:
        raise ValueError(
            f"unexpected holdout membership events={len(tail)} rendered={len(rendered)}"
        )

    flags: dict[str, dict[str, Any]] = {}
    for source_id, source in rendered.items():
        text = str(source.get("rendered_text") or "")
        digest = _digest(text)
        if digest != str(source.get("rendered_text_hash") or ""):
            raise ValueError(f"rendered hash mismatch: {source_id}")
        flags[source_id] = {
            **text_flags(text), "rendered_chars": len(text),
            "rendered_text_hash": digest,
        }
    attach_text_flags(tail, flags)
    enriched, cap_report = enrich_rows(
        {"2026-08": tail}, client=client, database="q_live",
        macro_database="market_sip_compact", macro_table="macro_bars_by_time_symbol",
        bridge_table="id_sec_market_bridge_v3", sec_table="sec_xbrl_company_fact_v3",
        snapshot_table="market_security_market_snapshot_v1",
    )

    source_rows = []
    controller_rows = []
    compact_rows = []
    for row in enriched:
        source_id = str(row["source_id"])
        event = event_by_id[source_id]
        rendered_row = rendered[source_id]
        review_id = "AH" + _digest(f"{AUDIT_VERSION}|{source_id}")[:20]
        preview = compact_preview(str(rendered_row["rendered_text"]), sentence_count=3)
        controller_rows.append({
            "review_id": review_id, "source_id": source_id,
            "published_at_utc": str(row["published_at_text"]),
            "source_revision_key": str(event["source_revision_key"]),
            "raw_payload_hash": str(event["raw_payload_hash"]),
            "rendered_text_sha256": str(rendered_row["rendered_text_hash"]),
            "preview_sha256": str(preview["preview_sha256"]),
        })
        common = {
            "review_id": review_id, "published_at_utc": str(row["published_at_text"]),
            "provider": str(row["provider"]), "tickers": list(row["tickers"]),
            "channels": list(row["channels"]), "provider_tags": list(row["provider_tags"]),
            "content_quality_flags": list(row["content_quality_flags"]),
            "session_segment": str(row["session_segment"]), "ticker_count": int(row["ticker_count"]),
            "rendered_chars": int(row["rendered_chars"]),
            "min_ticker_session_ordinal": row.get("min_ticker_session_ordinal"),
            "min_seconds_since_previous_ticker_news": row.get("min_seconds_since_previous_ticker_news"),
            "market_cap_coverage": str(row.get("market_cap_coverage") or "missing"),
            "market_cap_min_bucket": str(row.get("market_cap_min_bucket") or "missing"),
            "market_cap_max_bucket": str(row.get("market_cap_max_bucket") or "missing"),
            **preview, "rendered_text_sha256": str(rendered_row["rendered_text_hash"]),
        }
        compact_rows.append(common)
        source_rows.append({
            **{key: value for key, value in row.items() if key != "published_at_utc"},
            "published_at_utc": str(row["published_at_text"]),
            "title": str(event.get("title") or ""), "teaser": str(event.get("teaser") or ""),
            "author": str(event.get("author") or ""), "url_domain": str(event.get("url_domain") or ""),
            "source_revision_key": str(event["source_revision_key"]),
            "raw_payload_hash": str(event["raw_payload_hash"]),
            "rendered_text": str(rendered_row["rendered_text"]),
            "rendered_text_hash": str(rendered_row["rendered_text_hash"]),
        })

    output_root.mkdir(parents=True)
    _write_jsonl_new(output_root / "SOURCE_ROWS.jsonl", source_rows)
    _write_jsonl_new(output_root / "CONTROLLER.jsonl", controller_rows)
    _write_jsonl_new(output_root / "COMPACT_SAMPLE.jsonl", compact_rows)

    compact_by_id = {str(row["review_id"]): row for row in compact_rows}
    assignments: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in COMPACT_REVIEWERS}
    ordered_ids = sorted(compact_by_id, key=lambda value: _digest(f"{AUDIT_VERSION}|order|{value}"))
    for position, review_id in enumerate(ordered_ids):
        assignments[COMPACT_REVIEWERS[position % 3]].append(compact_by_id[review_id])
        assignments[COMPACT_REVIEWERS[3 + position % 3]].append(compact_by_id[review_id])
    ledger = []
    (output_root / "compact" / "assignments").mkdir(parents=True, exist_ok=True)
    for reviewer, rows in assignments.items():
        rows.sort(key=lambda row: _digest(f"{AUDIT_VERSION}|{reviewer}|{row['review_id']}"))
        for number, packet in enumerate(_packetize(rows), 1):
            packet_id = f"{reviewer}_C{number:03d}"
            path = output_root / "compact" / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(path, packet)
            ledger.append({
                "packet_id": packet_id, "reviewer_id": reviewer,
                "articles": len(packet), "packet_path": str(path),
                "review_path": str(output_root / "compact" / "reviews" / path.name),
                "packet_sha256": sha256_path(path),
            })
        write_json_new(output_root / "compact" / "assignments" / f"{reviewer}.json", {
            "audit_version": AUDIT_VERSION, "reviewer_id": reviewer,
            "packets": [item for item in ledger if item["reviewer_id"] == reviewer],
        })
    write_json_new(output_root / "COMPACT_PACKET_LEDGER.json", {"packets": ledger})
    write_json_new(output_root / "COMPACT_REVIEW_INSTRUCTIONS.json", {
        "objective": "Classify issuer forecast eligibility using only supplied metadata, title, teaser, and opening sentences.",
        "eligible": "The preview independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The preview is opinion, technical/valuation material, price movement, list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "needs_full_text": "The supplied evidence cannot safely determine whether a new material issuer event is independently reported.",
        "allowed_labels": sorted(COMPACT_LABELS), "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "required_fields": ["review_id", "manual_label", "confidence_probability", "reason_code", "rationale", "evidence_excerpt", "isolation_attestation"],
        "blindness": "Use only assigned packets. Do not inspect source/controller files, models, prior labels, other reviews, repository data, or internet sources.",
    })
    manifest = {
        "audit_version": AUDIT_VERSION, "status": "frozen_and_compact_packets_ready",
        "created_at_utc": datetime.now(UTC).isoformat(), "articles": len(source_rows),
        "exposed_through_utc": EXPOSED_THROUGH_UTC, "freeze_through_utc": FREEZE_THROUGH_UTC,
        "first_holdout_utc": min(row["published_at_utc"] for row in source_rows),
        "last_holdout_utc": max(row["published_at_utc"] for row in source_rows),
        "compact_votes_required": 2 * len(source_rows), "compact_packets": len(ledger),
        "reviewer_load": {reviewer: len(rows) for reviewer, rows in assignments.items()},
        "market_cap": cap_report,
        "artifacts": {
            name: {"sha256": sha256_path(output_root / name), "bytes": (output_root / name).stat().st_size}
            for name in ("SOURCE_ROWS.jsonl", "CONTROLLER.jsonl", "COMPACT_SAMPLE.jsonl", "COMPACT_PACKET_LEDGER.json")
        },
        "hidden_from_reviewers": ["source_id", "source_revision_key", "raw_payload_hash", "model predictions", "prior labels"],
    }
    write_json_new(output_root / "FREEZE_MANIFEST.json", manifest)
    return manifest


def collect_compact(*, output_root: Path) -> dict[str, Any]:
    ledger = json.loads((output_root / "COMPACT_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    controller = {str(row["review_id"]): row for row in _iter_jsonl(output_root / "CONTROLLER.jsonl")}
    votes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in ledger:
        packet = Path(str(item["packet_path"])); review = Path(str(item["review_path"]))
        validate_review(packet_path=packet, review_path=review)
        for row in _iter_jsonl(review):
            votes[str(row["review_id"])].append({**row, "reviewer_id": str(item["reviewer_id"])})
    if set(votes) != set(controller) or any(len(rows) != 2 for rows in votes.values()):
        raise ValueError("compact two-reviewer coverage mismatch")
    report = {
        "audit_version": AUDIT_VERSION, "status": "compact_complete",
        "articles": len(votes), "votes": sum(len(rows) for rows in votes.values()),
        "agreements": sum(len({str(row["manual_label"]) for row in rows}) == 1 for rows in votes.values()),
        "disagreements": sum(len({str(row["manual_label"]) for row in rows}) > 1 for rows in votes.values()),
        "needs_full_text_articles": sum(any(str(row["manual_label"]) == "needs_full_text" for row in rows) for rows in votes.values()),
    }
    _write_json_idempotent(output_root / "COMPACT_REVIEW_REPORT.json", report)
    return report


def _compact_votes(output_root: Path) -> dict[str, list[dict[str, Any]]]:
    ledger = json.loads((output_root / "COMPACT_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    votes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in ledger:
        for row in _iter_jsonl(Path(str(item["review_path"]))):
            votes[str(row["review_id"])].append({**row, "reviewer_id": str(item["reviewer_id"])})
    return votes


def _full_worker_row(source: Mapping[str, Any], review_id: str) -> dict[str, Any]:
    return {
        "review_id": review_id, "published_at_utc": str(source["published_at_text"]),
        "provider": str(source.get("provider") or ""), "tickers": list(source.get("tickers") or ()),
        "channels": list(source.get("channels") or ()), "provider_tags": list(source.get("provider_tags") or ()),
        "market_cap_coverage": str(source.get("market_cap_coverage") or "missing"),
        "market_cap_min_bucket": str(source.get("market_cap_min_bucket") or "missing"),
        "market_cap_max_bucket": str(source.get("market_cap_max_bucket") or "missing"),
        "rendered_text": str(source["rendered_text"]),
        "rendered_text_sha256": str(source["rendered_text_hash"]),
    }


def _write_full_instructions(output_root: Path) -> None:
    path = output_root / "FULL_REVIEW_INSTRUCTIONS.json"
    if path.exists():
        return
    write_json_new(path, {
        "objective": "Classify issuer forecast eligibility using only supplied metadata and complete rendered source text.",
        "eligible": "The article independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The article is opinion, technical/valuation material, price movement, list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "insufficient_information": "The complete rendered source still cannot establish whether a new material issuer event is reported.",
        "allowed_labels": sorted(FULL_LABELS), "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "required_fields": ["review_id", "manual_label", "confidence_probability", "reason_code", "rationale", "evidence_excerpt", "isolation_attestation"],
        "blindness": "Use only assigned full-text packets. Do not inspect compact packets or votes, controller files, labels, models, other reviews, repository data, or internet sources.",
    })


def prepare_full_primary(*, output_root: Path) -> dict[str, Any]:
    compact_report = collect_compact(output_root=output_root)
    if compact_report.get("status") != "compact_complete":
        raise ValueError("compact reviews incomplete")
    votes = _compact_votes(output_root)
    controller = {str(row["review_id"]): row for row in _iter_jsonl(output_root / "CONTROLLER.jsonl")}
    sources = {str(row["source_id"]): row for row in _iter_jsonl(output_root / "SOURCE_ROWS.jsonl")}
    selected: dict[str, list[str]] = {}
    for review_id, rows in votes.items():
        labels = {str(row["manual_label"]) for row in rows}
        reasons = []
        if len(labels) != 1:
            reasons.append("compact_disagreement")
        if "needs_full_text" in labels:
            reasons.append("compact_needs_full_text")
        if min(float(row["confidence_probability"]) for row in rows) < 0.90:
            reasons.append("compact_low_confidence")
        if len(labels) == 1 and "needs_full_text" not in labels and int(_digest(f"{AUDIT_VERSION}|qa|{review_id}")[:8], 16) % QA_SAMPLE_MODULUS == 0:
            reasons.append("fast_lane_quality_sample")
        if reasons:
            selected[review_id] = reasons
    ordered = sorted(selected, key=lambda value: _digest(f"{AUDIT_VERSION}|full-primary|{value}"))
    assignments: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in FULL_REVIEWERS}
    selection_rows = []
    for position, review_id in enumerate(ordered):
        reviewer = FULL_REVIEWERS[position % len(FULL_REVIEWERS)]
        hidden = controller[review_id]
        source = sources[str(hidden["source_id"])]
        assignments[reviewer].append(_full_worker_row(source, review_id))
        selection_rows.append({"review_id": review_id, "primary_full_reviewer": reviewer, "selection_reasons": selected[review_id]})
    _write_jsonl_new(output_root / "FULL_PRIMARY_SELECTION.jsonl", selection_rows)
    (output_root / "full_primary" / "assignments").mkdir(parents=True, exist_ok=True)
    ledger = []
    for reviewer, rows in assignments.items():
        for number, packet in enumerate(_packetize(
            rows, text_field="rendered_text", article_limit=FULL_PACKET_ARTICLES,
            character_limit=FULL_PACKET_CHARACTERS,
        ), 1):
            packet_id = f"{reviewer}_F{number:03d}"
            path = output_root / "full_primary" / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(path, packet)
            ledger.append({
                "packet_id": packet_id, "reviewer_id": reviewer, "articles": len(packet),
                "packet_path": str(path), "review_path": str(output_root / "full_primary" / "reviews" / path.name),
                "packet_sha256": sha256_path(path),
            })
        write_json_new(output_root / "full_primary" / "assignments" / f"{reviewer}.json", {
            "audit_version": AUDIT_VERSION, "reviewer_id": reviewer,
            "packets": [item for item in ledger if item["reviewer_id"] == reviewer],
        })
    write_json_new(output_root / "FULL_PRIMARY_PACKET_LEDGER.json", {"packets": ledger})
    _write_full_instructions(output_root)
    report = {
        "audit_version": AUDIT_VERSION, "status": "full_primary_ready",
        "articles": len(selected), "packets": len(ledger),
        "selection_reasons": dict(sorted(Counter(reason for reasons in selected.values() for reason in reasons).items())),
        "reviewer_load": {key: len(value) for key, value in assignments.items()},
    }
    write_json_new(output_root / "FULL_PRIMARY_PREPARE_REPORT.json", report)
    return report


def collect_full_primary(*, output_root: Path) -> dict[str, Any]:
    ledger = json.loads((output_root / "FULL_PRIMARY_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    labels: Counter[str] = Counter()
    articles = 0
    for item in ledger:
        packet = Path(str(item["packet_path"])); review = Path(str(item["review_path"]))
        validate_review(packet_path=packet, review_path=review, full_text=True)
        for row in _iter_jsonl(review):
            labels[str(row["manual_label"])] += 1
            articles += 1
    report = {"audit_version": AUDIT_VERSION, "status": "full_primary_complete", "articles": articles, "labels": dict(sorted(labels.items()))}
    _write_json_idempotent(output_root / "FULL_PRIMARY_REVIEW_REPORT.json", report)
    return report


def prepare_full_expansion(*, output_root: Path) -> dict[str, Any]:
    primary = _full_primary_votes(output_root)
    controller = {str(row["review_id"]): row for row in _iter_jsonl(output_root / "CONTROLLER.jsonl")}
    sources = {str(row["source_id"]): row for row in _iter_jsonl(output_root / "SOURCE_ROWS.jsonl")}
    remaining = sorted(set(controller) - set(primary), key=lambda value: _digest(f"{AUDIT_VERSION}|full-expansion|{value}"))
    rows = [_full_worker_row(sources[str(controller[review_id]["source_id"])], review_id) for review_id in remaining]
    (output_root / "full_expansion" / "assignments").mkdir(parents=True, exist_ok=True)
    ledger = []
    for number, packet in enumerate(_packetize(
        rows, text_field="rendered_text", article_limit=FULL_PACKET_ARTICLES,
        character_limit=FULL_PACKET_CHARACTERS,
    ), 1):
        packet_id = f"FP1_E{number:03d}"
        path = output_root / "full_expansion" / "packets" / f"{packet_id}.jsonl"
        _write_jsonl_new(path, packet)
        ledger.append({
            "packet_id": packet_id, "reviewer_id": "FP1", "articles": len(packet),
            "packet_path": str(path), "review_path": str(output_root / "full_expansion" / "reviews" / path.name),
            "packet_sha256": sha256_path(path),
        })
    write_json_new(output_root / "FULL_EXPANSION_PACKET_LEDGER.json", {"packets": ledger})
    write_json_new(output_root / "full_expansion" / "assignments" / "FP1.json", {
        "audit_version": AUDIT_VERSION, "reviewer_id": "FP1", "packets": ledger,
    })
    report = {
        "audit_version": AUDIT_VERSION, "status": "full_expansion_ready",
        "articles": len(rows), "packets": len(ledger),
        "reason": "fast-lane full-text quality sample failed certification",
    }
    write_json_new(output_root / "FULL_EXPANSION_PREPARE_REPORT.json", report)
    return report


def collect_full_expansion(*, output_root: Path) -> dict[str, Any]:
    ledger = json.loads((output_root / "FULL_EXPANSION_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    labels: Counter[str] = Counter()
    articles = 0
    for item in ledger:
        packet = Path(str(item["packet_path"])); review = Path(str(item["review_path"]))
        validate_review(packet_path=packet, review_path=review, full_text=True)
        for row in _iter_jsonl(review):
            labels[str(row["manual_label"])] += 1
            articles += 1
    report = {
        "audit_version": AUDIT_VERSION, "status": "full_expansion_complete",
        "articles": articles, "labels": dict(sorted(labels.items())),
    }
    _write_json_idempotent(output_root / "FULL_EXPANSION_REVIEW_REPORT.json", report)
    return report


def _full_primary_votes(output_root: Path) -> dict[str, dict[str, Any]]:
    ledger = json.loads((output_root / "FULL_PRIMARY_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    rows = {}
    for item in ledger:
        for row in _iter_jsonl(Path(str(item["review_path"]))):
            review_id = str(row["review_id"])
            if review_id in rows:
                raise ValueError(f"duplicate primary full review: {review_id}")
            rows[review_id] = {**row, "reviewer_id": "FP1"}
    expansion_ledger = output_root / "FULL_EXPANSION_PACKET_LEDGER.json"
    if expansion_ledger.exists():
        for item in json.loads(expansion_ledger.read_text(encoding="utf-8"))["packets"]:
            review_path = Path(str(item["review_path"]))
            if not review_path.exists():
                continue
            validate_review(
                packet_path=Path(str(item["packet_path"])), review_path=review_path,
                full_text=True,
            )
            for row in _iter_jsonl(review_path):
                review_id = str(row["review_id"])
                if review_id in rows:
                    raise ValueError(f"duplicate expansion full review: {review_id}")
                rows[review_id] = {**row, "reviewer_id": "FP1"}
    return rows


def prepare_full_secondary(*, output_root: Path) -> dict[str, Any]:
    collect_full_primary(output_root=output_root)
    compact = _compact_votes(output_root)
    primary = _full_primary_votes(output_root)
    controller = {str(row["review_id"]): row for row in _iter_jsonl(output_root / "CONTROLLER.jsonl")}
    sources = {str(row["source_id"]): row for row in _iter_jsonl(output_root / "SOURCE_ROWS.jsonl")}
    selected = {}
    for review_id, vote in primary.items():
        compact_labels = {str(row["manual_label"]) for row in compact[review_id]}
        reasons = []
        if str(vote["manual_label"]) == "insufficient_information":
            reasons.append("primary_full_insufficient")
        if float(vote["confidence_probability"]) < 0.90:
            reasons.append("primary_full_low_confidence")
        if len(compact_labels) == 1 and "needs_full_text" not in compact_labels and str(vote["manual_label"]) not in compact_labels:
            reasons.append("full_contradicts_compact_agreement")
        if reasons:
            selected[review_id] = reasons
    assignments: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in SECONDARY_REVIEWERS}
    selection_rows = []
    for review_id in sorted(selected, key=lambda value: _digest(f"{AUDIT_VERSION}|full-secondary|{value}")):
        compact_reviewers = {str(row["reviewer_id"]) for row in compact[review_id]}
        eligible_reviewers = [reviewer for reviewer in SECONDARY_REVIEWERS if reviewer not in compact_reviewers]
        if not eligible_reviewers:
            raise ValueError(f"no independent secondary reviewer: {review_id}")
        reviewer = min(eligible_reviewers, key=lambda value: (len(assignments[value]), value))
        source = sources[str(controller[review_id]["source_id"])]
        assignments[reviewer].append(_full_worker_row(source, review_id))
        selection_rows.append({"review_id": review_id, "secondary_full_reviewer": reviewer, "selection_reasons": selected[review_id]})
    _write_jsonl_new(output_root / "FULL_SECONDARY_SELECTION.jsonl", selection_rows)
    (output_root / "full_secondary" / "assignments").mkdir(parents=True, exist_ok=True)
    ledger = []
    for reviewer, rows in assignments.items():
        for number, packet in enumerate(_packetize(
            rows, text_field="rendered_text", article_limit=FULL_PACKET_ARTICLES,
            character_limit=FULL_PACKET_CHARACTERS,
        ), 1):
            packet_id = f"{reviewer}_S{number:03d}"
            path = output_root / "full_secondary" / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(path, packet)
            ledger.append({
                "packet_id": packet_id, "reviewer_id": reviewer, "articles": len(packet),
                "packet_path": str(path), "review_path": str(output_root / "full_secondary" / "reviews" / path.name),
                "packet_sha256": sha256_path(path),
            })
        write_json_new(output_root / "full_secondary" / "assignments" / f"{reviewer}.json", {
            "audit_version": AUDIT_VERSION, "reviewer_id": reviewer,
            "packets": [item for item in ledger if item["reviewer_id"] == reviewer],
        })
    write_json_new(output_root / "FULL_SECONDARY_PACKET_LEDGER.json", {"packets": ledger})
    report = {
        "audit_version": AUDIT_VERSION, "status": "full_secondary_ready",
        "articles": len(selected), "packets": len(ledger),
        "selection_reasons": dict(sorted(Counter(reason for reasons in selected.values() for reason in reasons).items())),
        "reviewer_load": {key: len(value) for key, value in assignments.items()},
    }
    write_json_new(output_root / "FULL_SECONDARY_PREPARE_REPORT.json", report)
    return report


def _full_secondary_votes(output_root: Path) -> dict[str, dict[str, Any]]:
    ledger = json.loads((output_root / "FULL_SECONDARY_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    rows = {}
    for item in ledger:
        packet = Path(str(item["packet_path"])); review = Path(str(item["review_path"]))
        validate_review(packet_path=packet, review_path=review, full_text=True)
        for row in _iter_jsonl(review):
            rows[str(row["review_id"])] = {**row, "reviewer_id": str(item["reviewer_id"])}
    return rows


def _wilson_lower(successes: int, total: int, z: float = 1.959963984540054) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    denominator = 1 + z * z / total
    center = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return (center - margin) / denominator


def finalize_labels(*, output_root: Path) -> dict[str, Any]:
    compact = _compact_votes(output_root)
    primary = _full_primary_votes(output_root)
    secondary = _full_secondary_votes(output_root)
    controller = {str(row["review_id"]): row for row in _iter_jsonl(output_root / "CONTROLLER.jsonl")}
    selection = {str(row["review_id"]): row for row in _iter_jsonl(output_root / "FULL_PRIMARY_SELECTION.jsonl")}
    qa_ids = {review_id for review_id, row in selection.items() if "fast_lane_quality_sample" in row["selection_reasons"]}
    qa_resolved = [review_id for review_id in qa_ids if str(primary[review_id]["manual_label"]) != "insufficient_information"]
    qa_agreements = sum(
        str(primary[review_id]["manual_label"]) == str(compact[review_id][0]["manual_label"])
        for review_id in qa_resolved
    )
    qa_rate = qa_agreements / len(qa_resolved) if qa_resolved else 0.0
    qa_lower = _wilson_lower(qa_agreements, len(qa_resolved))
    full_population_coverage = set(primary) == set(controller)
    certified = full_population_coverage or (
        qa_rate >= FAST_LANE_MIN_AGREEMENT and qa_lower >= FAST_LANE_MIN_WILSON_LOWER
    )
    if not certified:
        raise ValueError(
            f"fast lane failed certification agreements={qa_agreements}/{len(qa_resolved)} "
            f"rate={qa_rate:.6f} wilson_lower={qa_lower:.6f}; full expansion required"
        )
    decisions = []
    for review_id, hidden in controller.items():
        compact_votes = compact[review_id]
        first = primary.get(review_id)
        second = secondary.get(review_id)
        if first is None:
            labels = {str(row["manual_label"]) for row in compact_votes}
            if len(labels) != 1 or "needs_full_text" in labels:
                raise ValueError(f"invalid compact-only decision: {review_id}")
            final_label = labels.pop(); path = "certified_two_compact_agreement"
        elif second is None:
            label = str(first["manual_label"])
            if label == "insufficient_information":
                raise ValueError(f"unadjudicated full insufficiency: {review_id}")
            final_label = label; path = "primary_full_text_resolution"
        else:
            compact_reviewer_ids = {str(row["reviewer_id"]) for row in compact_votes}
            if str(second["reviewer_id"]) in compact_reviewer_ids:
                raise ValueError(f"secondary reviewer saw compact row: {review_id}")
            if str(second["reviewer_id"]) == str(first["reviewer_id"]):
                raise ValueError(f"secondary reviewer equals primary full reviewer: {review_id}")
            labels = {str(first["manual_label"]), str(second["manual_label"])}
            if len(labels) == 1 and "insufficient_information" not in labels:
                final_label = labels.pop(); path = "two_full_text_readers_agree"
            else:
                final_label = "unresolved"; path = "full_text_disagreement_or_insufficient"
        decisions.append({
            **hidden, "final_label": final_label, "decision_path": path,
            "compact_votes": compact_votes, "primary_full_vote": first,
            "secondary_full_vote": second,
        })
    decisions.sort(key=lambda row: str(row["source_id"]))
    _write_jsonl_new(output_root / "FINAL_LABELS.jsonl", decisions)
    report = {
        "audit_version": AUDIT_VERSION, "status": "complete", "articles": len(decisions),
        "resolved": sum(str(row["final_label"]) in {"eligible", "ineligible"} for row in decisions),
        "unresolved": sum(str(row["final_label"]) == "unresolved" for row in decisions),
        "labels": dict(sorted(Counter(str(row["final_label"]) for row in decisions).items())),
        "decision_paths": dict(sorted(Counter(str(row["decision_path"]) for row in decisions).items())),
        "compact_votes": sum(len(rows) for rows in compact.values()),
        "primary_full_votes": len(primary), "secondary_full_votes": len(secondary),
        "fast_lane_certification": {
            "sample": len(qa_resolved), "agreements": qa_agreements, "agreement_rate": qa_rate,
            "wilson_95_lower": qa_lower, "minimum_agreement": FAST_LANE_MIN_AGREEMENT,
            "minimum_wilson_lower": FAST_LANE_MIN_WILSON_LOWER, "passed": certified,
            "full_population_expansion": full_population_coverage,
        },
        "final_labels_sha256": sha256_path(output_root / "FINAL_LABELS.jsonl"),
    }
    write_json_new(output_root / "FINAL_LABEL_REPORT.json", report)
    return report


def prepare_full_tertiary(*, output_root: Path) -> dict[str, Any]:
    first_final = {str(row["review_id"]): row for row in _iter_jsonl(output_root / "FINAL_LABELS.jsonl")}
    unresolved = {review_id: row for review_id, row in first_final.items() if str(row["final_label"]) == "unresolved"}
    compact = _compact_votes(output_root)
    secondary = _full_secondary_votes(output_root)
    controller = {str(row["review_id"]): row for row in _iter_jsonl(output_root / "CONTROLLER.jsonl")}
    sources = {str(row["source_id"]): row for row in _iter_jsonl(output_root / "SOURCE_ROWS.jsonl")}
    assignments: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in TERTIARY_REVIEWERS}
    selection_rows = []
    for review_id in sorted(unresolved, key=lambda value: _digest(f"{AUDIT_VERSION}|tertiary|{value}")):
        excluded = {str(row["reviewer_id"]) for row in compact[review_id]}
        excluded.add(str(secondary[review_id]["reviewer_id"]))
        eligible = [reviewer for reviewer in TERTIARY_REVIEWERS if reviewer not in excluded]
        if not eligible:
            raise ValueError(f"no row-blind tertiary reviewer: {review_id}")
        reviewer = min(eligible, key=lambda value: (len(assignments[value]), value))
        source = sources[str(controller[review_id]["source_id"])]
        assignments[reviewer].append(_full_worker_row(source, review_id))
        selection_rows.append({"review_id": review_id, "tertiary_full_reviewer": reviewer})
    _write_jsonl_new(output_root / "FULL_TERTIARY_SELECTION.jsonl", selection_rows)
    (output_root / "full_tertiary" / "assignments").mkdir(parents=True, exist_ok=True)
    ledger = []
    for reviewer, rows in assignments.items():
        for number, packet in enumerate(_packetize(
            rows, text_field="rendered_text", article_limit=FULL_PACKET_ARTICLES,
            character_limit=FULL_PACKET_CHARACTERS,
        ), 1):
            packet_id = f"{reviewer}_T{number:03d}"
            path = output_root / "full_tertiary" / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(path, packet)
            ledger.append({
                "packet_id": packet_id, "reviewer_id": reviewer, "articles": len(packet),
                "packet_path": str(path), "review_path": str(output_root / "full_tertiary" / "reviews" / path.name),
                "packet_sha256": sha256_path(path),
            })
        write_json_new(output_root / "full_tertiary" / "assignments" / f"{reviewer}.json", {
            "audit_version": AUDIT_VERSION, "reviewer_id": reviewer,
            "packets": [item for item in ledger if item["reviewer_id"] == reviewer],
        })
    write_json_new(output_root / "FULL_TERTIARY_PACKET_LEDGER.json", {"packets": ledger})
    report = {
        "audit_version": AUDIT_VERSION, "status": "full_tertiary_ready",
        "articles": len(unresolved), "packets": len(ledger),
        "reviewer_load": {key: len(value) for key, value in assignments.items()},
    }
    write_json_new(output_root / "FULL_TERTIARY_PREPARE_REPORT.json", report)
    return report


def _full_tertiary_votes(output_root: Path) -> dict[str, dict[str, Any]]:
    ledger = json.loads((output_root / "FULL_TERTIARY_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    rows = {}
    for item in ledger:
        packet = Path(str(item["packet_path"])); review = Path(str(item["review_path"]))
        validate_review(packet_path=packet, review_path=review, full_text=True)
        for row in _iter_jsonl(review):
            review_id = str(row["review_id"])
            if review_id in rows:
                raise ValueError(f"duplicate tertiary review: {review_id}")
            rows[review_id] = {**row, "reviewer_id": str(item["reviewer_id"])}
    return rows


def finalize_tertiary(*, output_root: Path) -> dict[str, Any]:
    first_final = list(_iter_jsonl(output_root / "FINAL_LABELS.jsonl"))
    compact = _compact_votes(output_root)
    tertiary = _full_tertiary_votes(output_root)
    decisions = []
    for row in first_final:
        review_id = str(row["review_id"])
        if str(row["final_label"]) != "unresolved":
            decisions.append({**row, "tertiary_full_vote": None})
            continue
        third = tertiary.get(review_id)
        if third is None:
            raise ValueError(f"missing tertiary vote: {review_id}")
        compact_reviewers = {str(vote["reviewer_id"]) for vote in compact[review_id]}
        if str(third["reviewer_id"]) in compact_reviewers:
            raise ValueError(f"tertiary reviewer saw compact row: {review_id}")
        first = row["primary_full_vote"]; second = row["secondary_full_vote"]
        if str(third["reviewer_id"]) in {str(first["reviewer_id"]), str(second["reviewer_id"])}:
            raise ValueError(f"tertiary reviewer not independent: {review_id}")
        labels = [
            str(vote["manual_label"]) for vote in (first, second, third)
            if str(vote["manual_label"]) in {"eligible", "ineligible"}
        ]
        counts = Counter(labels)
        majority = [label for label, count in counts.items() if count >= 2]
        if len(majority) == 1:
            final_label = majority[0]; path = "three_reader_full_text_majority"
        else:
            final_label = "unresolved"; path = "three_reader_no_decisive_majority"
        decisions.append({
            **row, "final_label": final_label, "decision_path": path,
            "tertiary_full_vote": third,
        })
    decisions.sort(key=lambda row: str(row["source_id"]))
    _write_jsonl_new(output_root / "FINAL_LABELS_V2.jsonl", decisions)
    report = {
        "audit_version": AUDIT_VERSION, "status": "complete", "articles": len(decisions),
        "resolved": sum(str(row["final_label"]) in {"eligible", "ineligible"} for row in decisions),
        "unresolved": sum(str(row["final_label"]) == "unresolved" for row in decisions),
        "labels": dict(sorted(Counter(str(row["final_label"]) for row in decisions).items())),
        "decision_paths": dict(sorted(Counter(str(row["decision_path"]) for row in decisions).items())),
        "tertiary_full_votes": len(tertiary),
        "final_labels_sha256": sha256_path(output_root / "FINAL_LABELS_V2.jsonl"),
    }
    write_json_new(output_root / "FINAL_LABEL_REPORT_V2.json", report)
    return report


def _binary_metrics(truth: np.ndarray, probability: np.ndarray, threshold: float) -> dict[str, Any]:
    prediction = (probability >= threshold).astype(np.int8)
    return {
        "articles": len(truth), "threshold": threshold,
        "accuracy": float(accuracy_score(truth, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(truth, prediction)),
        "eligible_precision": float(precision_score(truth, prediction, zero_division=0)),
        "eligible_recall": float(recall_score(truth, prediction, zero_division=0)),
        "eligible_f1": float(f1_score(truth, prediction, zero_division=0)),
        "roc_auc": float(roc_auc_score(truth, probability)),
        "confusion_matrix": confusion_matrix(truth, prediction, labels=[0, 1]).tolist(),
        "confusion_matrix_labels": ["ineligible", "eligible"],
    }


def score_models(
    *, output_root: Path, feature_root: Path, forward_model_root: Path,
    reverse_model_root: Path,
) -> dict[str, Any]:
    final_report = json.loads((output_root / "FINAL_LABEL_REPORT_V2.json").read_text(encoding="utf-8"))
    if final_report.get("status") != "complete":
        raise ValueError("labels are not final")
    labels = {str(row["source_id"]): str(row["final_label"]) for row in _iter_jsonl(output_root / "FINAL_LABELS_V2.jsonl")}
    rows = [row for row in _iter_jsonl(output_root / "SOURCE_ROWS.jsonl") if labels[str(row["source_id"])] in {"eligible", "ineligible"}]
    rows.sort(key=lambda row: (str(row["published_at_text"]), str(row["source_id"])))
    for row in rows:
        row["label"] = labels[str(row["source_id"])]
    contract = json.loads((feature_root / "FEATURE_CONTRACT.json").read_text(encoding="utf-8"))
    feature_names = list(map(str, contract["feature_names"]))
    feature_index = {name: index for index, name in enumerate(feature_names)}
    active = {str(family): set(map(str, values)) for family, values in contract["active_categories"].items()}
    historical: dict[str, set[str]] = defaultdict(set)
    with (feature_root / "CATEGORY_CATALOG_2010_2025.csv").open("r", encoding="utf-8", newline="") as handle:
        for item in csv.DictReader(handle):
            historical[str(item["family"])].add(str(item["category"]))
    caps = {str(row["source_id"]): row for row in rows}
    matrix = _build_matrix(rows, caps, feature_index, active, historical)
    truth = np.asarray([row["label"] == "eligible" for row in rows], dtype=np.int8)
    results = {}
    prediction_rows = [{"source_id": str(row["source_id"]), "label": str(row["label"])} for row in rows]
    for name, root in (("train_2025", forward_model_root), ("train_2026", reverse_model_root)):
        validation = json.loads((root / "VALIDATION.json").read_text(encoding="utf-8"))
        if validation.get("status") != "passed":
            raise ValueError(f"unvalidated model: {root}")
        model_report = json.loads((root / "REPORT.json").read_text(encoding="utf-8"))
        threshold = float(model_report["selected_threshold"])
        model = joblib.load(root / "RANDOM_FOREST.joblib")
        probability = model.predict_proba(matrix)[:, 1]
        results[name] = _binary_metrics(truth, probability, threshold)
        for item, score in zip(prediction_rows, probability, strict=True):
            item[f"{name}_eligible_probability"] = float(score)
            item[f"{name}_predicted_label"] = "eligible" if score >= threshold else "ineligible"
    _write_jsonl_new(output_root / "MODEL_PREDICTIONS.jsonl", prediction_rows)
    report = {
        "audit_version": AUDIT_VERSION, "status": "complete",
        "evaluation_role": "untouched temporal holdout frozen and labeled before model exposure",
        "articles_frozen": EXPECTED_ARTICLES, "articles_scored": len(rows),
        "unresolved_excluded": EXPECTED_ARTICLES - len(rows),
        "label_counts": dict(sorted(Counter(row["label"] for row in rows).items())),
        "models": results,
        "inputs": {
            "feature_contract_sha256": sha256_path(feature_root / "FEATURE_CONTRACT.json"),
            "forward_model_manifest_sha256": sha256_path(forward_model_root / "HASH_MANIFEST.json"),
            "reverse_model_manifest_sha256": sha256_path(reverse_model_root / "HASH_MANIFEST.json"),
            "final_labels_sha256": sha256_path(output_root / "FINAL_LABELS_V2.jsonl"),
        },
        "predictions_sha256": sha256_path(output_root / "MODEL_PREDICTIONS.jsonl"),
    }
    write_json_new(output_root / "MODEL_EVALUATION_REPORT.json", report)
    return report


def validate_artifacts(*, output_root: Path) -> dict[str, Any]:
    freeze = json.loads((output_root / "FREEZE_MANIFEST.json").read_text(encoding="utf-8"))
    final_report = json.loads((output_root / "FINAL_LABEL_REPORT_V2.json").read_text(encoding="utf-8"))
    model_report = json.loads((output_root / "MODEL_EVALUATION_REPORT.json").read_text(encoding="utf-8"))
    controller = list(_iter_jsonl(output_root / "CONTROLLER.jsonl"))
    sources = list(_iter_jsonl(output_root / "SOURCE_ROWS.jsonl"))
    final = list(_iter_jsonl(output_root / "FINAL_LABELS_V2.jsonl"))
    predictions = list(_iter_jsonl(output_root / "MODEL_PREDICTIONS.jsonl"))
    compact = _compact_votes(output_root)
    primary = _full_primary_votes(output_root)
    secondary = _full_secondary_votes(output_root)
    secondary_selected = {str(row["review_id"]) for row in _iter_jsonl(output_root / "FULL_SECONDARY_SELECTION.jsonl")}
    tertiary = _full_tertiary_votes(output_root)
    tertiary_selected = {str(row["review_id"]) for row in _iter_jsonl(output_root / "FULL_TERTIARY_SELECTION.jsonl")}
    checks = {
        "freeze_complete": freeze.get("status") == "frozen_and_compact_packets_ready",
        "frozen_articles": int(freeze.get("articles", 0)) == EXPECTED_ARTICLES,
        "controller_rows": len(controller) == EXPECTED_ARTICLES,
        "source_rows": len(sources) == EXPECTED_ARTICLES,
        "unique_sources": len({str(row["source_id"]) for row in sources}) == EXPECTED_ARTICLES,
        "compact_two_votes": set(compact) == {str(row["review_id"]) for row in controller}
        and all(len(rows) == 2 for rows in compact.values()),
        "primary_full_all_articles": len(primary) == EXPECTED_ARTICLES,
        "secondary_exact_selection": set(secondary) == secondary_selected,
        "tertiary_exact_selection": set(tertiary) == tertiary_selected,
        "final_complete": final_report.get("status") == "complete",
        "final_rows": len(final) == EXPECTED_ARTICLES,
        "final_unique_sources": len({str(row["source_id"]) for row in final}) == EXPECTED_ARTICLES,
        "final_count_reconciles": int(final_report["resolved"]) + int(final_report["unresolved"]) == EXPECTED_ARTICLES,
        "model_complete": model_report.get("status") == "complete",
        "model_role": model_report.get("evaluation_role") == "untouched temporal holdout frozen and labeled before model exposure",
        "prediction_rows": len(predictions) == int(model_report["articles_scored"]),
        "unresolved_excluded": int(model_report["unresolved_excluded"]) == int(final_report["unresolved"]),
        "source_hash_frozen": sha256_path(output_root / "SOURCE_ROWS.jsonl") == freeze["artifacts"]["SOURCE_ROWS.jsonl"]["sha256"],
        "controller_hash_frozen": sha256_path(output_root / "CONTROLLER.jsonl") == freeze["artifacts"]["CONTROLLER.jsonl"]["sha256"],
    }
    if not all(checks.values()):
        raise ValueError(f"holdout validation failed: {checks}")
    validation = {"audit_version": AUDIT_VERSION, "status": "passed", "checks": checks}
    write_json_new(output_root / "VALIDATION.json", validation)
    files = sorted(path for path in output_root.rglob("*") if path.is_file() and path.name != "HASH_MANIFEST.json")
    write_json_new(output_root / "HASH_MANIFEST.json", {
        "audit_version": AUDIT_VERSION,
        "files": {
            str(path.relative_to(output_root)).replace("\\", "/"): {
                "bytes": path.stat().st_size, "sha256": sha256_path(path),
            }
            for path in files
        },
    })
    return validation
