from __future__ import annotations

import hashlib
import json
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path, write_json_new
from .trading_ideas_blind_audit import ALLOWED_REASONS, compact_preview


AUDIT_VERSION = "market_cap_exception_blind_audit_v1"
COMPACT_LABELS = frozenset(("eligible", "ineligible", "needs_full_text"))
FULL_LABELS = frozenset(("eligible", "ineligible", "insufficient_information"))


def _write_jsonl_new(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
            count += 1
    return count


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def prepare(
    *,
    candidates_path: Path,
    article_features_path: Path,
    rendered_texts_path: Path,
    output_root: Path,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite audit output: {output_root}")
    candidates = [
        row
        for row in iter_jsonl(candidates_path)
        if str(row.get("current_label")) == "eligible"
        and "high_precision_candidate" in set(row.get("matched_candidate_grades") or ())
    ]
    if len(candidates) != 26 or len({str(row["source_id"]) for row in candidates}) != 26:
        raise ValueError(f"expected 26 unique eligible high-precision exceptions, got {len(candidates)}")
    by_source = {str(row["source_id"]): row for row in candidates}

    metadata: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(article_features_path):
        source_id = str(row["source_id"])
        if source_id not in by_source:
            continue
        if str(row["label"]) != "eligible":
            raise ValueError(f"feature label mismatch: {source_id}")
        metadata[source_id] = {
            "provider": str(row.get("provider") or ""),
            "tickers": list(row.get("tickers") or ()),
            "channels": list(row.get("channels") or ()),
            "provider_tags": list(row.get("provider_tags") or ()),
        }
    if set(metadata) != set(by_source):
        raise ValueError("candidate/article-feature membership mismatch")

    previews: dict[str, dict[str, Any]] = {}
    rendered_hashes: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts_path):
        source_id = str(row["source_id"])
        if source_id not in by_source:
            continue
        text = str(row.get("rendered_text") or "")
        digest = _digest(text)
        if digest != str(row.get("rendered_text_hash") or ""):
            raise ValueError(f"rendered text hash mismatch: {source_id}")
        previews[source_id] = compact_preview(text)
        rendered_hashes[source_id] = digest
    if set(previews) != set(by_source):
        raise ValueError("candidate/rendered-text membership mismatch")

    ordered = sorted(by_source, key=lambda value: _digest(f"{AUDIT_VERSION}|{value}"))
    controller_rows: list[dict[str, Any]] = []
    worker_rows: list[dict[str, Any]] = []
    for source_id in ordered:
        candidate = by_source[source_id]
        review_id = "MC" + _digest(f"{AUDIT_VERSION}|{source_id}")[:20]
        controller_rows.append({
            "review_id": review_id,
            **candidate,
            "rendered_text_sha256": rendered_hashes[source_id],
        })
        worker_rows.append({
            "review_id": review_id,
            "published_at_utc": str(candidate["published_at_utc"]),
            **metadata[source_id],
            **previews[source_id],
            "rendered_text_sha256": rendered_hashes[source_id],
        })

    controller_path = output_root / "CONTROLLER.jsonl"
    packet_one = output_root / "compact" / "PACKET_R1.jsonl"
    packet_two = output_root / "compact" / "PACKET_R2.jsonl"
    _write_jsonl_new(controller_path, controller_rows)
    _write_jsonl_new(packet_one, worker_rows)
    _write_jsonl_new(packet_two, worker_rows)
    instructions = {
        "objective": "Classify forecast eligibility from only supplied metadata, title, teaser, and first three sentences.",
        "eligible": "The preview independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The preview is analyst/investment opinion, technical/valuation material, price movement, a list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "needs_full_text": "The preview cannot safely establish whether a new material issuer event is independently reported.",
        "allowed_labels": sorted(COMPACT_LABELS),
        "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "required_fields": [
            "review_id", "manual_label", "confidence_probability", "reason_code",
            "rationale", "evidence_excerpt", "isolation_attestation",
        ],
        "blindness": "Do not inspect controller files, labels, market-cap paths, statistics, model outputs, prior reviews, or full source text.",
    }
    instructions_path = output_root / "COMPACT_REVIEW_INSTRUCTIONS.json"
    write_json_new(instructions_path, instructions)
    manifest = {
        "audit_version": AUDIT_VERSION,
        "status": "compact_packets_frozen",
        "articles": len(worker_rows),
        "inputs": {
            "candidates": {"path": str(candidates_path), "sha256": sha256_path(candidates_path)},
            "article_features": {"path": str(article_features_path), "sha256": sha256_path(article_features_path)},
            "rendered_texts": {"path": str(rendered_texts_path), "sha256": sha256_path(rendered_texts_path)},
        },
        "outputs": {
            "controller": {"path": str(controller_path), "sha256": sha256_path(controller_path)},
            "packet_r1": {"path": str(packet_one), "sha256": sha256_path(packet_one)},
            "packet_r2": {"path": str(packet_two), "sha256": sha256_path(packet_two)},
            "instructions": {"path": str(instructions_path), "sha256": sha256_path(instructions_path)},
        },
        "hidden_from_reviewers": [
            "source_id", "current_label", "split", "market_cap_bucket_set",
            "matched_candidate_features", "matched_candidate_grades", "candidate_statistics",
        ],
    }
    write_json_new(output_root / "PREPARE_MANIFEST.json", manifest)
    return manifest


def validate_review(*, packet_path: Path, review_path: Path) -> dict[str, Any]:
    packet = list(iter_jsonl(packet_path))
    reviews = list(iter_jsonl(review_path))
    if [str(row.get("review_id")) for row in reviews] != [str(row["review_id"]) for row in packet]:
        raise ValueError("review identity/order mismatch")
    for source, review in zip(packet, reviews, strict=True):
        review_id = str(review["review_id"])
        if set(review) != {
            "review_id", "manual_label", "confidence_probability", "reason_code",
            "rationale", "evidence_excerpt", "isolation_attestation",
        }:
            raise ValueError(f"review schema mismatch: {review_id}")
        if review["manual_label"] not in COMPACT_LABELS:
            raise ValueError(f"invalid compact label: {review_id}")
        if review["reason_code"] not in ALLOWED_REASONS:
            raise ValueError(f"invalid reason code: {review_id}")
        if not 0.0 <= float(review["confidence_probability"]) <= 1.0:
            raise ValueError(f"invalid confidence: {review_id}")
        rationale = str(review["rationale"]).strip()
        excerpt = str(review["evidence_excerpt"])
        if not rationale or len(rationale.split()) > 30:
            raise ValueError(f"invalid rationale: {review_id}")
        if not excerpt or len(excerpt) > 240 or excerpt not in str(source["preview_text"]):
            raise ValueError(f"evidence absent from preview: {review_id}")
        if review["isolation_attestation"] != {
            "used_only_supplied_packet": True,
            "used_external_context": False,
        }:
            raise ValueError(f"invalid isolation attestation: {review_id}")
    return {"status": "valid", "articles": len(packet), "review_sha256": sha256_path(review_path)}


def prepare_full_confirmation(*, output_root: Path, rendered_texts_path: Path) -> dict[str, Any]:
    packet = list(iter_jsonl(output_root / "compact" / "PACKET_R1.jsonl"))
    first = list(iter_jsonl(output_root / "compact" / "REVIEW_R1.jsonl"))
    second = list(iter_jsonl(output_root / "compact" / "REVIEW_R2.jsonl"))
    validate_review(packet_path=output_root / "compact" / "PACKET_R1.jsonl", review_path=output_root / "compact" / "REVIEW_R1.jsonl")
    validate_review(packet_path=output_root / "compact" / "PACKET_R2.jsonl", review_path=output_root / "compact" / "REVIEW_R2.jsonl")
    selected_ids = {
        str(a["review_id"])
        for a, b in zip(first, second, strict=True)
        if a["manual_label"] != b["manual_label"]
        or a["manual_label"] in {"ineligible", "needs_full_text"}
        or b["manual_label"] in {"ineligible", "needs_full_text"}
    }
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    compact_by_id = {str(row["review_id"]): row for row in packet}
    source_to_review = {
        str(controller[review_id]["source_id"]): review_id for review_id in selected_ids
    }
    rendered: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts_path):
        review_id = source_to_review.get(str(row["source_id"]))
        if review_id is None:
            continue
        text = str(row.get("rendered_text") or "")
        digest = _digest(text)
        if digest != str(row.get("rendered_text_hash") or ""):
            raise ValueError(f"full-text hash mismatch: {review_id}")
        if digest != str(controller[review_id]["rendered_text_sha256"]):
            raise ValueError(f"controller/full-text hash mismatch: {review_id}")
        rendered[review_id] = text
    if set(rendered) != selected_ids:
        raise ValueError("selected/full-text membership mismatch")
    rows = [{
        "review_id": review_id,
        "published_at_utc": compact_by_id[review_id]["published_at_utc"],
        "provider": compact_by_id[review_id]["provider"],
        "tickers": compact_by_id[review_id]["tickers"],
        "channels": compact_by_id[review_id]["channels"],
        "provider_tags": compact_by_id[review_id]["provider_tags"],
        "rendered_text": rendered[review_id],
        "rendered_text_sha256": controller[review_id]["rendered_text_sha256"],
    } for review_id in sorted(selected_ids, key=lambda value: _digest(f"{AUDIT_VERSION}|full|{value}"))]
    packet_path = output_root / "full" / "PACKET_R3.jsonl"
    _write_jsonl_new(packet_path, rows)
    instructions_path = output_root / "FULL_REVIEW_INSTRUCTIONS.json"
    write_json_new(instructions_path, {
        "objective": "Classify issuer forecast eligibility using only supplied metadata and complete rendered source text.",
        "eligible": "The article independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The article is opinion, technical/valuation material, price movement, list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "insufficient_information": "The complete rendered source still cannot establish whether a new material issuer event is reported.",
        "allowed_labels": sorted(FULL_LABELS),
        "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "required_fields": [
            "review_id", "manual_label", "confidence_probability", "reason_code",
            "rationale", "evidence_excerpt", "isolation_attestation",
        ],
        "blindness": "Do not inspect compact packets/votes, controller, labels, paths, statistics, models, prior reviews, repository, or internet.",
    })
    report = {
        "audit_version": AUDIT_VERSION,
        "status": "full_confirmation_packet_frozen",
        "articles": len(rows),
        "selection_policy": "all compact disagreements, needs-full-text votes, and proposed ineligible changes",
        "votes_hidden": True,
        "packet": {"path": str(packet_path), "sha256": sha256_path(packet_path)},
        "instructions": {"path": str(instructions_path), "sha256": sha256_path(instructions_path)},
    }
    write_json_new(output_root / "FULL_PREPARE_MANIFEST.json", report)
    return report


def validate_full_review(*, packet_path: Path, review_path: Path) -> dict[str, Any]:
    packet = list(iter_jsonl(packet_path))
    reviews = list(iter_jsonl(review_path))
    if [str(row.get("review_id")) for row in reviews] != [str(row["review_id"]) for row in packet]:
        raise ValueError("full review identity/order mismatch")
    for source, review in zip(packet, reviews, strict=True):
        review_id = str(review["review_id"])
        if set(review) != {
            "review_id", "manual_label", "confidence_probability", "reason_code",
            "rationale", "evidence_excerpt", "isolation_attestation",
        }:
            raise ValueError(f"full review schema mismatch: {review_id}")
        if review["manual_label"] not in FULL_LABELS:
            raise ValueError(f"invalid full label: {review_id}")
        if review["reason_code"] not in ALLOWED_REASONS:
            raise ValueError(f"invalid full reason: {review_id}")
        if not 0.0 <= float(review["confidence_probability"]) <= 1.0:
            raise ValueError(f"invalid full confidence: {review_id}")
        rationale = str(review["rationale"]).strip()
        excerpt = str(review["evidence_excerpt"])
        if not rationale or len(rationale.split()) > 30:
            raise ValueError(f"invalid full rationale: {review_id}")
        if not excerpt or len(excerpt) > 240 or excerpt not in str(source["rendered_text"]):
            raise ValueError(f"full evidence absent from source: {review_id}")
        if review["isolation_attestation"] != {
            "used_only_supplied_packet": True,
            "used_external_context": False,
        }:
            raise ValueError(f"invalid full isolation attestation: {review_id}")
    return {"status": "valid", "articles": len(packet), "review_sha256": sha256_path(review_path)}


def finalize(
    *,
    output_root: Path,
    parent_authority: Path,
    successor_authority: Path,
) -> dict[str, Any]:
    compact_packet = output_root / "compact" / "PACKET_R1.jsonl"
    first_path = output_root / "compact" / "REVIEW_R1.jsonl"
    second_path = output_root / "compact" / "REVIEW_R2.jsonl"
    full_packet = output_root / "full" / "PACKET_R3.jsonl"
    third_path = output_root / "full" / "REVIEW_R3.jsonl"
    fourth_path = output_root / "full" / "REVIEW_R4.jsonl"
    validate_review(packet_path=compact_packet, review_path=first_path)
    validate_review(packet_path=output_root / "compact" / "PACKET_R2.jsonl", review_path=second_path)
    validate_full_review(packet_path=full_packet, review_path=third_path)
    validate_full_review(packet_path=full_packet, review_path=fourth_path)
    compact = list(iter_jsonl(compact_packet))
    first = {str(row["review_id"]): row for row in iter_jsonl(first_path)}
    second = {str(row["review_id"]): row for row in iter_jsonl(second_path)}
    third = {str(row["review_id"]): row for row in iter_jsonl(third_path)}
    fourth = {str(row["review_id"]): row for row in iter_jsonl(fourth_path)}
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    decisions: list[dict[str, Any]] = []
    final_by_source: dict[str, str] = {}
    for packet_row in compact:
        review_id = str(packet_row["review_id"])
        hidden = controller[review_id]
        full_one = third.get(review_id)
        full_two = fourth.get(review_id)
        if full_one is None and full_two is None:
            if first[review_id]["manual_label"] != "eligible" or second[review_id]["manual_label"] != "eligible":
                raise ValueError(f"unconfirmed non-eligible compact decision: {review_id}")
            final_label = "eligible"
            decision_path = "two_compact_readers_agree_preserve"
        elif full_one is not None and full_two is not None:
            if full_one["manual_label"] == full_two["manual_label"]:
                final_label = str(full_one["manual_label"])
                decision_path = "two_full_text_readers_agree"
            else:
                final_label = "eligible"
                decision_path = "full_text_disagreement_preserve_parent"
        else:
            raise ValueError(f"incomplete full confirmation: {review_id}")
        source_id = str(hidden["source_id"])
        final_by_source[source_id] = final_label
        decisions.append({
            "source_id": source_id,
            "review_id": review_id,
            "original_label": "eligible",
            "final_label": final_label,
            "changed": final_label != "eligible",
            "decision_path": decision_path,
            "rendered_text_sha256": hidden["rendered_text_sha256"],
            "matched_candidate_features": hidden["matched_candidate_features"],
            "votes": [
                {**first[review_id], "stage": "compact", "reviewer_id": "R1"},
                {**second[review_id], "stage": "compact", "reviewer_id": "R2"},
                *([{**full_one, "stage": "full_text", "reviewer_id": "R3"}] if full_one else []),
                *([{**full_two, "stage": "full_confirmation", "reviewer_id": "R4"}] if full_two else []),
            ],
        })
    if len(decisions) != 26 or len(final_by_source) != 26:
        raise ValueError("final decision membership mismatch")
    decision_path = output_root / "FINAL_DECISIONS.jsonl"
    _write_jsonl_new(decision_path, sorted(decisions, key=lambda row: str(row["source_id"])))
    audit_report = {
        "audit_version": AUDIT_VERSION,
        "status": "complete",
        "articles": 26,
        "compact_votes": 52,
        "full_text_votes": len(third) + len(fourth),
        "final_labels": dict(sorted(Counter(row["final_label"] for row in decisions).items())),
        "changed_labels": sum(bool(row["changed"]) for row in decisions),
        "decision_paths": dict(sorted(Counter(row["decision_path"] for row in decisions).items())),
        "final_decisions": {"path": str(decision_path), "sha256": sha256_path(decision_path)},
    }
    write_json_new(output_root / "FINAL_REPORT.json", audit_report)
    seal(output_root)

    if not any(bool(row["changed"]) for row in decisions):
        return {
            "status": "audit_complete_no_confirmed_label_changes",
            "parent_authority": str(parent_authority),
            "successor_authority_created": False,
            "audit": audit_report,
        }

    if successor_authority.exists():
        raise FileExistsError(successor_authority)
    parent_manifest_path = parent_authority / "HASH_MANIFEST.json"
    parent_files = json.loads(parent_manifest_path.read_text(encoding="utf-8"))["files"]
    for name, metadata in parent_files.items():
        if sha256_path(parent_authority / name) != str(metadata["sha256"]):
            raise ValueError(f"parent authority hash mismatch: {name}")
    labels_name = "article_forecast_eligibility_labels.jsonl"
    inherited_data = sorted(
        name for name in parent_files
        if name.endswith(".jsonl") and name != labels_name
    )
    successor_authority.mkdir(parents=True)
    ledger_path = successor_authority / "market_cap_exception_correction_ledger.jsonl"
    _write_jsonl_new(ledger_path, sorted(decisions, key=lambda row: str(row["source_id"])))
    labels_path = successor_authority / labels_name
    seen: set[str] = set()
    row_count = 0
    updated = 0
    label_counts: Counter[str] = Counter()
    with labels_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in iter_jsonl(parent_authority / labels_name):
            row_count += 1
            source_id = str(row["source_id"])
            final_label = final_by_source.get(source_id)
            if final_label is not None:
                seen.add(source_id)
                if str(row["forecast_eligibility_label"]) != "eligible":
                    raise ValueError(f"parent candidate label drifted: {source_id}")
                if final_label != "eligible":
                    row = dict(row)
                    row.update({
                        "authority_class": "codex_multi_reader_full_text",
                        "authority_detail": AUDIT_VERSION,
                        "certification_level": "codex_adjudicated",
                        "decisive": True,
                        "forecast_eligibility_label": final_label,
                        "forecast_eligible": final_label == "eligible",
                        "usage_policy": "model_development_adjudicated",
                    })
                    updated += 1
            label_counts[str(row["forecast_eligibility_label"])] += 1
            handle.write(canonical_json(row) + "\n")
    if row_count != 361_695 or seen != set(final_by_source):
        raise ValueError("successor authority coverage mismatch")
    copied: list[Path] = []
    for name in inherited_data:
        destination = successor_authority / name
        shutil.copyfile(parent_authority / name, destination)
        copied.append(destination)
    validation = {
        "status": "passed",
        "article_rows": row_count,
        "reviewed_articles": len(decisions),
        "updated_primary_rows": updated,
        "candidate_coverage_complete": seen == set(final_by_source),
        "two_full_text_votes_for_every_change": all(
            len(row["votes"]) == 4 and row["votes"][-1]["manual_label"] == row["votes"][-2]["manual_label"]
            for row in decisions if row["changed"]
        ),
        "inherited_files_byte_identical": all(
            sha256_path(path) == sha256_path(parent_authority / path.name) for path in copied
        ),
        "parent_labels_unchanged": sha256_path(parent_authority / labels_name) == str(parent_files[labels_name]["sha256"]),
    }
    if not all(value for key, value in validation.items() if isinstance(value, bool)):
        raise ValueError(f"successor validation failed: {validation}")
    report = {
        "status": "scoped_correction_grade_successor",
        "authority_version": successor_authority.name,
        "parent_authority": str(parent_authority),
        "audit_version": AUDIT_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": "26 eligible exceptions in the high-precision market-cap interaction union",
        "reviewed_articles": len(decisions),
        "updated_primary_rows": updated,
        "authority_label_counts": dict(sorted(label_counts.items())),
        "limitations": [
            "Reviewer decisions are local Codex multi-reader adjudications, not human certification.",
            "Every changed label has two agreeing independent full-text decisions; disagreements preserve the parent label.",
        ],
    }
    load_manifest = {
        "dataset_version": successor_authority.name,
        "status": report["status"],
        "parent_authority": str(parent_authority),
        "audit_root": str(output_root),
        "primary_tables": {
            "article_forecast_eligibility": {"path": str(labels_path), "rows": row_count, "primary_key": ["source_id"]},
            "gold_issuer_sentiment": {"path": str(successor_authority / "gold_issuer_sentiment_labels.jsonl"), "rows": 16_983, "primary_key": ["unit_id"]},
        },
        "correction_ledger": str(ledger_path),
        "inherited_data_files": [str(path) for path in copied],
    }
    write_json_new(successor_authority / "REPORT.json", report)
    write_json_new(successor_authority / "VALIDATION.json", validation)
    write_json_new(successor_authority / "LOAD_MANIFEST.json", load_manifest)
    hash_files = [labels_path, ledger_path, *copied, successor_authority / "REPORT.json", successor_authority / "VALIDATION.json", successor_authority / "LOAD_MANIFEST.json"]
    write_json_new(successor_authority / "HASH_MANIFEST.json", {
        "files": {path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in hash_files}
    })
    return {**report, "audit": audit_report, "validation": validation}


def seal(output_root: Path) -> dict[str, Any]:
    validate_review(
        packet_path=output_root / "compact" / "PACKET_R1.jsonl",
        review_path=output_root / "compact" / "REVIEW_R1.jsonl",
    )
    validate_review(
        packet_path=output_root / "compact" / "PACKET_R2.jsonl",
        review_path=output_root / "compact" / "REVIEW_R2.jsonl",
    )
    validate_full_review(
        packet_path=output_root / "full" / "PACKET_R3.jsonl",
        review_path=output_root / "full" / "REVIEW_R3.jsonl",
    )
    validate_full_review(
        packet_path=output_root / "full" / "PACKET_R3.jsonl",
        review_path=output_root / "full" / "REVIEW_R4.jsonl",
    )
    decisions = list(iter_jsonl(output_root / "FINAL_DECISIONS.jsonl"))
    report = json.loads((output_root / "FINAL_REPORT.json").read_text(encoding="utf-8"))
    validation = {
        "status": "passed",
        "audit_version": AUDIT_VERSION,
        "controller_rows": sum(1 for _ in iter_jsonl(output_root / "CONTROLLER.jsonl")),
        "decision_rows": len(decisions),
        "unique_source_ids": len({str(row["source_id"]) for row in decisions}),
        "compact_review_rows": sum(1 for _ in iter_jsonl(output_root / "compact" / "REVIEW_R1.jsonl")) + sum(1 for _ in iter_jsonl(output_root / "compact" / "REVIEW_R2.jsonl")),
        "full_review_rows": sum(1 for _ in iter_jsonl(output_root / "full" / "REVIEW_R3.jsonl")) + sum(1 for _ in iter_jsonl(output_root / "full" / "REVIEW_R4.jsonl")),
        "report_complete": report.get("status") == "complete",
        "all_candidates_decided": len(decisions) == 26,
        "source_ids_unique": len({str(row["source_id"]) for row in decisions}) == 26,
    }
    if not all(value for key, value in validation.items() if isinstance(value, bool)):
        raise ValueError(f"audit seal validation failed: {validation}")
    validation_path = output_root / "VALIDATION.json"
    write_json_new(validation_path, validation)
    paths = [
        output_root / "PREPARE_MANIFEST.json",
        output_root / "FULL_PREPARE_MANIFEST.json",
        output_root / "CONTROLLER.jsonl",
        output_root / "COMPACT_REVIEW_INSTRUCTIONS.json",
        output_root / "FULL_REVIEW_INSTRUCTIONS.json",
        output_root / "compact" / "PACKET_R1.jsonl",
        output_root / "compact" / "PACKET_R2.jsonl",
        output_root / "compact" / "REVIEW_R1.jsonl",
        output_root / "compact" / "REVIEW_R2.jsonl",
        output_root / "full" / "PACKET_R3.jsonl",
        output_root / "full" / "REVIEW_R3.jsonl",
        output_root / "full" / "REVIEW_R4.jsonl",
        output_root / "FINAL_DECISIONS.jsonl",
        output_root / "FINAL_REPORT.json",
        validation_path,
    ]
    hash_manifest = {
        "audit_version": AUDIT_VERSION,
        "files": {
            str(path.relative_to(output_root)): {
                "bytes": path.stat().st_size,
                "sha256": sha256_path(path),
            }
            for path in paths
        },
    }
    write_json_new(output_root / "HASH_MANIFEST.json", hash_manifest)
    return validation
