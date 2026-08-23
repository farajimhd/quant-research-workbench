from __future__ import annotations

import json
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path, write_json_new
from .structured_rf_disagreement_audit import COMPACT_LABELS, FULL_LABELS, validate_review
from .structured_rf_priority_funnel import (
    FAST_QA_FRACTION,
    FULL_PACKET_ARTICLES,
    FULL_PACKET_CHARACTERS,
    PRIMARY_REVIEWERS,
    _digest,
    _dominant_channel,
    _packetize,
    _wilson_lower,
    _write_jsonl_new,
    collect_primary,
)
from .trading_ideas_blind_audit import ALLOWED_REASONS, compact_preview


FUNNEL_VERSION = "structured_rf_reverse_disagreement_blind_audit_v1"
EXPECTED_POPULATION = 16_680
FAST_QA_MIN_AGREEMENT = 0.985
FAST_QA_MIN_WILSON_LOWER = 0.975
FAST_QA_MAX_NEEDS_FULL = 0.005
QA_REVIEWERS = ("Q1", "Q2")
FULL_AGENT_REVIEWERS = ("A1", "A2", "A3", "A4", "A5", "A6")
LEDGER_NAMES = (
    "provider_filter_correction_ledger.jsonl",
    "provider_path_exception_correction_ledger.jsonl",
    "provider_path_exception_refinement_ledger.jsonl",
    "structured_rf_disagreement_audit_ledger.jsonl",
    "structured_rf_priority_blind_review_ledger.jsonl",
    "trading_ideas_correction_ledger.jsonl",
)


def _verify_manifest(root: Path) -> None:
    validation = json.loads((root / "VALIDATION.json").read_text(encoding="utf-8"))
    if validation.get("status") != "passed":
        raise ValueError(f"unvalidated input: {root}")
    manifest = json.loads((root / "HASH_MANIFEST.json").read_text(encoding="utf-8"))
    for relative, metadata in manifest["files"].items():
        if sha256_path(root / relative) != str(metadata["sha256"]):
            raise ValueError(f"input hash mismatch: {root / relative}")


def _reviewed_ledgers(authority_root: Path, candidate_ids: set[str]) -> dict[str, list[dict[str, Any]]]:
    reviewed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for name in LEDGER_NAMES:
        path = authority_root / name
        if not path.exists():
            continue
        for row in iter_jsonl(path):
            source_id = str(row["source_id"])
            if source_id not in candidate_ids:
                continue
            digest = row.get("rendered_text_sha256") or row.get("rendered_text_hash")
            final = str(row.get("final_label") or "")
            if digest and final in {"eligible", "ineligible"}:
                reviewed[source_id].append({
                    "ledger": name, "rendered_text_sha256": str(digest),
                    "final_label": final, "review_id": str(row.get("review_id") or ""),
                    "decision_path": str(row.get("decision_path") or "prior_correction_grade_review"),
                })
    return reviewed


def prepare_primary(
    *, predictions_path: Path, authority_root: Path, feature_path: Path,
    rendered_texts_path: Path, output_root: Path,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    _verify_manifest(predictions_path.parent)
    _verify_manifest(authority_root)
    predictions = {str(row["source_id"]): row for row in iter_jsonl(predictions_path)}
    if len(predictions) != EXPECTED_POPULATION:
        raise ValueError(f"unexpected disagreement population: {len(predictions)}")
    candidate_ids = set(predictions)

    authority = {}
    for row in iter_jsonl(authority_root / "article_forecast_eligibility_labels.jsonl"):
        source_id = str(row["source_id"])
        if source_id in candidate_ids:
            authority[source_id] = row
    if set(authority) != candidate_ids:
        raise ValueError("authority membership mismatch")
    if any(str(authority[source_id]["forecast_eligibility_label"]) != str(predictions[source_id]["label"])
           for source_id in candidate_ids):
        raise ValueError("prediction/current authority drift")

    features = {}
    for row in iter_jsonl(feature_path):
        source_id = str(row["source_id"])
        if source_id in candidate_ids:
            features[source_id] = row
    if set(features) != candidate_ids:
        raise ValueError("feature membership mismatch")
    rendered = {}
    previews = {}
    for row in iter_jsonl(rendered_texts_path):
        source_id = str(row["source_id"])
        if source_id not in candidate_ids:
            continue
        text = str(row.get("rendered_text") or "")
        digest = _digest(text)
        if digest != str(row.get("rendered_text_hash") or ""):
            raise ValueError(f"rendered hash mismatch: {source_id}")
        rendered[source_id] = {"text": text, "sha256": digest}
        previews[source_id] = compact_preview(text, sentence_count=2)
    if set(rendered) != candidate_ids:
        raise ValueError("rendered membership mismatch")

    prior = _reviewed_ledgers(authority_root, candidate_ids)
    reused_rows = []
    reused_ids = set()
    for source_id, records in prior.items():
        matching = [row for row in records if row["rendered_text_sha256"] == rendered[source_id]["sha256"]]
        if not matching:
            continue
        labels = {str(row["final_label"]) for row in matching}
        current = str(authority[source_id]["forecast_eligibility_label"])
        if labels != {current}:
            raise ValueError(f"conflicting prior review authority: {source_id}")
        reused_ids.add(source_id)
        reused_rows.append({
            "source_id": source_id, "current_label": current,
            "model_label": str(predictions[source_id]["predicted_label"]),
            "eligible_probability": float(predictions[source_id]["eligible_probability"]),
            "final_review_label": current, "audit_outcome": "model_wrong",
            "decision_path": "reused_exact_correction_grade_review",
            "rendered_text_sha256": rendered[source_id]["sha256"],
            "prior_reviews": matching,
        })

    fresh_ids = candidate_ids - reused_ids
    reviewer_rows: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in PRIMARY_REVIEWERS}
    controller_rows = []
    ordered = sorted(fresh_ids, key=lambda value: _digest(f"{FUNNEL_VERSION}|order|{value}"))
    for position, source_id in enumerate(ordered):
        feature = features[source_id]
        prediction = predictions[source_id]
        current = str(authority[source_id]["forecast_eligibility_label"])
        model = str(prediction["predicted_label"])
        if current == model:
            raise ValueError(f"non-disagreement entered campaign: {source_id}")
        reviewer = PRIMARY_REVIEWERS[position % len(PRIMARY_REVIEWERS)]
        review_id = "RR" + _digest(f"{FUNNEL_VERSION}|{source_id}")[:20]
        channels = {str(value).strip().casefold() for value in feature.get("channels") or ()}
        controller_rows.append({
            "review_id": review_id, "source_id": source_id,
            "current_label": current, "model_label": model,
            "eligible_probability": float(prediction["eligible_probability"]),
            "disagreement_direction": f"{current}_to_{model}",
            "source_split": str(feature["split"]),
            "month": str(feature["published_at_text"])[:7],
            "dominant_channel": _dominant_channel(channels),
            "primary_reviewer": reviewer,
            "rendered_text_sha256": rendered[source_id]["sha256"],
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
            "rendered_text_sha256": rendered[source_id]["sha256"],
        })

    output_root.mkdir(parents=True)
    _write_jsonl_new(output_root / "CONTROLLER.jsonl", controller_rows)
    _write_jsonl_new(output_root / "REUSED_DECISIONS.jsonl", sorted(reused_rows, key=lambda row: row["source_id"]))
    packet_ledger = []
    for reviewer, rows in reviewer_rows.items():
        rows.sort(key=lambda row: _digest(f"{FUNNEL_VERSION}|{reviewer}|{row['review_id']}"))
        for number, packet in enumerate(_packetize(rows), 1):
            packet_id = f"{reviewer}_P{number:03d}"
            path = output_root / "primary" / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(path, packet)
            packet_ledger.append({
                "packet_id": packet_id, "reviewer_id": reviewer,
                "articles": len(packet), "packet_path": str(path),
                "packet_sha256": sha256_path(path),
            })
    write_json_new(output_root / "PRIMARY_PACKET_LEDGER.json", {"packets": packet_ledger})
    write_json_new(output_root / "PRIMARY_REVIEW_INSTRUCTIONS.json", {
        "objective": "Classify forecast eligibility using only supplied metadata, title, teaser, and first two sentences.",
        "eligible": "The preview independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The preview is opinion, technical/valuation material, price movement, list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "needs_full_text": "Compact evidence cannot safely determine whether a new material issuer event is independently reported.",
        "allowed_labels": sorted(COMPACT_LABELS), "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "required_fields": ["review_id", "manual_label", "confidence_probability", "reason_code", "rationale", "evidence_excerpt", "isolation_attestation"],
        "blindness": "Use only the assigned packet. Do not inspect controller files, current labels, model outputs, probabilities, splits, prior reviews, repository data, or internet sources.",
    })
    manifest = {
        "funnel_version": FUNNEL_VERSION, "status": "primary_packets_frozen",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "disagreement_population": len(candidate_ids), "reused_exact_reviews": len(reused_ids),
        "fresh_primary_articles": len(fresh_ids), "packets": len(packet_ledger),
        "reviewer_load": {key: len(value) for key, value in reviewer_rows.items()},
        "inputs": {
            "predictions": {"path": str(predictions_path), "sha256": sha256_path(predictions_path)},
            "authority": {"path": str(authority_root), "hash_manifest_sha256": sha256_path(authority_root / "HASH_MANIFEST.json")},
            "features": {"path": str(feature_path), "sha256": sha256_path(feature_path)},
            "rendered_texts": {"path": str(rendered_texts_path), "sha256": sha256_path(rendered_texts_path)},
        },
        "hidden_from_reviewers": ["source_id", "current_label", "model_label", "eligible_probability", "disagreement_direction", "source_split", "month", "dominant_channel", "primary_reviewer"],
    }
    write_json_new(output_root / "PREPARE_MANIFEST.json", manifest)
    return manifest


def collect_primary(*, output_root: Path) -> dict[str, Any]:
    ledger = json.loads((output_root / "PRIMARY_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    decisions: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for item in ledger:
        packet = Path(str(item["packet_path"]))
        review = output_root / "primary" / "reviews" / packet.name
        validate_review(packet_path=packet, review_path=review)
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
        "decisions": {"path": str(path), "sha256": sha256_path(path)},
    }
    write_json_new(output_root / "PRIMARY_REPORT.json", report)
    return report


def prepare_qa(*, output_root: Path) -> dict[str, Any]:
    primary_report = json.loads((output_root / "PRIMARY_REPORT.json").read_text(encoding="utf-8"))
    if primary_report.get("status") != "primary_reviews_complete":
        raise ValueError("primary reviews incomplete")
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    primary = {str(row["review_id"]): row for row in iter_jsonl(output_root / "PRIMARY_DECISIONS.jsonl")}
    worker_rows = {}
    batch_risk = set()
    packet_ledger = json.loads((output_root / "PRIMARY_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    for item in packet_ledger:
        packet_number = int(str(item["packet_id"]).rsplit("P", 1)[1])
        for row in iter_jsonl(Path(str(item["packet_path"]))):
            review_id = str(row["review_id"])
            worker_rows[review_id] = row
            if 5 <= packet_number <= 15:
                batch_risk.add(review_id)
    if set(controller) != set(primary) or set(primary) != set(worker_rows):
        raise ValueError("QA source membership mismatch")

    mandatory = {
        review_id for review_id, row in primary.items()
        if str(row["manual_label"]) == "needs_full_text"
        or float(row["confidence_probability"]) < 0.90
        or str(row["manual_label"]) != str(controller[review_id]["model_label"])
        or review_id in batch_risk
    }
    fast = set(primary) - mandatory
    strata: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for review_id in fast:
        hidden = controller[review_id]
        probability = float(hidden["eligible_probability"])
        confidence_band = "extreme" if probability <= 0.10 or probability >= 0.90 else "ordinary"
        strata[(str(hidden["disagreement_direction"]), str(hidden["month"]),
                str(hidden["dominant_channel"]), confidence_band)].append(review_id)
    sampled = set()
    for stratum, review_ids in strata.items():
        ordered = sorted(review_ids, key=lambda value: _digest(f"{FUNNEL_VERSION}|fast-qa|{stratum}|{value}"))
        count = max(1, int(len(ordered) * FAST_QA_FRACTION + 0.999999))
        sampled.update(ordered[:count])
    selected = mandatory | sampled
    reviewer_rows: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in QA_REVIEWERS}
    selection_rows = []
    for position, review_id in enumerate(sorted(selected, key=lambda value: _digest(f"{FUNNEL_VERSION}|qa|{value}"))):
        primary_reviewer = str(primary[review_id]["reviewer_id"])
        qa_reviewer = QA_REVIEWERS[position % len(QA_REVIEWERS)]
        reason = "mandatory_model_opposition_ambiguity_or_low_confidence" if review_id in mandatory else "stratified_fast_lane_qa"
        reviewer_rows[qa_reviewer].append(worker_rows[review_id])
        selection_rows.append({
            "review_id": review_id, "primary_reviewer": primary_reviewer,
            "qa_reviewer": qa_reviewer, "selection_reason": reason,
        })
    _write_jsonl_new(output_root / "QA_SELECTION_LEDGER.jsonl", selection_rows)
    qa_packets = []
    for reviewer, rows in reviewer_rows.items():
        rows.sort(key=lambda row: _digest(f"{FUNNEL_VERSION}|qa|{reviewer}|{row['review_id']}"))
        for number, packet in enumerate(_packetize(rows), 1):
            packet_id = f"{reviewer}_Q{number:03d}"
            path = output_root / "qa" / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(path, packet)
            qa_packets.append({
                "packet_id": packet_id, "reviewer_id": reviewer, "articles": len(packet),
                "packet_path": str(path), "packet_sha256": sha256_path(path),
            })
    write_json_new(output_root / "QA_PACKET_LEDGER.json", {"packets": qa_packets})
    report = {
        "funnel_version": FUNNEL_VERSION, "status": "qa_packets_frozen",
        "mandatory_articles": len(mandatory), "fast_lane_articles": len(fast),
        "batch_risk_articles_forced_to_qa": len(batch_risk),
        "fast_lane_qa_articles": len(sampled), "qa_articles": len(selected),
        "packets": len(qa_packets), "reviewer_load": {key: len(value) for key, value in reviewer_rows.items()},
    }
    write_json_new(output_root / "QA_PREPARE_REPORT.json", report)
    return report


def collect_qa_general(*, output_root: Path) -> dict[str, Any]:
    packet_ledger = json.loads((output_root / "QA_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    selection = {str(row["review_id"]): row for row in iter_jsonl(output_root / "QA_SELECTION_LEDGER.jsonl")}
    primary = {str(row["review_id"]): row for row in iter_jsonl(output_root / "PRIMARY_DECISIONS.jsonl")}
    qa = {}
    for item in packet_ledger:
        packet = Path(str(item["packet_path"])); review = output_root / "qa" / "reviews" / packet.name
        validate_review(packet_path=packet, review_path=review)
        for row in iter_jsonl(review):
            qa[str(row["review_id"])] = {**row, "reviewer_id": str(item["reviewer_id"])}
    if set(qa) != set(selection):
        raise ValueError("QA coverage mismatch")
    if any(qa[key]["reviewer_id"] == primary[key]["reviewer_id"] for key in qa):
        raise ValueError("QA independence mismatch")
    path = output_root / "QA_DECISIONS.jsonl"
    _write_jsonl_new(path, (qa[key] for key in sorted(qa)))
    fast_ids = [key for key, row in selection.items() if row["selection_reason"] == "stratified_fast_lane_qa"]
    agreements = sum(str(qa[key]["manual_label"]) == str(primary[key]["manual_label"]) for key in fast_ids)
    needs_full = sum(str(qa[key]["manual_label"]) == "needs_full_text" for key in fast_ids)
    rate = agreements / len(fast_ids) if fast_ids else 0.0
    needs_rate = needs_full / len(fast_ids) if fast_ids else 1.0
    lower = _wilson_lower(agreements, len(fast_ids))
    certified = rate >= FAST_QA_MIN_AGREEMENT and lower >= FAST_QA_MIN_WILSON_LOWER and needs_rate <= FAST_QA_MAX_NEEDS_FULL
    report = {
        "funnel_version": FUNNEL_VERSION, "status": "qa_reviews_complete",
        "qa_articles": len(qa), "fast_lane_qa_articles": len(fast_ids),
        "fast_lane_agreements": agreements, "fast_lane_agreement_rate": rate,
        "fast_lane_wilson_95_lower": lower, "fast_lane_needs_full_text": needs_full,
        "fast_lane_needs_full_text_rate": needs_rate, "fast_lane_certified": certified,
        "decisions": {"path": str(path), "sha256": sha256_path(path)},
    }
    write_json_new(output_root / "QA_REPORT.json", report)
    return report


def prepare_qa_expansion(*, output_root: Path) -> dict[str, Any]:
    qa_report = json.loads((output_root / "QA_REPORT.json").read_text(encoding="utf-8"))
    if qa_report.get("fast_lane_certified"):
        raise ValueError("fast lane already certified; expansion is unnecessary")
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    selected = {str(row["review_id"]) for row in iter_jsonl(output_root / "QA_SELECTION_LEDGER.jsonl")}
    remaining = set(controller) - selected
    worker_rows: dict[str, dict[str, Any]] = {}
    for item in json.loads((output_root / "PRIMARY_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]:
        for row in iter_jsonl(Path(str(item["packet_path"]))):
            worker_rows[str(row["review_id"])] = row
    reviewer_rows: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in QA_REVIEWERS}
    ledger_rows = []
    for position, review_id in enumerate(sorted(remaining, key=lambda value: _digest(f"{FUNNEL_VERSION}|qa-expand|{value}"))):
        reviewer = QA_REVIEWERS[position % len(QA_REVIEWERS)]
        reviewer_rows[reviewer].append(worker_rows[review_id])
        ledger_rows.append({"review_id": review_id, "qa_reviewer": reviewer, "selection_reason": "failed_fast_lane_full_expansion"})
    _write_jsonl_new(output_root / "QA_EXPANSION_SELECTION_LEDGER.jsonl", ledger_rows)
    packets = []
    for reviewer, rows in reviewer_rows.items():
        for number, packet in enumerate(_packetize(rows), 1):
            packet_id = f"{reviewer}_X{number:03d}"
            path = output_root / "qa_expansion" / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(path, packet)
            packets.append({"packet_id": packet_id, "reviewer_id": reviewer, "articles": len(packet),
                            "packet_path": str(path), "packet_sha256": sha256_path(path)})
    write_json_new(output_root / "QA_EXPANSION_PACKET_LEDGER.json", {"packets": packets})
    report = {"funnel_version": FUNNEL_VERSION, "status": "qa_expansion_packets_frozen",
              "articles": len(remaining), "packets": len(packets),
              "reviewer_load": {key: len(value) for key, value in reviewer_rows.items()}}
    write_json_new(output_root / "QA_EXPANSION_PREPARE_REPORT.json", report)
    return report


def collect_qa_expansion(*, output_root: Path) -> dict[str, Any]:
    existing = {str(row["review_id"]): row for row in iter_jsonl(output_root / "QA_DECISIONS.jsonl")}
    primary = {str(row["review_id"]): row for row in iter_jsonl(output_root / "PRIMARY_DECISIONS.jsonl")}
    expansion: dict[str, dict[str, Any]] = {}
    ledger = json.loads((output_root / "QA_EXPANSION_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    for item in ledger:
        packet = Path(str(item["packet_path"]))
        review = output_root / "qa_expansion" / "reviews" / packet.name
        validate_review(packet_path=packet, review_path=review)
        for row in iter_jsonl(review):
            review_id = str(row["review_id"])
            if review_id in expansion:
                raise ValueError(f"duplicate expanded QA review: {review_id}")
            expansion[review_id] = {**row, "reviewer_id": str(item["reviewer_id"])}
    combined = {**existing, **expansion}
    if set(combined) != set(primary) or set(existing) & set(expansion):
        raise ValueError("expanded QA membership mismatch")
    path = output_root / "QA_DECISIONS_COMPLETE.jsonl"
    _write_jsonl_new(path, (combined[key] for key in sorted(combined)))
    agreements = sum(str(combined[key]["manual_label"]) == str(primary[key]["manual_label"]) for key in combined)
    needs_full = sum("needs_full_text" in {str(combined[key]["manual_label"]), str(primary[key]["manual_label"])} for key in combined)
    report = {
        "funnel_version": FUNNEL_VERSION, "status": "all_fresh_rows_double_reviewed",
        "articles": len(combined), "original_qa_articles": len(existing), "expanded_qa_articles": len(expansion),
        "compact_agreements": agreements, "compact_agreement_rate": agreements / len(combined),
        "compact_disagreements": len(combined) - agreements, "either_needs_full_text": needs_full,
        "all_rows_double_reviewed": True, "decisions": {"path": str(path), "sha256": sha256_path(path)},
    }
    write_json_new(output_root / "QA_COMPLETE_REPORT.json", report)
    return report


def _qa_batch_risk_ids(output_root: Path) -> set[str]:
    result = set()
    ledger = json.loads((output_root / "QA_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    for item in ledger:
        packet_number = int(str(item["packet_id"]).rsplit("Q", 1)[1])
        if 5 <= packet_number <= 9:
            result.update(str(row["review_id"]) for row in iter_jsonl(Path(str(item["packet_path"]))))
    return result


def prepare_full(*, output_root: Path, rendered_texts_path: Path) -> dict[str, Any]:
    qa_report = json.loads((output_root / "QA_COMPLETE_REPORT.json").read_text(encoding="utf-8"))
    if not qa_report.get("all_rows_double_reviewed"):
        raise ValueError("expanded QA is incomplete")
    primary = {str(row["review_id"]): row for row in iter_jsonl(output_root / "PRIMARY_DECISIONS.jsonl")}
    qa = {str(row["review_id"]): row for row in iter_jsonl(output_root / "QA_DECISIONS_COMPLETE.jsonl")}
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    selected = {
        key for key, second in qa.items()
        if str(primary[key]["manual_label"]) != str(second["manual_label"])
        or "needs_full_text" in {str(primary[key]["manual_label"]), str(second["manual_label"])}
    } | _qa_batch_risk_ids(output_root)
    source_to_review = {str(controller[key]["source_id"]): key for key in selected}
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
    compact_rows: dict[str, dict[str, Any]] = {}
    packet_ledger = json.loads((output_root / "PRIMARY_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    for item in packet_ledger:
        for row in iter_jsonl(Path(str(item["packet_path"]))):
            compact_rows[str(row["review_id"])] = row
    reviewer_rows: dict[str, list[dict[str, Any]]] = {"F1": [], "F2": []}
    for position, review_id in enumerate(sorted(selected, key=lambda value: _digest(f"{FUNNEL_VERSION}|full|{value}"))):
        source = compact_rows[review_id]
        reviewer_rows["F1" if position % 2 == 0 else "F2"].append({
            "review_id": review_id, "published_at_utc": source["published_at_utc"],
            "provider": source["provider"], "tickers": source["tickers"],
            "channels": source["channels"], "provider_tags": source["provider_tags"],
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
            ledger.append({"packet_id": packet_id, "reviewer_id": reviewer, "articles": len(packet),
                           "packet_path": str(path), "packet_sha256": sha256_path(path)})
    write_json_new(output_root / "FULL_PACKET_LEDGER.json", {"packets": ledger})
    write_json_new(output_root / "FULL_REVIEW_INSTRUCTIONS.json", {
        "objective": "Classify issuer forecast eligibility using only supplied metadata and complete rendered source text.",
        "eligible": "The article independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The article is opinion, technical/valuation material, price movement, list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "insufficient_information": "The complete rendered source still cannot establish whether a new material issuer event is reported.",
        "allowed_labels": sorted(FULL_LABELS), "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "required_fields": ["review_id", "manual_label", "confidence_probability", "reason_code", "rationale", "evidence_excerpt", "isolation_attestation"],
        "blindness": "Use only the assigned full-text packet. Do not inspect compact packets or votes, controller files, labels, model outputs, statistics, repository data, or internet sources.",
    })
    report = {"funnel_version": FUNNEL_VERSION, "status": "full_packets_frozen", "articles": len(selected),
              "packets": len(ledger), "reviewer_load": {key: len(value) for key, value in reviewer_rows.items()}}
    write_json_new(output_root / "FULL_PREPARE_REPORT.json", report)
    return report


def prepare_full_agent_assignments(*, output_root: Path) -> dict[str, Any]:
    ledger = json.loads((output_root / "FULL_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    loads = {reviewer: {"bytes": 0, "articles": 0, "packets": []} for reviewer in FULL_AGENT_REVIEWERS}
    for item in sorted(ledger, key=lambda row: Path(str(row["packet_path"])).stat().st_size, reverse=True):
        reviewer = min(FULL_AGENT_REVIEWERS, key=lambda key: (loads[key]["bytes"], loads[key]["articles"], key))
        packet = Path(str(item["packet_path"]))
        assignment = {
            "packet_id": str(item["packet_id"]), "packet_path": str(packet),
            "review_path": str(output_root / "full" / "reviews" / packet.name),
            "articles": int(item["articles"]), "packet_sha256": str(item["packet_sha256"]),
            "bytes": packet.stat().st_size,
        }
        loads[reviewer]["packets"].append(assignment)
        loads[reviewer]["bytes"] += assignment["bytes"]
        loads[reviewer]["articles"] += assignment["articles"]
    all_assignments = []
    for reviewer, payload in loads.items():
        payload["packets"].sort(key=lambda row: str(row["packet_id"]))
        manifest = {"funnel_version": FUNNEL_VERSION, "reviewer_id": reviewer, **payload}
        path = output_root / "full" / "assignments" / f"{reviewer}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_new(path, manifest)
        all_assignments.extend({"reviewer_id": reviewer, **row} for row in payload["packets"])
    if len(all_assignments) != len(ledger) or len({row["packet_id"] for row in all_assignments}) != len(ledger):
        raise ValueError("full assignment coverage mismatch")
    report = {
        "funnel_version": FUNNEL_VERSION, "status": "full_agent_assignments_frozen",
        "packets": len(all_assignments), "reviewers": len(FULL_AGENT_REVIEWERS),
        "loads": {reviewer: {"packets": len(payload["packets"]), "articles": payload["articles"], "bytes": payload["bytes"]}
                  for reviewer, payload in loads.items()},
    }
    write_json_new(output_root / "FULL_AGENT_ASSIGNMENT_REPORT.json", report)
    return report


def finalize(*, output_root: Path) -> dict[str, Any]:
    qa_report = json.loads((output_root / "QA_COMPLETE_REPORT.json").read_text(encoding="utf-8"))
    if not qa_report.get("all_rows_double_reviewed"):
        raise ValueError("expanded QA is incomplete")
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    primary = {str(row["review_id"]): row for row in iter_jsonl(output_root / "PRIMARY_DECISIONS.jsonl")}
    qa = {str(row["review_id"]): row for row in iter_jsonl(output_root / "QA_DECISIONS_COMPLETE.jsonl")}
    full = {}
    actual_reviewer_by_packet = {}
    assignment_report = output_root / "FULL_AGENT_ASSIGNMENT_REPORT.json"
    if assignment_report.exists():
        for reviewer in FULL_AGENT_REVIEWERS:
            manifest = json.loads((output_root / "full" / "assignments" / f"{reviewer}.json").read_text(encoding="utf-8"))
            for row in manifest["packets"]:
                packet_id = str(row["packet_id"])
                if packet_id in actual_reviewer_by_packet:
                    raise ValueError(f"duplicate actual full reviewer assignment: {packet_id}")
                actual_reviewer_by_packet[packet_id] = reviewer
    full_ledger = json.loads((output_root / "FULL_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    for item in full_ledger:
        packet = Path(str(item["packet_path"])); review = output_root / "full" / "reviews" / packet.name
        validate_review(packet_path=packet, review_path=review, full_text=True)
        reviewer_id = actual_reviewer_by_packet.get(str(item["packet_id"]), str(item["reviewer_id"]))
        for row in iter_jsonl(review):
            full[str(row["review_id"])] = {**row, "reviewer_id": reviewer_id}
    expected_full = {
        key for key, second in qa.items()
        if str(primary[key]["manual_label"]) != str(second["manual_label"])
        or "needs_full_text" in {str(primary[key]["manual_label"]), str(second["manual_label"])}
    } | _qa_batch_risk_ids(output_root)
    if set(full) != expected_full:
        raise ValueError("full coverage mismatch")
    decisions = []
    for review_id, hidden in controller.items():
        first = primary[review_id]; second = qa.get(review_id); final_vote = full.get(review_id)
        if final_vote is not None:
            label = str(final_vote["manual_label"])
            final_label = "unresolved" if label == "insufficient_information" else label
            path = "fresh_full_text_resolution" if final_label != "unresolved" else "full_text_insufficient"
        elif second is not None:
            labels = {str(first["manual_label"]), str(second["manual_label"])}
            if len(labels) != 1 or "needs_full_text" in labels:
                raise ValueError(f"unresolved compact decision: {review_id}")
            final_label = labels.pop(); path = "independent_compact_agreement"
        else:
            final_label = str(first["manual_label"])
            if final_label == "needs_full_text" or float(first["confidence_probability"]) < 0.90:
                raise ValueError(f"invalid fast-lane decision: {review_id}")
            path = "certified_fast_lane_primary"
        current = str(hidden["current_label"]); model = str(hidden["model_label"])
        outcome = "current_label_wrong" if final_label == model else "model_wrong" if final_label == current else "unresolved"
        decisions.append({**hidden, "final_review_label": final_label, "audit_outcome": outcome,
                          "decision_path": path, "primary_vote": first, "qa_vote": second, "full_vote": final_vote})
    reused = list(iter_jsonl(output_root / "REUSED_DECISIONS.jsonl"))
    all_rows = sorted([*decisions, *reused], key=lambda row: str(row["source_id"]))
    if len(all_rows) != EXPECTED_POPULATION or len({str(row["source_id"]) for row in all_rows}) != EXPECTED_POPULATION:
        raise ValueError("final population mismatch")
    final_path = output_root / "FINAL_DECISIONS.jsonl"
    _write_jsonl_new(final_path, all_rows)
    report = {
        "funnel_version": FUNNEL_VERSION, "status": "complete",
        "population": len(all_rows), "reused_exact_reviews": len(reused),
        "fresh_reviews": len(decisions),
        "outcomes": dict(sorted(Counter(str(row["audit_outcome"]) for row in all_rows).items())),
        "labels": dict(sorted(Counter(str(row["final_review_label"]) for row in all_rows).items())),
        "decision_paths": dict(sorted(Counter(str(row["decision_path"]) for row in all_rows).items())),
        "primary_votes": len(primary), "qa_votes": len(qa), "full_votes": len(full),
        "final_decisions": {"path": str(final_path), "sha256": sha256_path(final_path)},
    }
    write_json_new(output_root / "FINAL_REPORT.json", report)
    return report


def validate_artifacts(*, output_root: Path) -> dict[str, Any]:
    final = list(iter_jsonl(output_root / "FINAL_DECISIONS.jsonl"))
    report = json.loads((output_root / "FINAL_REPORT.json").read_text(encoding="utf-8"))
    checks = {
        "population": len(final) == EXPECTED_POPULATION,
        "unique_sources": len({str(row["source_id"]) for row in final}) == EXPECTED_POPULATION,
        "report_complete": report.get("status") == "complete",
        "outcome_total": sum(report["outcomes"].values()) == EXPECTED_POPULATION,
        "all_fresh_rows_double_reviewed": bool(json.loads((output_root / "QA_COMPLETE_REPORT.json").read_text(encoding="utf-8"))["all_rows_double_reviewed"]),
    }
    if not all(checks.values()):
        raise ValueError(f"validation failed: {checks}")
    validation = {"funnel_version": FUNNEL_VERSION, "status": "passed", "checks": checks}
    write_json_new(output_root / "VALIDATION.json", validation)
    files = sorted(path for path in output_root.rglob("*") if path.is_file() and path.name != "HASH_MANIFEST.json")
    write_json_new(output_root / "HASH_MANIFEST.json", {"funnel_version": FUNNEL_VERSION, "files": {
        str(path.relative_to(output_root)).replace("\\", "/"): {"bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in files
    }})
    return validation


def promote_successor_authority(
    *, audit_root: Path, parent_authority: Path, successor_authority: Path,
) -> dict[str, Any]:
    if successor_authority.exists():
        raise FileExistsError(successor_authority)
    validation = json.loads((audit_root / "VALIDATION.json").read_text(encoding="utf-8"))
    if validation.get("status") != "passed":
        raise ValueError("reverse disagreement audit is not validated")
    for relative, metadata in json.loads((audit_root / "HASH_MANIFEST.json").read_text(encoding="utf-8"))["files"].items():
        if sha256_path(audit_root / relative) != str(metadata["sha256"]):
            raise ValueError(f"audit hash mismatch: {relative}")
    parent_hashes = json.loads((parent_authority / "HASH_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    for name, metadata in parent_hashes.items():
        if sha256_path(parent_authority / name) != str(metadata["sha256"]):
            raise ValueError(f"parent hash mismatch: {name}")
    decisions = {str(row["source_id"]): row for row in iter_jsonl(audit_root / "FINAL_DECISIONS.jsonl")}
    if len(decisions) != EXPECTED_POPULATION:
        raise ValueError("unexpected reverse-audit decision population")

    successor_authority.mkdir(parents=True)
    parent_labels = parent_authority / "article_forecast_eligibility_labels.jsonl"
    labels_path = successor_authority / parent_labels.name
    ledger_path = successor_authority / "structured_rf_reverse_disagreement_blind_audit_ledger.jsonl"
    seen: set[str] = set()
    ledger_rows = []
    rows = changes = fresh_resolved = unresolved = reused = 0
    label_counts: Counter[str] = Counter()
    with labels_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in iter_jsonl(parent_labels):
            rows += 1
            source_id = str(row["source_id"])
            decision = decisions.get(source_id)
            if decision is not None:
                seen.add(source_id)
                original = str(row["forecast_eligibility_label"])
                if original != str(decision["current_label"]):
                    raise ValueError(f"parent label drifted: {source_id}")
                final_label = str(decision["final_review_label"])
                is_reused = str(decision["decision_path"]) == "reused_exact_correction_grade_review"
                if is_reused:
                    reused += 1
                elif final_label in {"eligible", "ineligible"}:
                    row = dict(row)
                    row.update({
                        "authority_class": "codex_adaptive_blind_review_funnel",
                        "authority_detail": FUNNEL_VERSION,
                        "certification_level": "codex_adjudicated", "decisive": True,
                        "forecast_eligibility_label": final_label,
                        "forecast_eligible": final_label == "eligible", "human_certified": False,
                        "usage_policy": "model_development_adjudicated",
                    })
                    fresh_resolved += 1
                    changes += final_label != original
                else:
                    unresolved += 1
                ledger_rows.append({
                    "source_id": source_id, "review_id": decision.get("review_id"),
                    "original_label": original,
                    "final_label": original if final_label == "unresolved" else final_label,
                    "changed": final_label in {"eligible", "ineligible"} and final_label != original,
                    "audit_outcome": decision["audit_outcome"], "decision_path": decision["decision_path"],
                    "rendered_text_sha256": decision["rendered_text_sha256"],
                    "primary_vote": decision.get("primary_vote"), "qa_vote": decision.get("qa_vote"),
                    "full_vote": decision.get("full_vote"), "prior_reviews": decision.get("prior_reviews"),
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
        shutil.copyfile(parent_authority / name, destination)
        copied.append(destination)
    sentiment = successor_authority / "gold_issuer_sentiment_labels.jsonl"
    sentiment_equal = sha256_path(sentiment) == sha256_path(parent_authority / sentiment.name)
    report = {
        "status": "scoped_correction_grade_successor", "authority_version": successor_authority.name,
        "parent_authority": str(parent_authority), "audit_root": str(audit_root),
        "audited_disagreements": len(decisions), "reused_exact_reviews": reused,
        "fresh_resolved_articles": fresh_resolved, "unresolved_articles": unresolved,
        "label_changes": changes, "authority_label_counts": dict(sorted(label_counts.items())),
        "sentiment_byte_identical": sentiment_equal,
        "limitations": ["Local Codex adjudication is not human certification.", "Unresolved rows preserve the parent label and metadata."],
    }
    successor_validation = {
        "status": "passed", "article_rows": rows, "audited_rows": len(decisions),
        "reused_rows": reused, "fresh_resolved_rows": fresh_resolved,
        "unresolved_rows": unresolved, "label_changes": changes,
        "coverage_complete": seen == set(decisions), "sentiment_sha256_equal": sentiment_equal,
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
