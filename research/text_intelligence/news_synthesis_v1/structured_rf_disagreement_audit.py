from __future__ import annotations

import hashlib
import json
import math
import csv
import shutil
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path, write_json_new
from .trading_ideas_blind_audit import ALLOWED_REASONS, compact_preview


AUDIT_VERSION = "structured_rf_disagreement_blind_audit_v1"
SEED = 20260822
SAMPLE_SIZE = 1_000
COMPACT_REVIEWERS = ("R1", "R2", "R3")
COMPACT_PAIRS = (("R1", "R2"), ("R1", "R3"), ("R2", "R3"))
COMPACT_LABELS = frozenset(("eligible", "ineligible", "needs_full_text"))
FULL_LABELS = frozenset(("eligible", "ineligible", "insufficient_information"))
PACKET_ARTICLES = 50
PACKET_CHARACTERS = 80_000
FULL_PACKET_ARTICLES = 12
FULL_PACKET_CHARACTERS = 100_000


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


def _dominant_channel(channels: Sequence[Any]) -> str:
    values = {str(value).strip().casefold() for value in channels}
    for candidate in (
        "analyst ratings", "price target", "trading ideas", "earnings", "news",
    ):
        if candidate in values:
            return candidate.replace(" ", "_")
    return "other" if values else "none"


def _confidence_band(predicted_label: str, probability: float) -> str:
    confidence = probability if predicted_label == "eligible" else 1.0 - probability
    if confidence >= 0.90:
        return "extreme_gte_0_90"
    if confidence >= 0.80:
        return "high_0_80_0_90"
    if confidence >= 0.60:
        return "medium_0_60_0_80"
    return "boundary_lt_0_60"


def _audit_stratum(row: Mapping[str, Any], feature: Mapping[str, Any]) -> str:
    direction = f"{row['label']}_to_{row['predicted_label']}"
    month = str(row["published_at_utc"])[:7]
    confidence = _confidence_band(str(row["predicted_label"]), float(row["eligible_probability"]))
    channel = _dominant_channel(feature.get("channels") or ())
    return "|".join((direction, month, confidence, channel))


def _allocate_strata(counts: Mapping[str, int], sample_size: int) -> dict[str, int]:
    if sample_size < len(counts):
        raise ValueError("sample is too small to represent every nonempty stratum")
    allocation = {key: 1 for key in counts}
    remaining = sample_size - len(allocation)
    residual_total = sum(max(0, count - 1) for count in counts.values())
    raw = {
        key: remaining * max(0, count - 1) / residual_total if residual_total else 0.0
        for key, count in counts.items()
    }
    for key in allocation:
        addition = min(counts[key] - 1, int(math.floor(raw[key])))
        allocation[key] += addition
    left = sample_size - sum(allocation.values())
    order = sorted(
        counts,
        key=lambda key: (-(raw[key] - math.floor(raw[key])), _digest(f"{AUDIT_VERSION}|remainder|{key}")),
    )
    while left:
        progressed = False
        for key in order:
            if allocation[key] >= counts[key]:
                continue
            allocation[key] += 1
            left -= 1
            progressed = True
            if not left:
                break
        if not progressed:
            raise ValueError("could not complete sample allocation")
    return allocation


def _packetize(rows: Sequence[dict[str, Any]], *, text_field: str, article_limit: int, character_limit: int) -> list[list[dict[str, Any]]]:
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


def prepare(
    *,
    disagreements_path: Path,
    article_features_path: Path,
    market_cap_path: Path,
    rendered_texts_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    output_root.mkdir(parents=True)
    disagreements = {str(row["source_id"]): row for row in iter_jsonl(disagreements_path)}
    if len(disagreements) != 29_556:
        raise ValueError(f"unexpected disagreement population: {len(disagreements)}")

    features: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(article_features_path):
        source_id = str(row["source_id"])
        if source_id in disagreements:
            features[source_id] = row
    caps: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(market_cap_path):
        source_id = str(row["source_id"])
        if source_id in disagreements:
            caps[source_id] = row
    if set(features) != set(disagreements) or set(caps) != set(disagreements):
        raise ValueError("disagreement/feature/market-cap membership mismatch")

    by_stratum: dict[str, list[str]] = defaultdict(list)
    for source_id, row in disagreements.items():
        by_stratum[_audit_stratum(row, features[source_id])].append(source_id)
    counts = {key: len(values) for key, values in by_stratum.items()}
    allocation = _allocate_strata(counts, SAMPLE_SIZE)
    selected: list[str] = []
    for stratum, source_ids in sorted(by_stratum.items()):
        ordered = sorted(source_ids, key=lambda value: _digest(f"{AUDIT_VERSION}|sample|{value}"))
        selected.extend(ordered[:allocation[stratum]])
    if len(selected) != SAMPLE_SIZE or len(set(selected)) != SAMPLE_SIZE:
        raise ValueError("sample size or uniqueness mismatch")

    selected_set = set(selected)
    previews: dict[str, dict[str, Any]] = {}
    rendered_hashes: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts_path):
        source_id = str(row["source_id"])
        if source_id not in selected_set:
            continue
        text = str(row.get("rendered_text") or "")
        digest = _digest(text)
        if digest != str(row.get("rendered_text_hash") or ""):
            raise ValueError(f"rendered text hash mismatch: {source_id}")
        previews[source_id] = compact_preview(text)
        rendered_hashes[source_id] = digest
    if set(previews) != selected_set:
        raise ValueError("sample/rendered-text membership mismatch")

    ordered = sorted(selected, key=lambda value: _digest(f"{AUDIT_VERSION}|order|{value}"))
    controller_rows: list[dict[str, Any]] = []
    worker_rows: list[dict[str, Any]] = []
    assignment: dict[str, tuple[str, str]] = {}
    for position, source_id in enumerate(ordered):
        disagreement = disagreements[source_id]
        feature = features[source_id]
        cap = caps[source_id]
        stratum = _audit_stratum(disagreement, feature)
        review_id = "RD" + _digest(f"{AUDIT_VERSION}|{source_id}")[:20]
        reviewers = COMPACT_PAIRS[position % len(COMPACT_PAIRS)]
        assignment[review_id] = reviewers
        controller_rows.append({
            "review_id": review_id,
            "source_id": source_id,
            "current_label": str(disagreement["label"]),
            "model_label": str(disagreement["predicted_label"]),
            "eligible_probability": float(disagreement["eligible_probability"]),
            "source_split": str(disagreement["split"]),
            "audit_stratum": stratum,
            "stratum_population": counts[stratum],
            "stratum_sample": allocation[stratum],
            "population_weight": counts[stratum] / allocation[stratum],
            "compact_reviewers": list(reviewers),
            "rendered_text_sha256": rendered_hashes[source_id],
        })
        worker_rows.append({
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
            "market_cap_coverage": str(cap.get("market_cap_coverage") or "missing"),
            "market_cap_min_bucket": str(cap.get("market_cap_min_bucket") or "missing"),
            "market_cap_max_bucket": str(cap.get("market_cap_max_bucket") or "missing"),
            **previews[source_id],
            "rendered_text_sha256": rendered_hashes[source_id],
        })

    _write_jsonl_new(output_root / "CONTROLLER.jsonl", controller_rows)
    _write_jsonl_new(output_root / "COMPACT_SAMPLE.jsonl", worker_rows)
    worker_by_id = {str(row["review_id"]): row for row in worker_rows}
    reviewer_rows: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in COMPACT_REVIEWERS}
    for review_id, reviewers in assignment.items():
        for reviewer in reviewers:
            reviewer_rows[reviewer].append(worker_by_id[review_id])
    ledger = []
    for reviewer, rows in reviewer_rows.items():
        rows.sort(key=lambda row: _digest(f"{AUDIT_VERSION}|{reviewer}|{row['review_id']}"))
        packets = _packetize(rows, text_field="preview_text", article_limit=PACKET_ARTICLES, character_limit=PACKET_CHARACTERS)
        for number, packet in enumerate(packets, 1):
            packet_id = f"{reviewer}_C{number:03d}"
            path = output_root / "compact" / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(path, packet)
            ledger.append({
                "packet_id": packet_id, "reviewer_id": reviewer, "articles": len(packet),
                "packet_path": str(path), "packet_sha256": sha256_path(path),
            })
    write_json_new(output_root / "COMPACT_PACKET_LEDGER.json", {"packets": ledger})
    instructions = {
        "objective": "Classify forecast eligibility from only supplied metadata, title, teaser, and first three sentences.",
        "eligible": "The preview independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The preview is analyst/investment opinion, technical/valuation material, price movement, list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "needs_full_text": "The compact evidence cannot safely determine whether a new material issuer event is independently reported.",
        "allowed_labels": sorted(COMPACT_LABELS),
        "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "required_fields": [
            "review_id", "manual_label", "confidence_probability", "reason_code",
            "rationale", "evidence_excerpt", "isolation_attestation",
        ],
        "blindness": "Use only the assigned packet. Do not inspect controller files, current labels, model outputs, statistics, other packets, prior reviews, repository data, or internet sources.",
    }
    write_json_new(output_root / "COMPACT_REVIEW_INSTRUCTIONS.json", instructions)
    manifest = {
        "audit_version": AUDIT_VERSION, "status": "compact_packets_frozen",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "population": len(disagreements), "sample": len(worker_rows),
        "strata": len(counts), "compact_votes_required": 2 * len(worker_rows),
        "reviewer_load": {reviewer: len(rows) for reviewer, rows in reviewer_rows.items()},
        "inputs": {
            "disagreements": {"path": str(disagreements_path), "sha256": sha256_path(disagreements_path)},
            "article_features": {"path": str(article_features_path), "sha256": sha256_path(article_features_path)},
            "market_cap": {"path": str(market_cap_path), "sha256": sha256_path(market_cap_path)},
            "rendered_texts": {"path": str(rendered_texts_path), "sha256": sha256_path(rendered_texts_path)},
        },
        "hidden_from_reviewers": [
            "source_id", "current_label", "model_label", "eligible_probability",
            "source_split", "audit_stratum", "population_weight",
        ],
    }
    write_json_new(output_root / "PREPARE_MANIFEST.json", manifest)
    return manifest


def validate_review(*, packet_path: Path, review_path: Path, full_text: bool = False) -> dict[str, Any]:
    packet = list(iter_jsonl(packet_path))
    review = list(iter_jsonl(review_path))
    if [str(row.get("review_id")) for row in review] != [str(row["review_id"]) for row in packet]:
        raise ValueError("review identity/order mismatch")
    allowed = FULL_LABELS if full_text else COMPACT_LABELS
    text_field = "rendered_text" if full_text else "preview_text"
    for source, decision in zip(packet, review, strict=True):
        review_id = str(decision.get("review_id"))
        if set(decision) != {
            "review_id", "manual_label", "confidence_probability", "reason_code",
            "rationale", "evidence_excerpt", "isolation_attestation",
        }:
            raise ValueError(f"review schema mismatch: {review_id}")
        if decision["manual_label"] not in allowed:
            raise ValueError(f"invalid review label: {review_id}")
        if decision["reason_code"] not in ALLOWED_REASONS:
            raise ValueError(f"invalid reason code: {review_id}")
        if not 0.0 <= float(decision["confidence_probability"]) <= 1.0:
            raise ValueError(f"invalid confidence: {review_id}")
        rationale = str(decision["rationale"]).strip()
        excerpt = str(decision["evidence_excerpt"])
        if not rationale or len(rationale.split()) > 30:
            raise ValueError(f"invalid rationale: {review_id}")
        if not excerpt or len(excerpt) > 240 or excerpt not in str(source[text_field]):
            raise ValueError(f"review evidence absent from packet: {review_id}")
        if decision["isolation_attestation"] != {
            "used_only_supplied_packet": True, "used_external_context": False,
        }:
            raise ValueError(f"invalid isolation attestation: {review_id}")
    return {"status": "valid", "articles": len(review), "review_sha256": sha256_path(review_path)}


def collect_compact_reviews(*, output_root: Path) -> dict[str, Any]:
    ledger = json.loads((output_root / "COMPACT_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    decisions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    files = []
    for item in ledger:
        packet_path = Path(str(item["packet_path"]))
        review_path = output_root / "compact" / "reviews" / packet_path.name
        result = validate_review(packet_path=packet_path, review_path=review_path)
        reviewer = str(item["reviewer_id"])
        files.append({"reviewer_id": reviewer, "path": str(review_path), **result})
        for row in iter_jsonl(review_path):
            decisions[str(row["review_id"])].append({**row, "reviewer_id": reviewer})
    if set(decisions) != set(controller) or any(len(values) != 2 for values in decisions.values()):
        raise ValueError("compact two-reviewer coverage mismatch")
    if any(values[0]["reviewer_id"] == values[1]["reviewer_id"] for values in decisions.values()):
        raise ValueError("compact reviewer independence mismatch")
    summary = {
        "audit_version": AUDIT_VERSION, "status": "compact_reviews_complete",
        "articles": len(decisions), "votes": sum(map(len, decisions.values())),
        "exact_label_agreement": sum(values[0]["manual_label"] == values[1]["manual_label"] for values in decisions.values()),
        "needs_full_text_votes": sum(value["manual_label"] == "needs_full_text" for values in decisions.values() for value in values),
        "files": files,
    }
    write_json_new(output_root / "COMPACT_REVIEW_REPORT.json", summary)
    return summary


def prepare_full(*, output_root: Path, rendered_texts_path: Path) -> dict[str, Any]:
    collect_compact_reviews(output_root=output_root)
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    compact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted((output_root / "compact" / "reviews").glob("*.jsonl")):
        reviewer = path.stem.split("_", 1)[0]
        for row in iter_jsonl(path):
            compact[str(row["review_id"])].append({**row, "reviewer_id": reviewer})
    selected = {
        review_id for review_id, votes in compact.items()
        if votes[0]["manual_label"] != votes[1]["manual_label"]
        or any(vote["manual_label"] == "needs_full_text" for vote in votes)
    }
    source_to_review = {str(controller[review_id]["source_id"]): review_id for review_id in selected}
    rendered: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts_path):
        review_id = source_to_review.get(str(row["source_id"]))
        if review_id is None:
            continue
        text = str(row.get("rendered_text") or "")
        digest = _digest(text)
        if digest != str(row.get("rendered_text_hash") or "") or digest != str(controller[review_id]["rendered_text_sha256"]):
            raise ValueError(f"full-text hash mismatch: {review_id}")
        rendered[review_id] = text
    if set(rendered) != selected:
        raise ValueError("full-text escalation membership mismatch")
    compact_rows = {str(row["review_id"]): row for row in iter_jsonl(output_root / "COMPACT_SAMPLE.jsonl")}
    rows = [{
        "review_id": review_id,
        "published_at_utc": compact_rows[review_id]["published_at_utc"],
        "provider": compact_rows[review_id]["provider"],
        "tickers": compact_rows[review_id]["tickers"],
        "channels": compact_rows[review_id]["channels"],
        "provider_tags": compact_rows[review_id]["provider_tags"],
        "rendered_text": rendered[review_id],
        "rendered_text_sha256": controller[review_id]["rendered_text_sha256"],
    } for review_id in sorted(selected, key=lambda value: _digest(f"{AUDIT_VERSION}|full|{value}"))]
    ledger = []
    for reviewer in ("F1", "F2"):
        packets = _packetize(rows, text_field="rendered_text", article_limit=FULL_PACKET_ARTICLES, character_limit=FULL_PACKET_CHARACTERS)
        for number, packet in enumerate(packets, 1):
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
        "blindness": "Use only the assigned full-text packet. Do not inspect compact packets or votes, controller files, labels, model outputs, statistics, repository data, or internet sources.",
    })
    report = {
        "audit_version": AUDIT_VERSION, "status": "full_packets_frozen",
        "articles": len(rows), "full_votes_required": 2 * len(rows), "packets": len(ledger),
        "selection_policy": "compact label disagreement or at least one needs_full_text vote",
    }
    write_json_new(output_root / "FULL_PREPARE_REPORT.json", report)
    return report


def _weighted_rate(rows: Sequence[Mapping[str, Any]], predicate: Any) -> float:
    denominator = sum(float(row["population_weight"]) for row in rows)
    return sum(float(row["population_weight"]) * bool(predicate(row)) for row in rows) / denominator


def finalize(*, output_root: Path) -> dict[str, Any]:
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    compact: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted((output_root / "compact" / "reviews").glob("*.jsonl")):
        reviewer = path.stem.split("_", 1)[0]
        for row in iter_jsonl(path):
            compact[str(row["review_id"])].append({**row, "reviewer_id": reviewer})
    full: dict[str, list[dict[str, Any]]] = defaultdict(list)
    ledger = json.loads((output_root / "FULL_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    for item in ledger:
        packet_path = Path(str(item["packet_path"]))
        review_path = output_root / "full" / "reviews" / packet_path.name
        validate_review(packet_path=packet_path, review_path=review_path, full_text=True)
        reviewer = str(item["reviewer_id"])
        for row in iter_jsonl(review_path):
            full[str(row["review_id"])].append({**row, "reviewer_id": reviewer})
    escalated = set(full)
    if any(len(votes) != 2 or votes[0]["reviewer_id"] == votes[1]["reviewer_id"] for votes in full.values()):
        raise ValueError("full two-reviewer coverage mismatch")
    decisions = []
    for review_id, hidden in controller.items():
        compact_votes = compact[review_id]
        if review_id not in escalated:
            labels = {str(vote["manual_label"]) for vote in compact_votes}
            if len(labels) != 1 or "needs_full_text" in labels:
                raise ValueError(f"unresolved compact decision: {review_id}")
            final_label = labels.pop()
            decision_path = "two_compact_readers_agree"
            full_votes: list[dict[str, Any]] = []
        else:
            full_votes = full[review_id]
            labels = {str(vote["manual_label"]) for vote in full_votes}
            if len(labels) == 1 and "insufficient_information" not in labels:
                final_label = labels.pop()
                decision_path = "two_full_text_readers_agree"
            else:
                final_label = "unresolved"
                decision_path = "full_text_disagreement_or_insufficient"
        current = str(hidden["current_label"])
        model = str(hidden["model_label"])
        outcome = (
            "current_label_wrong" if final_label == model and final_label != current
            else "model_wrong" if final_label == current and final_label != model
            else "unresolved"
        )
        decisions.append({
            **hidden, "final_review_label": final_label, "audit_outcome": outcome,
            "decision_path": decision_path, "compact_votes": compact_votes, "full_votes": full_votes,
        })
    decisions.sort(key=lambda row: str(row["source_id"]))
    _write_jsonl_new(output_root / "FINAL_DECISIONS.jsonl", decisions)

    resolved = [row for row in decisions if row["audit_outcome"] != "unresolved"]
    by_direction = []
    for direction in sorted({f"{row['current_label']}_to_{row['model_label']}" for row in decisions}):
        subset = [row for row in decisions if f"{row['current_label']}_to_{row['model_label']}" == direction]
        resolved_subset = [row for row in subset if row["audit_outcome"] != "unresolved"]
        by_direction.append({
            "direction": direction, "sample": len(subset), "resolved": len(resolved_subset),
            "current_label_wrong": sum(row["audit_outcome"] == "current_label_wrong" for row in subset),
            "model_wrong": sum(row["audit_outcome"] == "model_wrong" for row in subset),
            "weighted_current_label_wrong_rate_among_resolved": _weighted_rate(
                resolved_subset, lambda row: row["audit_outcome"] == "current_label_wrong"
            ) if resolved_subset else None,
        })
    report = {
        "audit_version": AUDIT_VERSION, "status": "complete",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "population": 29_556, "sample": len(decisions),
        "resolved": len(resolved), "unresolved": len(decisions) - len(resolved),
        "outcomes": dict(sorted(Counter(str(row["audit_outcome"]) for row in decisions).items())),
        "weighted_current_label_wrong_rate_among_resolved": _weighted_rate(
            resolved, lambda row: row["audit_outcome"] == "current_label_wrong"
        ) if resolved else None,
        "weighted_model_wrong_rate_among_resolved": _weighted_rate(
            resolved, lambda row: row["audit_outcome"] == "model_wrong"
        ) if resolved else None,
        "by_direction": by_direction,
        "decision_paths": dict(sorted(Counter(str(row["decision_path"]) for row in decisions).items())),
        "final_decisions": {"path": str(output_root / "FINAL_DECISIONS.jsonl"), "sha256": sha256_path(output_root / "FINAL_DECISIONS.jsonl")},
        "interpretation": "Disagreements are audit candidates. No supervision labels are modified by this calibration audit.",
    }
    write_json_new(output_root / "FINAL_REPORT.json", report)
    return report


def _wilson_interval(rate: float, effective_n: float, z: float = 1.96) -> tuple[float, float]:
    if effective_n <= 0:
        return (0.0, 1.0)
    denominator = 1.0 + z * z / effective_n
    center = (rate + z * z / (2.0 * effective_n)) / denominator
    radius = z * math.sqrt(rate * (1.0 - rate) / effective_n + z * z / (4.0 * effective_n**2)) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def _ticker_count_bin(value: int) -> str:
    if value <= 0: return "0"
    if value == 1: return "1"
    if value == 2: return "2"
    if value <= 5: return "3_5"
    if value <= 10: return "6_10"
    return "gt_10"


def _size_bin(value: int) -> str:
    if value <= 200: return "lte_200"
    if value <= 500: return "201_500"
    if value <= 1_000: return "501_1000"
    if value <= 2_500: return "1001_2500"
    if value <= 5_000: return "2501_5000"
    return "gt_5000"


def _ordinal_bin(value: Any) -> str:
    if value is None: return "missing"
    number = int(value)
    if number == 1: return "1"
    if number == 2: return "2"
    if number <= 5: return "3_5"
    if number <= 10: return "6_10"
    return "gt_10"


def _recency_bin(value: Any) -> str:
    if value is None: return "missing"
    number = float(value)
    if number <= 300: return "lte_5m"
    if number <= 1_800: return "5_30m"
    if number <= 3_600: return "30_60m"
    if number <= 14_400: return "1_4h"
    if number <= 86_400: return "4_24h"
    return "gt_24h"


def analyze(*, output_root: Path) -> dict[str, Any]:
    decisions = list(iter_jsonl(output_root / "FINAL_DECISIONS.jsonl"))
    compact = {str(row["review_id"]): row for row in iter_jsonl(output_root / "COMPACT_SAMPLE.jsonl")}
    if len(decisions) != SAMPLE_SIZE or set(compact) != {str(row["review_id"]) for row in decisions}:
        raise ValueError("analysis decision/compact membership mismatch")

    expanded = []
    for decision in decisions:
        source = compact[str(decision["review_id"])]
        stratum_parts = str(decision["audit_stratum"]).split("|")
        channel_values = {str(value).strip().casefold() for value in source.get("channels") or ()}
        extreme = (
            str(decision["model_label"]) == "eligible" and float(decision["eligible_probability"]) >= 0.90
        ) or (
            str(decision["model_label"]) == "ineligible" and float(decision["eligible_probability"]) <= 0.10
        )
        earnings = bool(channel_values & {"earnings", "earnings beats", "earnings misses", "guidance"})
        expanded.append({
            **decision,
            "month": str(source["published_at_utc"])[:7],
            "confidence_band": stratum_parts[2],
            "dominant_channel": stratum_parts[3],
            "session_segment": str(source["session_segment"]),
            "market_cap_coverage": str(source["market_cap_coverage"]),
            "market_cap_max_bucket": str(source["market_cap_max_bucket"]),
            "ticker_count_bin": _ticker_count_bin(int(source["ticker_count"])),
            "rendered_chars_bin": _size_bin(int(source["rendered_chars"])),
            "session_ordinal_bin": _ordinal_bin(source.get("min_ticker_session_ordinal")),
            "ticker_recency_bin": _recency_bin(source.get("min_seconds_since_previous_ticker_news")),
            "priority_expansion_policy": "selected" if (
                str(decision["current_label"]) == "ineligible"
                and str(decision["model_label"]) == "eligible"
                and (extreme or earnings)
            ) else "not_selected",
            "analyst_price_target": "present" if channel_values & {"analyst ratings", "price target"} else "absent",
            "trading_ideas": "present" if "trading ideas" in channel_values else "absent",
            "channels": list(source.get("channels") or ()),
            "provider_tags": list(source.get("provider_tags") or ()),
        })

    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    dimensions = (
        "current_label", "model_label", "month", "confidence_band", "dominant_channel",
        "session_segment", "market_cap_coverage", "market_cap_max_bucket",
        "ticker_count_bin", "rendered_chars_bin", "session_ordinal_bin", "ticker_recency_bin",
        "priority_expansion_policy", "analyst_price_target", "trading_ideas",
    )
    for row in expanded:
        for dimension in dimensions:
            groups[(dimension, str(row[dimension]))].append(row)
        for channel in row["channels"]:
            groups[("channel", str(channel).strip().casefold())].append(row)
        for tag in row["provider_tags"]:
            groups[("provider_tag", str(tag).strip().casefold())].append(row)

    stats = []
    for (dimension, value), rows in groups.items():
        resolved = [row for row in rows if row["audit_outcome"] != "unresolved"]
        resolved_weight = sum(float(row["population_weight"]) for row in resolved)
        wrong_weight = sum(
            float(row["population_weight"]) for row in resolved
            if row["audit_outcome"] == "current_label_wrong"
        )
        rate = wrong_weight / resolved_weight if resolved_weight else 0.0
        weights = [float(row["population_weight"]) for row in resolved]
        effective_n = (sum(weights) ** 2 / sum(weight * weight for weight in weights)) if weights else 0.0
        lower, upper = _wilson_interval(rate, effective_n)
        stats.append({
            "dimension": dimension, "value": value, "sample": len(rows),
            "resolved": len(resolved), "unresolved": len(rows) - len(resolved),
            "weighted_population": sum(float(row["population_weight"]) for row in rows),
            "weighted_resolved_population": resolved_weight,
            "weighted_current_label_wrong": wrong_weight,
            "weighted_current_label_wrong_rate_among_resolved": rate,
            "kish_effective_resolved_sample": effective_n,
            "approx_wilson_95_lower": lower, "approx_wilson_95_upper": upper,
        })
    stats.sort(key=lambda row: (str(row["dimension"]), -float(row["weighted_population"]), str(row["value"])))
    stats_path = output_root / "AUDIT_GROUP_STATS.csv"
    with stats_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(stats[0]))
        writer.writeheader()
        writer.writerows(stats)

    candidates = [
        row for row in stats
        if int(row["resolved"]) >= 15
        and float(row["approx_wilson_95_lower"]) >= 0.75
        and int(row["unresolved"]) / int(row["sample"]) <= 0.10
    ]
    candidates.sort(key=lambda row: (-float(row["weighted_current_label_wrong"]), -float(row["approx_wilson_95_lower"])))
    candidates_path = output_root / "EXPANSION_CANDIDATE_PATHS.csv"
    with candidates_path.open("x", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(stats[0]))
        writer.writeheader()
        writer.writerows(candidates)

    total_weight = sum(float(row["population_weight"]) for row in expanded)
    wrong_weight = sum(float(row["population_weight"]) for row in expanded if row["audit_outcome"] == "current_label_wrong")
    unresolved_weight = sum(float(row["population_weight"]) for row in expanded if row["audit_outcome"] == "unresolved")
    resolved = [row for row in expanded if row["audit_outcome"] != "unresolved"]
    resolved_weights = [float(row["population_weight"]) for row in resolved]
    resolved_rate = wrong_weight / (total_weight - unresolved_weight)
    effective_n = sum(resolved_weights) ** 2 / sum(weight * weight for weight in resolved_weights)
    lower, upper = _wilson_interval(resolved_rate, effective_n)
    report = {
        "audit_version": AUDIT_VERSION, "status": "complete",
        "population": int(total_weight), "sample": len(expanded),
        "weighted_current_label_wrong": wrong_weight,
        "weighted_model_wrong": total_weight - wrong_weight - unresolved_weight,
        "weighted_unresolved": unresolved_weight,
        "weighted_current_label_wrong_rate_lower_bound": wrong_weight / total_weight,
        "weighted_current_label_wrong_rate_upper_bound": (wrong_weight + unresolved_weight) / total_weight,
        "weighted_current_label_wrong_rate_among_resolved": resolved_rate,
        "kish_effective_resolved_sample": effective_n,
        "approx_wilson_95_interval_among_resolved": [lower, upper],
        "group_stat_rows": len(stats), "expansion_candidate_paths": len(candidates),
        "top_expansion_candidates": candidates[:30],
        "limitations": [
            "The Wilson interval uses Kish effective sample size and is approximate for the stratified unequal-weight design.",
            "Provider tags and channels overlap; path rows are descriptive and correlated, not independent rules.",
            "This audit estimates error inside RF disagreements, not across all labeled news.",
            "No labels are changed by this analysis artifact.",
        ],
        "outputs": {
            "group_stats": {"path": str(stats_path), "sha256": sha256_path(stats_path)},
            "expansion_candidates": {"path": str(candidates_path), "sha256": sha256_path(candidates_path)},
        },
    }
    write_json_new(output_root / "ANALYSIS_REPORT.json", report)
    return report


def analyze_population(*, output_root: Path) -> dict[str, Any]:
    manifest = json.loads((output_root / "PREPARE_MANIFEST.json").read_text(encoding="utf-8"))
    disagreement_path = Path(str(manifest["inputs"]["disagreements"]["path"]))
    feature_path = Path(str(manifest["inputs"]["article_features"]["path"]))
    disagreements = {str(row["source_id"]): row for row in iter_jsonl(disagreement_path)}
    counts: Counter[str] = Counter()
    directions: Counter[tuple[str, str]] = Counter()
    priority = []
    for feature in iter_jsonl(feature_path):
        source_id = str(feature["source_id"])
        row = disagreements.get(source_id)
        if row is None:
            continue
        channels = {str(value).strip().casefold() for value in feature.get("channels") or ()}
        extreme = (
            str(row["predicted_label"]) == "eligible" and float(row["eligible_probability"]) >= 0.90
        ) or (
            str(row["predicted_label"]) == "ineligible" and float(row["eligible_probability"]) <= 0.10
        )
        earnings = bool(channels & {"earnings", "earnings beats", "earnings misses", "guidance"})
        analyst = bool(channels & {"analyst ratings", "price target"})
        trading = "trading ideas" in channels
        direction = f"{row['label']}_to_{row['predicted_label']}"
        flags = {
            "extreme": extreme,
            "earnings_guidance": earnings,
            "analyst_price_target": analyst,
            "trading_ideas": trading,
            "extreme_or_earnings": extreme or earnings,
            "extreme_and_earnings": extreme and earnings,
        }
        for name, enabled in flags.items():
            if enabled:
                counts[name] += 1
                directions[(name, direction)] += 1
        if direction == "ineligible_to_eligible" and (extreme or earnings):
            priority.append({
                "source_id": source_id,
                "published_at_utc": str(row["published_at_utc"]),
                "eligible_probability": float(row["eligible_probability"]),
                "extreme_probability": extreme,
                "earnings_or_guidance_channel": earnings,
                "channels": sorted(channels),
                "provider_tags": list(feature.get("provider_tags") or ()),
            })
    priority.sort(key=lambda row: (str(row["published_at_utc"]), str(row["source_id"])))
    queue_path = output_root / "PRIORITY_EXPANSION_CONTROLLER.jsonl"
    _write_jsonl_new(queue_path, priority)
    report = {
        "audit_version": AUDIT_VERSION, "status": "complete",
        "population": len(disagreements),
        "exact_candidate_counts": dict(sorted(counts.items())),
        "exact_counts_by_direction": {
            f"{name}|{direction}": count
            for (name, direction), count in sorted(directions.items())
        },
        "priority_policy": "current ineligible, model eligible, and either model confidence at least 0.90 or an earnings/guidance channel",
        "priority_articles": len(priority),
        "priority_controller": {"path": str(queue_path), "sha256": sha256_path(queue_path)},
        "interpretation": "Controller candidates for expanded blind review; not automatic corrections.",
    }
    write_json_new(output_root / "POPULATION_EXPANSION_REPORT.json", report)
    return report


def validate_artifacts(*, output_root: Path) -> dict[str, Any]:
    controller = list(iter_jsonl(output_root / "CONTROLLER.jsonl"))
    compact_sample = list(iter_jsonl(output_root / "COMPACT_SAMPLE.jsonl"))
    compact_ledger = json.loads((output_root / "COMPACT_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    full_ledger = json.loads((output_root / "FULL_PACKET_LEDGER.json").read_text(encoding="utf-8"))["packets"]
    final = list(iter_jsonl(output_root / "FINAL_DECISIONS.jsonl"))
    compact_packet_rows = compact_review_rows = full_packet_rows = full_review_rows = 0
    for item in compact_ledger:
        packet = Path(str(item["packet_path"]))
        review = output_root / "compact" / "reviews" / packet.name
        validation = validate_review(packet_path=packet, review_path=review)
        compact_packet_rows += int(item["articles"])
        compact_review_rows += int(validation["articles"])
    for item in full_ledger:
        packet = Path(str(item["packet_path"]))
        review = output_root / "full" / "reviews" / packet.name
        validation = validate_review(packet_path=packet, review_path=review, full_text=True)
        full_packet_rows += int(item["articles"])
        full_review_rows += int(validation["articles"])
    worker_forbidden = {"source_id", "current_label", "model_label", "eligible_probability", "source_split", "population_weight"}
    final_report = json.loads((output_root / "FINAL_REPORT.json").read_text(encoding="utf-8"))
    analysis_report = json.loads((output_root / "ANALYSIS_REPORT.json").read_text(encoding="utf-8"))
    population_report = json.loads((output_root / "POPULATION_EXPANSION_REPORT.json").read_text(encoding="utf-8"))
    priority_rows = sum(1 for _ in iter_jsonl(output_root / "PRIORITY_EXPANSION_CONTROLLER.jsonl"))
    checks = {
        "controller_rows": len(controller) == SAMPLE_SIZE,
        "compact_sample_rows": len(compact_sample) == SAMPLE_SIZE,
        "compact_packet_count": len(compact_ledger) == 42,
        "compact_packet_votes": compact_packet_rows == 2 * SAMPLE_SIZE,
        "compact_review_votes": compact_review_rows == 2 * SAMPLE_SIZE,
        "full_packet_count": len(full_ledger) == 34,
        "full_packet_votes": full_packet_rows == 212,
        "full_review_votes": full_review_rows == 212,
        "final_rows": len(final) == SAMPLE_SIZE,
        "unique_review_ids": len({str(row["review_id"]) for row in controller}) == SAMPLE_SIZE,
        "compact_reviewer_pairs_independent": all(len(set(row["compact_reviewers"])) == 2 for row in controller),
        "worker_packets_hide_controller_fields": all(not (worker_forbidden & set(row)) for row in compact_sample),
        "final_report_complete": final_report.get("status") == "complete",
        "analysis_report_complete": analysis_report.get("status") == "complete",
        "population_report_complete": population_report.get("status") == "complete",
        "priority_expansion_rows": priority_rows == 12_099,
        "outcome_total": sum(final_report["outcomes"].values()) == SAMPLE_SIZE,
    }
    if not all(checks.values()):
        raise ValueError(f"audit validation failed: {checks}")
    validation = {
        "audit_version": AUDIT_VERSION, "status": "passed", "checks": checks,
        "compact_votes": compact_review_rows, "full_votes": full_review_rows,
        "final_decisions": len(final),
    }
    write_json_new(output_root / "VALIDATION.json", validation)
    files = sorted(
        path for path in output_root.rglob("*")
        if path.is_file() and path.name != "HASH_MANIFEST.json"
    )
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


def promote_successor_authority(
    *, audit_root: Path, parent_authority: Path, successor_authority: Path,
) -> dict[str, Any]:
    if successor_authority.exists():
        raise FileExistsError(successor_authority)
    validation = json.loads((audit_root / "VALIDATION.json").read_text(encoding="utf-8"))
    if validation.get("status") != "passed":
        raise ValueError("audit authority is not validated")
    audit_hashes = json.loads((audit_root / "HASH_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    for relative, metadata in audit_hashes.items():
        path = audit_root / relative
        if sha256_path(path) != str(metadata["sha256"]):
            raise ValueError(f"audit hash mismatch: {relative}")
    parent_hashes = json.loads((parent_authority / "HASH_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    for name, metadata in parent_hashes.items():
        if sha256_path(parent_authority / name) != str(metadata["sha256"]):
            raise ValueError(f"parent authority hash mismatch: {name}")

    decisions = {str(row["source_id"]): row for row in iter_jsonl(audit_root / "FINAL_DECISIONS.jsonl")}
    if len(decisions) != SAMPLE_SIZE:
        raise ValueError("unexpected audited decision population")
    parent_labels = parent_authority / "article_forecast_eligibility_labels.jsonl"
    successor_authority.mkdir(parents=True)
    labels_path = successor_authority / parent_labels.name
    ledger_path = successor_authority / "structured_rf_disagreement_audit_ledger.jsonl"
    ledger_rows = []
    seen: set[str] = set()
    rows = label_changes = metadata_upgrades = 0
    label_counts: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()
    with labels_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in iter_jsonl(parent_labels):
            rows += 1
            source_id = str(row["source_id"])
            decision = decisions.get(source_id)
            if decision is not None:
                seen.add(source_id)
                original = str(row["forecast_eligibility_label"])
                if original != str(decision["current_label"]):
                    raise ValueError(f"audited parent label drifted: {source_id}")
                outcome = str(decision["audit_outcome"])
                outcomes[outcome] += 1
                final = str(decision["final_review_label"])
                if outcome != "unresolved":
                    if final not in {"eligible", "ineligible"}:
                        raise ValueError(f"invalid resolved audit label: {source_id}")
                    row = dict(row)
                    row.update({
                        "authority_class": (
                            "codex_multi_reader_blind_compact"
                            if str(decision["decision_path"]) == "two_compact_readers_agree"
                            else "codex_multi_reader_full_text"
                        ),
                        "authority_detail": AUDIT_VERSION,
                        "certification_level": "codex_adjudicated",
                        "decisive": True,
                        "forecast_eligibility_label": final,
                        "forecast_eligible": final == "eligible",
                        "human_certified": False,
                        "usage_policy": "model_development_adjudicated",
                    })
                    metadata_upgrades += 1
                    label_changes += final != original
                ledger_rows.append({
                    "source_id": source_id, "review_id": str(decision["review_id"]),
                    "original_label": original,
                    "final_label": original if outcome == "unresolved" else final,
                    "changed": outcome != "unresolved" and final != original,
                    "audit_outcome": outcome,
                    "decision_path": str(decision["decision_path"]),
                    "rendered_text_sha256": str(decision["rendered_text_sha256"]),
                    "compact_votes": decision["compact_votes"], "full_votes": decision["full_votes"],
                })
            label_counts[str(row["forecast_eligibility_label"])] += 1
            handle.write(canonical_json(row) + "\n")
    if rows != 361_695 or seen != set(decisions):
        raise ValueError("successor authority membership mismatch")
    _write_jsonl_new(ledger_path, sorted(ledger_rows, key=lambda row: str(row["source_id"])))

    inherited_names = [
        name for name in parent_hashes
        if name not in {
            "article_forecast_eligibility_labels.jsonl", "REPORT.json", "VALIDATION.json",
            "LOAD_MANIFEST.json", "HASH_MANIFEST.json",
        }
    ]
    copied = []
    for name in sorted(inherited_names):
        destination = successor_authority / name
        shutil.copyfile(parent_authority / name, destination)
        copied.append(destination)
    sentiment = successor_authority / "gold_issuer_sentiment_labels.jsonl"
    report = {
        "status": "scoped_correction_grade_successor",
        "authority_version": successor_authority.name,
        "parent_authority": str(parent_authority), "audit_root": str(audit_root),
        "reviewed_articles": len(decisions), "resolved_articles": metadata_upgrades,
        "unresolved_articles": outcomes["unresolved"], "label_changes": label_changes,
        "audit_outcomes": dict(sorted(outcomes.items())),
        "authority_label_counts": dict(sorted(label_counts.items())),
        "sentiment_byte_identical": sha256_path(sentiment) == sha256_path(parent_authority / sentiment.name),
        "limitations": [
            "Local Codex multi-reader adjudication is not human certification.",
            "Unresolved audit rows preserve the parent label and authority metadata.",
        ],
    }
    successor_validation = {
        "status": "passed", "article_rows": rows, "reviewed_rows": len(decisions),
        "resolved_rows": metadata_upgrades, "unresolved_rows": outcomes["unresolved"],
        "label_changes": label_changes, "coverage_complete": seen == set(decisions),
        "sentiment_sha256_equal": report["sentiment_byte_identical"],
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
    files = [labels_path, ledger_path, *copied, successor_authority / "REPORT.json",
             successor_authority / "VALIDATION.json", successor_authority / "LOAD_MANIFEST.json"]
    write_json_new(successor_authority / "HASH_MANIFEST.json", {
        "files": {path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in files}
    })
    return report
