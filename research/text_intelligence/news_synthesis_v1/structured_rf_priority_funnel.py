from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path, write_json_new
from .structured_rf_disagreement_audit import AUDIT_VERSION as CALIBRATION_AUDIT_VERSION
from .structured_rf_disagreement_audit import (
    COMPACT_LABELS,
    FULL_LABELS,
    FULL_PACKET_ARTICLES,
    FULL_PACKET_CHARACTERS,
    validate_review,
)
from .trading_ideas_blind_audit import ALLOWED_REASONS, compact_preview


FUNNEL_VERSION = "structured_rf_priority_blind_review_v1"
PRIMARY_REVIEWERS = ("P1", "P2", "P3")
PACKET_ARTICLES = 180
PACKET_CHARACTERS = 120_000
EXPECTED_PRIORITY = 12_099
EXPECTED_ALREADY_AUDITED = 334
EXPECTED_PRIMARY = EXPECTED_PRIORITY - EXPECTED_ALREADY_AUDITED
FAST_QA_FRACTION = 0.10
FAST_QA_MIN_AGREEMENT = 0.985
FAST_QA_MIN_WILSON_LOWER = 0.975
FAST_QA_MAX_NEEDS_FULL = 0.005


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _write_jsonl_new(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
            count += 1
    return count


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


def prepare_primary(
    *,
    calibration_root: Path,
    successor_authority: Path,
    article_features_path: Path,
    rendered_texts_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    calibration_validation = json.loads((calibration_root / "VALIDATION.json").read_text(encoding="utf-8"))
    if calibration_validation.get("status") != "passed":
        raise ValueError("calibration audit is not validated")
    authority_validation = json.loads((successor_authority / "VALIDATION.json").read_text(encoding="utf-8"))
    if authority_validation.get("status") != "passed":
        raise ValueError("successor authority is not validated")
    priority = {str(row["source_id"]): row for row in iter_jsonl(calibration_root / "PRIORITY_EXPANSION_CONTROLLER.jsonl")}
    audited = {str(row["source_id"]) for row in iter_jsonl(calibration_root / "FINAL_DECISIONS.jsonl")}
    already_audited = set(priority) & audited
    candidate_ids = set(priority) - audited
    if len(priority) != EXPECTED_PRIORITY or len(already_audited) != EXPECTED_ALREADY_AUDITED or len(candidate_ids) != EXPECTED_PRIMARY:
        raise ValueError("priority/exclusion population mismatch")

    current_labels: dict[str, str] = {}
    for row in iter_jsonl(successor_authority / "article_forecast_eligibility_labels.jsonl"):
        source_id = str(row["source_id"])
        if source_id in candidate_ids:
            current_labels[source_id] = str(row["forecast_eligibility_label"])
    if set(current_labels) != candidate_ids or set(current_labels.values()) != {"ineligible"}:
        raise ValueError("priority successor-label membership mismatch")

    features: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(article_features_path):
        source_id = str(row["source_id"])
        if source_id in candidate_ids:
            features[source_id] = row
    if set(features) != candidate_ids:
        raise ValueError("priority feature membership mismatch")
    previews: dict[str, dict[str, Any]] = {}
    rendered_hashes: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts_path):
        source_id = str(row["source_id"])
        if source_id not in candidate_ids:
            continue
        text = str(row.get("rendered_text") or "")
        digest = _digest(text)
        if digest != str(row.get("rendered_text_hash") or ""):
            raise ValueError(f"rendered hash mismatch: {source_id}")
        previews[source_id] = compact_preview(text, sentence_count=2)
        rendered_hashes[source_id] = digest
    if set(previews) != candidate_ids:
        raise ValueError("priority rendered membership mismatch")

    reviewer_rows: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in PRIMARY_REVIEWERS}
    controller_rows = []
    ordered = sorted(candidate_ids, key=lambda value: _digest(f"{FUNNEL_VERSION}|order|{value}"))
    for position, source_id in enumerate(ordered):
        feature = features[source_id]
        hidden = priority[source_id]
        reviewer = PRIMARY_REVIEWERS[position % len(PRIMARY_REVIEWERS)]
        review_id = "RP" + _digest(f"{FUNNEL_VERSION}|{source_id}")[:20]
        channels = {str(value).strip().casefold() for value in feature.get("channels") or ()}
        controller_rows.append({
            "review_id": review_id, "source_id": source_id,
            "current_label": "ineligible", "model_label": "eligible",
            "eligible_probability": float(hidden["eligible_probability"]),
            "extreme_probability": bool(hidden["extreme_probability"]),
            "earnings_or_guidance_channel": bool(hidden["earnings_or_guidance_channel"]),
            "source_split": str(feature["split"]),
            "month": str(feature["published_at_text"])[:7],
            "dominant_channel": _dominant_channel(channels),
            "primary_reviewer": reviewer,
            "rendered_text_sha256": rendered_hashes[source_id],
        })
        reviewer_rows[reviewer].append({
            "review_id": review_id,
            "published_at_utc": str(feature["published_at_text"]),
            "provider": str(feature.get("provider") or ""),
            "tickers": list(feature.get("tickers") or ()),
            "channels": list(feature.get("channels") or ()),
            "provider_tags": list(feature.get("provider_tags") or ()),
            "content_quality_flags": list(feature.get("content_quality_flags") or ()),
            "session_segment": str(feature.get("session_segment") or ""),
            "ticker_count": int(feature.get("ticker_count") or 0),
            "rendered_chars": int(feature.get("rendered_chars") or 0),
            "min_ticker_session_ordinal": feature.get("min_ticker_session_ordinal"),
            "min_seconds_since_previous_ticker_news": feature.get("min_seconds_since_previous_ticker_news"),
            **previews[source_id],
            "rendered_text_sha256": rendered_hashes[source_id],
        })
    output_root.mkdir(parents=True)
    _write_jsonl_new(output_root / "CONTROLLER.jsonl", controller_rows)
    ledger = []
    for reviewer, rows in reviewer_rows.items():
        rows.sort(key=lambda row: _digest(f"{FUNNEL_VERSION}|{reviewer}|{row['review_id']}"))
        for number, packet in enumerate(_packetize(rows), 1):
            packet_id = f"{reviewer}_P{number:03d}"
            path = output_root / "primary" / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(path, packet)
            ledger.append({
                "packet_id": packet_id, "reviewer_id": reviewer, "articles": len(packet),
                "packet_path": str(path), "packet_sha256": sha256_path(path),
            })
    write_json_new(output_root / "PRIMARY_PACKET_LEDGER.json", {"packets": ledger})
    write_json_new(output_root / "PRIMARY_REVIEW_INSTRUCTIONS.json", {
        "objective": "Classify forecast eligibility from only supplied metadata, title, teaser, and first two sentences.",
        "eligible": "The preview independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The preview is analyst/investment opinion, technical/valuation material, price movement, list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "needs_full_text": "Compact evidence cannot safely determine whether a new material issuer event is independently reported.",
        "allowed_labels": sorted(COMPACT_LABELS), "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "required_fields": [
            "review_id", "manual_label", "confidence_probability", "reason_code",
            "rationale", "evidence_excerpt", "isolation_attestation",
        ],
        "blindness": "Use only the assigned packet. Do not inspect controller files, labels, RF outputs, calibration statistics, other packets/reviews, repository data, or internet sources.",
    })
    manifest = {
        "funnel_version": FUNNEL_VERSION, "status": "primary_packets_frozen",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "priority_population": len(priority), "already_audited_excluded": len(already_audited),
        "primary_articles": len(controller_rows), "primary_votes_required": len(controller_rows),
        "reviewer_load": {reviewer: len(rows) for reviewer, rows in reviewer_rows.items()},
        "packets": len(ledger),
        "inputs": {
            "calibration_audit": {"path": str(calibration_root), "version": CALIBRATION_AUDIT_VERSION,
                                  "hash_manifest_sha256": sha256_path(calibration_root / "HASH_MANIFEST.json")},
            "successor_authority": {"path": str(successor_authority),
                                    "hash_manifest_sha256": sha256_path(successor_authority / "HASH_MANIFEST.json")},
            "article_features": {"path": str(article_features_path), "sha256": sha256_path(article_features_path)},
            "rendered_texts": {"path": str(rendered_texts_path), "sha256": sha256_path(rendered_texts_path)},
        },
        "hidden_from_reviewers": [
            "source_id", "current_label", "model_label", "eligible_probability",
            "extreme_probability", "earnings_or_guidance_channel", "source_split",
            "month", "dominant_channel", "primary_reviewer",
        ],
    }
    write_json_new(output_root / "PREPARE_MANIFEST.json", manifest)
    return manifest


def _dominant_channel(channels: set[str]) -> str:
    for candidate in ("earnings", "earnings beats", "earnings misses", "guidance", "news"):
        if candidate in channels:
            return candidate.replace(" ", "_")
    return "other" if channels else "none"


def validate_primary_review(*, packet_path: Path, review_path: Path) -> dict[str, Any]:
    return validate_review(packet_path=packet_path, review_path=review_path, full_text=False)


def collect_primary(*, output_root: Path) -> dict[str, Any]:
    ledger = json.loads((output_root / "PRIMARY_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    decisions = {}
    counts: Counter[str] = Counter()
    for item in ledger:
        packet = Path(str(item["packet_path"]))
        review = output_root / "primary" / "reviews" / packet.name
        validate_primary_review(packet_path=packet, review_path=review)
        for row in iter_jsonl(review):
            review_id = str(row["review_id"])
            if review_id in decisions:
                raise ValueError(f"duplicate primary review: {review_id}")
            decisions[review_id] = {**row, "reviewer_id": str(item["reviewer_id"])}
            counts[str(row["manual_label"])] += 1
    if set(decisions) != set(controller):
        raise ValueError("primary review coverage mismatch")
    path = output_root / "PRIMARY_DECISIONS.jsonl"
    _write_jsonl_new(path, (decisions[key] for key in sorted(decisions)))
    report = {
        "funnel_version": FUNNEL_VERSION, "status": "primary_reviews_complete",
        "articles": len(decisions), "label_counts": dict(sorted(counts.items())),
        "high_confidence_eligible": sum(
            row["manual_label"] == "eligible" and float(row["confidence_probability"]) >= 0.90
            for row in decisions.values()
        ),
        "decisions": {"path": str(path), "sha256": sha256_path(path)},
    }
    write_json_new(output_root / "PRIMARY_REPORT.json", report)
    return report


def _wilson_lower(successes: int, total: int, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    rate = successes / total
    denominator = 1.0 + z * z / total
    center = (rate + z * z / (2.0 * total)) / denominator
    radius = z * math.sqrt(rate * (1.0 - rate) / total + z * z / (4.0 * total * total)) / denominator
    return max(0.0, center - radius)


def _load_primary_worker_rows(output_root: Path) -> dict[str, dict[str, Any]]:
    ledger = json.loads((output_root / "PRIMARY_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    rows: dict[str, dict[str, Any]] = {}
    for item in ledger:
        for row in iter_jsonl(Path(str(item["packet_path"]))):
            review_id = str(row["review_id"])
            if review_id in rows:
                raise ValueError(f"duplicate primary worker row: {review_id}")
            rows[review_id] = row
    return rows


def prepare_qa(*, output_root: Path) -> dict[str, Any]:
    primary_report = json.loads((output_root / "PRIMARY_REPORT.json").read_text(encoding="utf-8"))
    if primary_report.get("status") != "primary_reviews_complete":
        raise ValueError("primary reviews are not complete")
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    decisions = {str(row["review_id"]): row for row in iter_jsonl(output_root / "PRIMARY_DECISIONS.jsonl")}
    worker_rows = _load_primary_worker_rows(output_root)
    if set(controller) != set(decisions) or set(worker_rows) != set(decisions):
        raise ValueError("QA source membership mismatch")

    mandatory = {
        review_id for review_id, row in decisions.items()
        if str(row["manual_label"]) != "eligible" or float(row["confidence_probability"]) < 0.90
    }
    fast = set(decisions) - mandatory
    strata: dict[tuple[str, str, bool, bool], list[str]] = defaultdict(list)
    for review_id in fast:
        hidden = controller[review_id]
        strata[(
            str(hidden["month"]), str(hidden["dominant_channel"]),
            bool(hidden["extreme_probability"]), bool(hidden["earnings_or_guidance_channel"]),
        )].append(review_id)
    sampled: set[str] = set()
    for stratum, review_ids in strata.items():
        ordered = sorted(review_ids, key=lambda value: _digest(f"{FUNNEL_VERSION}|fast-qa|{stratum}|{value}"))
        sampled.update(ordered[:max(1, math.ceil(len(ordered) * FAST_QA_FRACTION))])
    selected = mandatory | sampled

    reviewer_rows: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in PRIMARY_REVIEWERS}
    ledger_rows = []
    for review_id in sorted(selected, key=lambda value: _digest(f"{FUNNEL_VERSION}|qa-order|{value}")):
        primary_reviewer = str(decisions[review_id]["reviewer_id"])
        # P2 is deliberately conservative about compact-text sufficiency. Keep that
        # useful behavior in the primary pass, while assigning compact QA to the
        # two reviewers that demonstrated stable decisive use of the same contract.
        qa_reviewer = {"P1": "P3", "P2": "P1", "P3": "P1"}[primary_reviewer]
        selection_reason = "mandatory_noneligible_needs_full_or_low_confidence" if review_id in mandatory else "stratified_fast_lane_qa"
        reviewer_rows[qa_reviewer].append(worker_rows[review_id])
        ledger_rows.append({
            "review_id": review_id, "primary_reviewer": primary_reviewer,
            "qa_reviewer": qa_reviewer, "selection_reason": selection_reason,
        })
    _write_jsonl_new(output_root / "QA_SELECTION_LEDGER.jsonl", ledger_rows)
    packet_ledger = []
    for reviewer, rows in reviewer_rows.items():
        rows.sort(key=lambda row: _digest(f"{FUNNEL_VERSION}|qa|{reviewer}|{row['review_id']}"))
        for number, packet in enumerate(_packetize(rows), 1):
            packet_id = f"{reviewer}_Q{number:03d}"
            path = output_root / "qa" / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(path, packet)
            packet_ledger.append({
                "packet_id": packet_id, "reviewer_id": reviewer, "articles": len(packet),
                "packet_path": str(path), "packet_sha256": sha256_path(path),
            })
    write_json_new(output_root / "QA_PACKET_LEDGER.json", {"packets": packet_ledger})
    report = {
        "funnel_version": FUNNEL_VERSION, "status": "qa_packets_frozen",
        "mandatory_articles": len(mandatory), "fast_lane_articles": len(fast),
        "fast_lane_qa_articles": len(sampled), "qa_articles": len(selected),
        "qa_votes_required": len(selected), "packets": len(packet_ledger),
        "reviewer_load": {reviewer: len(rows) for reviewer, rows in reviewer_rows.items()},
        "fast_lane_sampling_fraction": FAST_QA_FRACTION,
        "independence": "Every QA reviewer differs from the article's primary reviewer.",
    }
    write_json_new(output_root / "QA_PREPARE_REPORT.json", report)
    return report


def collect_qa(*, output_root: Path) -> dict[str, Any]:
    packet_ledger = json.loads((output_root / "QA_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    selection = {str(row["review_id"]): row for row in iter_jsonl(output_root / "QA_SELECTION_LEDGER.jsonl")}
    primary = {str(row["review_id"]): row for row in iter_jsonl(output_root / "PRIMARY_DECISIONS.jsonl")}
    qa: dict[str, dict[str, Any]] = {}
    for item in packet_ledger:
        packet = Path(str(item["packet_path"]))
        review = output_root / "qa" / "reviews" / packet.name
        validate_primary_review(packet_path=packet, review_path=review)
        for row in iter_jsonl(review):
            review_id = str(row["review_id"])
            if review_id in qa:
                raise ValueError(f"duplicate QA review: {review_id}")
            qa[review_id] = {**row, "reviewer_id": str(item["reviewer_id"])}
    if set(qa) != set(selection):
        raise ValueError("QA coverage mismatch")
    if any(qa[key]["reviewer_id"] == primary[key]["reviewer_id"] for key in qa):
        raise ValueError("QA reviewer independence mismatch")
    path = output_root / "QA_DECISIONS.jsonl"
    _write_jsonl_new(path, (qa[key] for key in sorted(qa)))

    fast_ids = [key for key, row in selection.items() if row["selection_reason"] == "stratified_fast_lane_qa"]
    agreements = sum(str(qa[key]["manual_label"]) == "eligible" for key in fast_ids)
    needs_full = sum(str(qa[key]["manual_label"]) == "needs_full_text" for key in fast_ids)
    agreement_rate = agreements / len(fast_ids) if fast_ids else 0.0
    needs_full_rate = needs_full / len(fast_ids) if fast_ids else 1.0
    wilson_lower = _wilson_lower(agreements, len(fast_ids))
    certified = (
        agreement_rate >= FAST_QA_MIN_AGREEMENT
        and wilson_lower >= FAST_QA_MIN_WILSON_LOWER
        and needs_full_rate <= FAST_QA_MAX_NEEDS_FULL
    )
    report = {
        "funnel_version": FUNNEL_VERSION, "status": "qa_reviews_complete",
        "qa_articles": len(qa), "fast_lane_qa_articles": len(fast_ids),
        "fast_lane_eligible_agreements": agreements,
        "fast_lane_agreement_rate": agreement_rate,
        "fast_lane_wilson_95_lower": wilson_lower,
        "fast_lane_needs_full_text": needs_full,
        "fast_lane_needs_full_text_rate": needs_full_rate,
        "fast_lane_certified": certified,
        "thresholds": {
            "minimum_agreement_rate": FAST_QA_MIN_AGREEMENT,
            "minimum_wilson_95_lower": FAST_QA_MIN_WILSON_LOWER,
            "maximum_needs_full_text_rate": FAST_QA_MAX_NEEDS_FULL,
        },
        "decisions": {"path": str(path), "sha256": sha256_path(path)},
    }
    write_json_new(output_root / "QA_REPORT.json", report)
    return report


def prepare_full(*, output_root: Path, rendered_texts_path: Path) -> dict[str, Any]:
    qa_report = json.loads((output_root / "QA_REPORT.json").read_text(encoding="utf-8"))
    if qa_report.get("status") != "qa_reviews_complete":
        raise ValueError("QA reviews are not complete")
    if not bool(qa_report.get("fast_lane_certified")):
        raise ValueError("fast lane did not certify; expand compact QA before full-text escalation")
    primary = {str(row["review_id"]): row for row in iter_jsonl(output_root / "PRIMARY_DECISIONS.jsonl")}
    qa = {str(row["review_id"]): row for row in iter_jsonl(output_root / "QA_DECISIONS.jsonl")}
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    selected = {
        review_id for review_id, second in qa.items()
        if str(primary[review_id]["manual_label"]) != str(second["manual_label"])
        or str(primary[review_id]["manual_label"]) == "needs_full_text"
        or str(second["manual_label"]) == "needs_full_text"
    }
    source_to_review = {str(controller[review_id]["source_id"]): review_id for review_id in selected}
    full_texts: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts_path):
        review_id = source_to_review.get(str(row["source_id"]))
        if review_id is None:
            continue
        rendered = str(row.get("rendered_text") or "")
        digest = _digest(rendered)
        if digest != str(row.get("rendered_text_hash") or "") or digest != str(controller[review_id]["rendered_text_sha256"]):
            raise ValueError(f"full-text hash mismatch: {review_id}")
        full_texts[review_id] = rendered
    if set(full_texts) != selected:
        raise ValueError("full-text escalation membership mismatch")
    compact_rows = _load_primary_worker_rows(output_root)
    reviewer_rows: dict[str, list[dict[str, Any]]] = {"F1": [], "F2": []}
    for position, review_id in enumerate(sorted(selected, key=lambda value: _digest(f"{FUNNEL_VERSION}|full|{value}"))):
        source = compact_rows[review_id]
        reviewer = "F1" if position % 2 == 0 else "F2"
        reviewer_rows[reviewer].append({
            "review_id": review_id,
            "published_at_utc": source["published_at_utc"], "provider": source["provider"],
            "tickers": source["tickers"], "channels": source["channels"],
            "provider_tags": source["provider_tags"],
            "rendered_text": full_texts[review_id],
            "rendered_text_sha256": controller[review_id]["rendered_text_sha256"],
        })
    ledger = []
    for reviewer, rows in reviewer_rows.items():
        for number, packet in enumerate(_packetize(
            rows, text_field="rendered_text", article_limit=FULL_PACKET_ARTICLES,
            character_limit=FULL_PACKET_CHARACTERS,
        ), 1):
            packet_id = f"{reviewer}_F{number:03d}"
            path = output_root / "full" / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(path, packet)
            ledger.append({
                "packet_id": packet_id, "reviewer_id": reviewer, "articles": len(packet),
                "packet_path": str(path), "packet_sha256": sha256_path(path),
            })
    write_json_new(output_root / "FULL_PACKET_LEDGER.json", {"packets": ledger})
    write_json_new(output_root / "FULL_REVIEW_INSTRUCTIONS.json", {
        "objective": "Classify issuer forecast eligibility using only supplied metadata and complete rendered source text.",
        "eligible": "The article independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The article is opinion, technical/valuation material, price movement, list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "insufficient_information": "The complete rendered source still cannot establish whether a new material issuer event is reported.",
        "allowed_labels": sorted(FULL_LABELS), "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "required_fields": [
            "review_id", "manual_label", "confidence_probability", "reason_code",
            "rationale", "evidence_excerpt", "isolation_attestation",
        ],
        "blindness": "Use only the assigned full-text packet. Do not inspect compact packets or votes, controller files, labels, model outputs, statistics, repository data, or internet sources.",
    })
    report = {
        "funnel_version": FUNNEL_VERSION, "status": "full_packets_frozen",
        "articles": len(selected), "full_votes_required": len(selected), "packets": len(ledger),
        "reviewer_load": {reviewer: len(rows) for reviewer, rows in reviewer_rows.items()},
        "selection_policy": "primary/QA label contradiction or either compact reviewer requested full text",
    }
    write_json_new(output_root / "FULL_PREPARE_REPORT.json", report)
    return report


def finalize(*, output_root: Path) -> dict[str, Any]:
    qa_report = json.loads((output_root / "QA_REPORT.json").read_text(encoding="utf-8"))
    if not bool(qa_report.get("fast_lane_certified")):
        raise ValueError("fast lane is not certified")
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    primary = {str(row["review_id"]): row for row in iter_jsonl(output_root / "PRIMARY_DECISIONS.jsonl")}
    qa = {str(row["review_id"]): row for row in iter_jsonl(output_root / "QA_DECISIONS.jsonl")}
    full: dict[str, dict[str, Any]] = {}
    full_ledger = json.loads((output_root / "FULL_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    for item in full_ledger:
        packet = Path(str(item["packet_path"]))
        review = output_root / "full" / "reviews" / packet.name
        validate_review(packet_path=packet, review_path=review, full_text=True)
        for row in iter_jsonl(review):
            review_id = str(row["review_id"])
            if review_id in full:
                raise ValueError(f"duplicate full review: {review_id}")
            full[review_id] = {**row, "reviewer_id": str(item["reviewer_id"])}
    expected_full = {
        review_id for review_id, second in qa.items()
        if str(primary[review_id]["manual_label"]) != str(second["manual_label"])
        or str(primary[review_id]["manual_label"]) == "needs_full_text"
        or str(second["manual_label"]) == "needs_full_text"
    }
    if set(full) != expected_full:
        raise ValueError("full review coverage mismatch")

    decisions = []
    paths: Counter[str] = Counter()
    labels: Counter[str] = Counter()
    for review_id, hidden in controller.items():
        first = primary[review_id]
        second = qa.get(review_id)
        final_vote = full.get(review_id)
        if final_vote is not None:
            full_label = str(final_vote["manual_label"])
            final_label = "unresolved" if full_label == "insufficient_information" else full_label
            decision_path = "fresh_full_text_resolution" if final_label != "unresolved" else "full_text_insufficient"
        elif second is not None:
            first_label = str(first["manual_label"])
            second_label = str(second["manual_label"])
            if first_label != second_label or "needs_full_text" in {first_label, second_label}:
                raise ValueError(f"unresolved compact contradiction omitted from full review: {review_id}")
            final_label = first_label
            decision_path = "independent_compact_agreement"
        else:
            if str(first["manual_label"]) != "eligible" or float(first["confidence_probability"]) < 0.90:
                raise ValueError(f"non-fast decision omitted from QA: {review_id}")
            final_label = "eligible"
            decision_path = "certified_fast_lane_primary"
        outcome = (
            "current_label_wrong" if final_label == "eligible"
            else "current_label_confirmed" if final_label == "ineligible"
            else "unresolved"
        )
        paths[decision_path] += 1
        labels[final_label] += 1
        decisions.append({
            **hidden, "final_review_label": final_label, "audit_outcome": outcome,
            "decision_path": decision_path, "primary_vote": first,
            "qa_vote": second, "full_vote": final_vote,
        })
    decisions.sort(key=lambda row: str(row["source_id"]))
    decisions_path = output_root / "FINAL_DECISIONS.jsonl"
    _write_jsonl_new(decisions_path, decisions)
    report = {
        "funnel_version": FUNNEL_VERSION, "status": "complete",
        "priority_population": EXPECTED_PRIORITY,
        "previously_audited_reused": EXPECTED_ALREADY_AUDITED,
        "newly_reviewed": len(decisions),
        "resolved": sum(row["final_review_label"] != "unresolved" for row in decisions),
        "unresolved": labels["unresolved"],
        "new_label_counts": dict(sorted(labels.items())),
        "new_audit_outcomes": dict(sorted(Counter(str(row["audit_outcome"]) for row in decisions).items())),
        "decision_paths": dict(sorted(paths.items())),
        "primary_votes": len(primary), "qa_votes": len(qa), "full_votes": len(full),
        "total_new_votes": len(primary) + len(qa) + len(full),
        "fast_lane_certification": qa_report,
        "final_decisions": {"path": str(decisions_path), "sha256": sha256_path(decisions_path)},
    }
    write_json_new(output_root / "FINAL_REPORT.json", report)
    return report


def validate_artifacts(*, output_root: Path) -> dict[str, Any]:
    controller = list(iter_jsonl(output_root / "CONTROLLER.jsonl"))
    primary = list(iter_jsonl(output_root / "PRIMARY_DECISIONS.jsonl"))
    qa = list(iter_jsonl(output_root / "QA_DECISIONS.jsonl"))
    final = list(iter_jsonl(output_root / "FINAL_DECISIONS.jsonl"))
    primary_ledger = json.loads((output_root / "PRIMARY_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    qa_ledger = json.loads((output_root / "QA_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    full_ledger = json.loads((output_root / "FULL_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    primary_votes = qa_votes = full_votes = 0
    for item in primary_ledger:
        packet = Path(str(item["packet_path"])); review = output_root / "primary" / "reviews" / packet.name
        primary_votes += int(validate_review(packet_path=packet, review_path=review)["articles"])
    for item in qa_ledger:
        packet = Path(str(item["packet_path"])); review = output_root / "qa" / "reviews" / packet.name
        qa_votes += int(validate_review(packet_path=packet, review_path=review)["articles"])
    for item in full_ledger:
        packet = Path(str(item["packet_path"])); review = output_root / "full" / "reviews" / packet.name
        full_votes += int(validate_review(packet_path=packet, review_path=review, full_text=True)["articles"])
    ids = {str(row["review_id"]) for row in controller}
    checks = {
        "controller_rows": len(controller) == EXPECTED_PRIMARY,
        "unique_controller_ids": len(ids) == EXPECTED_PRIMARY,
        "primary_rows": len(primary) == EXPECTED_PRIMARY,
        "primary_votes": primary_votes == EXPECTED_PRIMARY,
        "qa_rows": len(qa) == 1_763,
        "qa_votes": qa_votes == len(qa),
        "full_rows": full_votes == 432,
        "final_rows": len(final) == EXPECTED_PRIMARY,
        "final_membership": {str(row["review_id"]) for row in final} == ids,
        "fast_lane_certified": bool(json.loads((output_root / "QA_REPORT.json").read_text(encoding="utf-8"))["fast_lane_certified"]),
        "final_report_complete": json.loads((output_root / "FINAL_REPORT.json").read_text(encoding="utf-8")).get("status") == "complete",
    }
    if not all(checks.values()):
        raise ValueError(f"priority funnel validation failed: {checks}")
    validation = {
        "funnel_version": FUNNEL_VERSION, "status": "passed", "checks": checks,
        "primary_votes": primary_votes, "qa_votes": qa_votes, "full_votes": full_votes,
        "newly_reviewed": len(final), "priority_population_with_reused": EXPECTED_PRIORITY,
    }
    write_json_new(output_root / "VALIDATION.json", validation)
    files = sorted(path for path in output_root.rglob("*") if path.is_file() and path.name != "HASH_MANIFEST.json")
    write_json_new(output_root / "HASH_MANIFEST.json", {
        "funnel_version": FUNNEL_VERSION,
        "files": {str(path.relative_to(output_root)).replace("\\", "/"): {
            "bytes": path.stat().st_size, "sha256": sha256_path(path),
        } for path in files},
    })
    return validation


def promote_successor_authority(
    *, audit_root: Path, parent_authority: Path, successor_authority: Path,
) -> dict[str, Any]:
    if successor_authority.exists():
        raise FileExistsError(successor_authority)
    validation = json.loads((audit_root / "VALIDATION.json").read_text(encoding="utf-8"))
    if validation.get("status") != "passed":
        raise ValueError("priority audit is not validated")
    audit_hashes = json.loads((audit_root / "HASH_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    for relative, metadata in audit_hashes.items():
        if sha256_path(audit_root / relative) != str(metadata["sha256"]):
            raise ValueError(f"audit hash mismatch: {relative}")
    parent_hashes = json.loads((parent_authority / "HASH_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    for name, metadata in parent_hashes.items():
        if sha256_path(parent_authority / name) != str(metadata["sha256"]):
            raise ValueError(f"parent hash mismatch: {name}")
    decisions = {str(row["source_id"]): row for row in iter_jsonl(audit_root / "FINAL_DECISIONS.jsonl")}
    if len(decisions) != EXPECTED_PRIMARY:
        raise ValueError("unexpected priority decision population")

    successor_authority.mkdir(parents=True)
    parent_labels = parent_authority / "article_forecast_eligibility_labels.jsonl"
    labels_path = successor_authority / parent_labels.name
    ledger_path = successor_authority / "structured_rf_priority_blind_review_ledger.jsonl"
    seen: set[str] = set(); ledger_rows = []
    rows = changes = resolved = unresolved = 0; label_counts: Counter[str] = Counter()
    with labels_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in iter_jsonl(parent_labels):
            rows += 1; source_id = str(row["source_id"]); decision = decisions.get(source_id)
            if decision is not None:
                seen.add(source_id)
                original = str(row["forecast_eligibility_label"])
                if original != "ineligible":
                    raise ValueError(f"priority parent label drifted: {source_id}")
                final_label = str(decision["final_review_label"])
                if final_label in {"eligible", "ineligible"}:
                    row = dict(row)
                    row.update({
                        "authority_class": "codex_adaptive_blind_review_funnel",
                        "authority_detail": FUNNEL_VERSION,
                        "certification_level": "codex_adjudicated",
                        "decisive": True, "forecast_eligibility_label": final_label,
                        "forecast_eligible": final_label == "eligible", "human_certified": False,
                        "usage_policy": "model_development_adjudicated",
                    })
                    resolved += 1; changes += final_label != original
                else:
                    unresolved += 1
                ledger_rows.append({
                    "source_id": source_id, "review_id": decision["review_id"],
                    "original_label": original,
                    "final_label": original if final_label == "unresolved" else final_label,
                    "changed": final_label == "eligible", "audit_outcome": decision["audit_outcome"],
                    "decision_path": decision["decision_path"],
                    "rendered_text_sha256": decision["rendered_text_sha256"],
                    "primary_vote": decision["primary_vote"], "qa_vote": decision["qa_vote"],
                    "full_vote": decision["full_vote"],
                })
            label_counts[str(row["forecast_eligibility_label"])] += 1
            handle.write(canonical_json(row) + "\n")
    if rows != 361_695 or seen != set(decisions):
        raise ValueError("successor authority membership mismatch")
    _write_jsonl_new(ledger_path, sorted(ledger_rows, key=lambda row: str(row["source_id"])))
    excluded = {parent_labels.name, "REPORT.json", "VALIDATION.json", "LOAD_MANIFEST.json", "HASH_MANIFEST.json"}
    copied = []
    for name in sorted(set(parent_hashes) - excluded):
        destination = successor_authority / name
        shutil.copyfile(parent_authority / name, destination); copied.append(destination)
    sentiment = successor_authority / "gold_issuer_sentiment_labels.jsonl"
    report = {
        "status": "scoped_correction_grade_successor", "authority_version": successor_authority.name,
        "parent_authority": str(parent_authority), "audit_root": str(audit_root),
        "priority_population": EXPECTED_PRIORITY, "previously_audited_in_parent": EXPECTED_ALREADY_AUDITED,
        "newly_reviewed": len(decisions), "resolved_articles": resolved,
        "unresolved_articles": unresolved, "label_changes": changes,
        "authority_label_counts": dict(sorted(label_counts.items())),
        "sentiment_byte_identical": sha256_path(sentiment) == sha256_path(parent_authority / sentiment.name),
        "limitations": ["Local Codex adjudication is not human certification.", "Unresolved rows preserve the parent label and metadata."],
    }
    successor_validation = {
        "status": "passed", "article_rows": rows, "newly_reviewed_rows": len(decisions),
        "resolved_rows": resolved, "unresolved_rows": unresolved, "label_changes": changes,
        "coverage_complete": seen == set(decisions), "sentiment_sha256_equal": report["sentiment_byte_identical"],
        "parent_authority_unchanged": sha256_path(parent_labels) == str(parent_hashes[parent_labels.name]["sha256"]),
    }
    load_manifest = {
        "dataset_version": successor_authority.name, "status": report["status"],
        "parent_authority": str(parent_authority), "audit_root": str(audit_root),
        "primary_tables": {
            "article_forecast_eligibility": {"path": str(labels_path), "rows": rows, "primary_key": ["source_id"]},
            "gold_issuer_sentiment": {"path": str(sentiment), "rows": 16_983, "primary_key": ["unit_id"]},
        },
        "correction_ledger": str(ledger_path),
        "inherited_correction_ledgers": [str(path) for path in copied if path.name != sentiment.name],
    }
    write_json_new(successor_authority / "REPORT.json", report)
    write_json_new(successor_authority / "VALIDATION.json", successor_validation)
    write_json_new(successor_authority / "LOAD_MANIFEST.json", load_manifest)
    files = [labels_path, ledger_path, *copied, successor_authority / "REPORT.json", successor_authority / "VALIDATION.json", successor_authority / "LOAD_MANIFEST.json"]
    write_json_new(successor_authority / "HASH_MANIFEST.json", {"files": {
        path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in files
    }})
    return report
