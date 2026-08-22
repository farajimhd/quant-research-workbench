from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path, write_json_new


AUDIT_VERSION = "news_synthesis_trading_ideas_blind_audit_v1"
DEFAULT_CANDIDATES = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\trading_ideas_review_candidates_v2\CONTROLLER_REVIEW_CANDIDATES.jsonl"
)
DEFAULT_ARTICLE_FEATURES = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_feature_audit_v3_corrected\ARTICLE_FEATURES.jsonl"
)
DEFAULT_RENDERED_TEXTS = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_rf_comparison_v1\rendered_texts.jsonl"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\trading_ideas_blind_audit_v1"
)
DEFAULT_PARENT_AUTHORITY = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_provider_filter_v1"
)
DEFAULT_SUCCESSOR_AUTHORITY = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_trading_ideas_v1"
)
PACKET_CHARACTER_LIMIT = 80_000
PACKET_ARTICLE_LIMIT = 80
FULL_PACKET_CHARACTER_LIMIT = 80_000
FULL_PACKET_ARTICLE_LIMIT = 20
OVERSIZED_FULL_TEXT_CHARACTERS = 300_000
OVERSIZED_CHUNK_CHARACTERS = 60_000
EXPECTED_CANDIDATES = 6_896
ALLOWED_LABELS = {"eligible", "ineligible", "needs_full_text"}
ALLOWED_FULL_LABELS = {"eligible", "ineligible", "insufficient_information"}
ALLOWED_REASONS = {
    "new_material_event", "issuer_guidance", "financing_capital", "regulatory_clinical",
    "operations_contract", "earnings_current", "analyst_or_investment_idea",
    "technical_or_valuation", "price_movement_only", "screener_or_list",
    "scheduled_preview", "recap_or_background", "generic_macro", "routine_notice",
    "insufficient_preview", "insufficient_full_text", "other_eligible", "other_ineligible",
}
ELIGIBLE_QC_SAMPLE_MODULUS = 10


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def packet_order(review_id: str) -> str:
    return _digest(f"{AUDIT_VERSION}|compact-order|{review_id}")


def _field(text: str, name: str) -> str:
    prefix = name + ":"
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def compact_preview(text: str, *, sentence_count: int = 3) -> dict[str, Any]:
    title = _field(text, "Title")
    teaser = _field(text, "Teaser")
    body_lines: list[str] = []
    source_seen = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Source ["):
            source_seen = True
            continue
        if not source_seen or not line or line.startswith(("Title:", "Teaser:", "Source [")):
            continue
        body_lines.append(line)
    body = " ".join(body_lines)
    sentences = [value.strip() for value in re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", body) if value.strip()]
    opening = sentences[:sentence_count]
    preview_text = "\n".join((f"Title: {title}", f"Teaser: {teaser}", *(f"Opening: {value}" for value in opening)))
    return {
        "title": title,
        "teaser": teaser,
        "opening_sentences": opening,
        "preview_text": preview_text,
        "preview_sha256": _digest(preview_text),
    }


def _write_jsonl_new(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")


def _packetize(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return _packetize_by(rows, text_key="preview_text", article_limit=PACKET_ARTICLE_LIMIT, character_limit=PACKET_CHARACTER_LIMIT)


def _packetize_by(
    rows: list[dict[str, Any]], *, text_key: str, article_limit: int, character_limit: int,
) -> list[list[dict[str, Any]]]:
    packets: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    characters = 0
    for row in rows:
        size = len(str(row[text_key]))
        if current and (len(current) >= article_limit or characters + size > character_limit):
            packets.append(current)
            current = []
            characters = 0
        current.append(row)
        characters += size
    if current:
        packets.append(current)
    return packets


def prepare(*, candidates_path: Path, article_features_path: Path, rendered_texts_path: Path, output_root: Path) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    output_root.mkdir(parents=True)
    candidates = {str(row["source_id"]): row for row in iter_jsonl(candidates_path)}
    if len(candidates) != EXPECTED_CANDIDATES:
        raise ValueError(f"expected {EXPECTED_CANDIDATES} candidates")
    article_metadata: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(article_features_path):
        source_id = str(row["source_id"])
        if source_id in candidates:
            article_metadata[source_id] = {
                "provider": str(row.get("provider") or ""),
                "tickers": list(row.get("tickers") or ()),
            }
    if article_metadata.keys() != candidates.keys():
        raise ValueError("candidate/feature membership mismatch")
    previews: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(rendered_texts_path):
        source_id = str(row["source_id"])
        if source_id not in candidates:
            continue
        text = str(row["rendered_text"])
        digest = _digest(text)
        if digest != str(row["rendered_text_hash"]) or digest != str(candidates[source_id]["rendered_text_sha256"]):
            raise ValueError(f"rendered hash mismatch: {source_id}")
        previews[source_id] = compact_preview(text)
    if previews.keys() != candidates.keys():
        raise ValueError("candidate/rendered membership mismatch")
    ordered_ids = sorted(candidates, key=lambda source_id: packet_order(str(candidates[source_id]["review_id"])))
    controller_rows: list[dict[str, Any]] = []
    worker_rows: list[dict[str, Any]] = []
    for source_id in ordered_ids:
        candidate = candidates[source_id]
        preview = previews[source_id]
        controller_rows.append({
            **candidate,
            **article_metadata[source_id],
            "preview_sha256": preview["preview_sha256"],
        })
        worker_rows.append({
            "review_id": str(candidate["review_id"]),
            "published_at_utc": str(candidate["published_at_utc"]),
            **article_metadata[source_id],
            "channels": candidate["channels"],
            "provider_tags": candidate["provider_tags"],
            **preview,
            "rendered_text_sha256": str(candidate["rendered_text_sha256"]),
        })
    controller_path = output_root / "CONTROLLER.jsonl"
    _write_jsonl_new(controller_path, controller_rows)
    packets = _packetize(worker_rows)
    packet_dir = output_root / "compact_first" / "packets"
    ledger_rows: list[dict[str, Any]] = []
    for index, packet in enumerate(packets):
        packet_id = f"CF{index:04d}"
        packet_path = packet_dir / f"{packet_id}.jsonl"
        _write_jsonl_new(packet_path, packet)
        ledger_rows.append({
            "packet_id": packet_id,
            "packet_path": str(packet_path),
            "articles": len(packet),
            "preview_characters": sum(len(str(row["preview_text"])) for row in packet),
            "packet_sha256": sha256_path(packet_path),
            "status": "pending",
        })
    ledger_path = output_root / "compact_first" / "PACKET_LEDGER.jsonl"
    _write_jsonl_new(ledger_path, ledger_rows)
    manifest = {
        "audit_version": AUDIT_VERSION,
        "status": "compact_packets_frozen",
        "candidates": len(candidates),
        "packets": len(packets),
        "preview_characters": sum(len(str(row["preview_text"])) for row in worker_rows),
        "packet_article_limit": PACKET_ARTICLE_LIMIT,
        "packet_character_limit": PACKET_CHARACTER_LIMIT,
        "inputs": {
            "candidates": str(candidates_path), "candidates_sha256": sha256_path(candidates_path),
            "article_features": str(article_features_path), "article_features_sha256": sha256_path(article_features_path),
            "rendered_texts": str(rendered_texts_path), "rendered_texts_sha256": sha256_path(rendered_texts_path),
        },
        "outputs": {
            "controller_sha256": sha256_path(controller_path),
            "packet_ledger_sha256": sha256_path(ledger_path),
        },
    }
    write_json_new(output_root / "PREPARE_MANIFEST.json", manifest)
    write_json_new(output_root / "COMPACT_REVIEW_INSTRUCTIONS.json", {
        "objective": "Classify forecast eligibility using only supplied metadata, title, teaser, and opening sentences.",
        "eligible": "Preview independently reports a new/current potentially material event or issuer guidance for an identifiable tradable issuer.",
        "ineligible": "Investment/analyst idea, technical/valuation setup, price narrative, screener/list, preview, recap, generic context, or routine notice without a new issuer event.",
        "needs_full_text": "Preview cannot safely establish whether a new material issuer event is independently reported.",
        "allowed_labels": sorted(ALLOWED_LABELS),
        "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "blindness": "Do not inspect controller files, current labels, model data, feature statistics, prior reviews, or full source text.",
        "required_fields": ["review_id", "manual_label", "confidence_probability", "reason_code", "rationale", "evidence_excerpt", "isolation_attestation"],
    })
    return manifest


def validate_review(*, packet_path: Path, review_path: Path) -> dict[str, Any]:
    packet = list(iter_jsonl(packet_path))
    reviews = list(iter_jsonl(review_path))
    by_id = {str(row["review_id"]): row for row in packet}
    if len(reviews) != len(packet) or len({str(row.get("review_id")) for row in reviews}) != len(packet):
        raise ValueError("review coverage/uniqueness mismatch")
    for row in reviews:
        review_id = str(row.get("review_id"))
        source = by_id.get(review_id)
        if source is None:
            raise ValueError(f"unexpected review ID: {review_id}")
        if row.get("manual_label") not in ALLOWED_LABELS or row.get("reason_code") not in ALLOWED_REASONS:
            raise ValueError(f"invalid decision: {review_id}")
        confidence = float(row.get("confidence_probability"))
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"invalid confidence: {review_id}")
        rationale = str(row.get("rationale") or "").strip()
        if not rationale or len(rationale.split()) > 30:
            raise ValueError(f"invalid rationale: {review_id}")
        excerpt = str(row.get("evidence_excerpt") or "")
        if not excerpt or len(excerpt) > 240 or excerpt not in str(source["preview_text"]):
            raise ValueError(f"evidence not in preview: {review_id}")
        if row.get("isolation_attestation") != {"used_only_supplied_packet": True, "used_external_context": False}:
            raise ValueError(f"invalid isolation attestation: {review_id}")
    return {"status": "valid", "articles": len(packet), "review_sha256": sha256_path(review_path)}


def _collect_first_reviews(output_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    articles: dict[str, dict[str, Any]] = {}
    reviews: dict[str, dict[str, Any]] = {}
    packet_dir = output_root / "compact_first" / "packets"
    review_dir = output_root / "compact_first" / "reviews"
    for review_path in sorted(review_dir.glob("R?_CF????.jsonl")):
        match = re.fullmatch(r"(R[123])_(CF\d{4})", review_path.stem)
        if match is None:
            raise ValueError(f"invalid first-review filename: {review_path.name}")
        reviewer_id, packet_id = match.groups()
        packet_path = packet_dir / f"{packet_id}.jsonl"
        validate_review(packet_path=packet_path, review_path=review_path)
        packet_rows = list(iter_jsonl(packet_path))
        review_rows = list(iter_jsonl(review_path))
        for article, review in zip(packet_rows, review_rows, strict=True):
            review_id = str(article["review_id"])
            if review_id in articles or review_id in reviews:
                raise ValueError(f"duplicate first review: {review_id}")
            articles[review_id] = article
            reviews[review_id] = {**review, "reviewer_id": reviewer_id, "packet_id": packet_id}
    if len(articles) != EXPECTED_CANDIDATES:
        raise ValueError(f"first-review coverage incomplete: {len(articles)}/{EXPECTED_CANDIDATES}")
    return articles, reviews


def prepare_second_compact(*, output_root: Path) -> dict[str, Any]:
    second_root = output_root / "compact_second"
    if second_root.exists():
        raise FileExistsError(second_root)
    articles, reviews = _collect_first_reviews(output_root)
    selected: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    selection_counts: Counter[str] = Counter()
    for review_id, review in reviews.items():
        label = str(review["manual_label"])
        reason = "proposed_ineligible" if label == "ineligible" else ""
        if label == "eligible" and int(_digest(f"{AUDIT_VERSION}|eligible-qc|{review_id}"), 16) % ELIGIBLE_QC_SAMPLE_MODULUS == 0:
            reason = "eligible_quality_control"
        if reason:
            selected.append((articles[review_id], review, reason))
            selection_counts[reason] += 1
    selected.sort(key=lambda value: _digest(f"{AUDIT_VERSION}|compact-second|{value[0]['review_id']}"))
    grouped: dict[str, list[tuple[dict[str, Any], str]]] = {}
    for article, review, reason in selected:
        grouped.setdefault(str(review["reviewer_id"]), []).append((article, reason))
    ledger_rows: list[dict[str, Any]] = []
    packet_number = 0
    for excluded_reviewer in sorted(grouped):
        rows = [article for article, _ in grouped[excluded_reviewer]]
        reason_by_id = {str(article["review_id"]): reason for article, reason in grouped[excluded_reviewer]}
        for packet in _packetize(rows):
            packet_id = f"CS{packet_number:04d}"
            packet_number += 1
            packet_path = second_root / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(packet_path, packet)
            ledger_rows.append({
                "packet_id": packet_id,
                "packet_path": str(packet_path),
                "articles": len(packet),
                "preview_characters": sum(len(str(row["preview_text"])) for row in packet),
                "excluded_reviewer_id": excluded_reviewer,
                "selection_counts": dict(Counter(reason_by_id[str(row["review_id"])] for row in packet)),
                "packet_sha256": sha256_path(packet_path),
                "status": "pending",
            })
    ledger_path = second_root / "PACKET_LEDGER.jsonl"
    _write_jsonl_new(ledger_path, ledger_rows)
    manifest = {
        "audit_version": AUDIT_VERSION,
        "status": "second_compact_packets_frozen",
        "articles": len(selected),
        "packets": len(ledger_rows),
        "selection_counts": dict(selection_counts),
        "policy": {
            "proposed_ineligible": "Independent compact confirmation is required before changing a current eligible label.",
            "eligible_quality_control": f"Deterministic 1/{ELIGIBLE_QC_SAMPLE_MODULUS} sample estimates compact eligible error.",
            "needs_full_text": "First-pass needs_full_text rows bypass second compact and enter full-text review.",
        },
        "first_review_files": len(list((output_root / "compact_first" / "reviews").glob("R?_CF????.jsonl"))),
        "packet_ledger_sha256": sha256_path(ledger_path),
    }
    write_json_new(second_root / "MANIFEST.json", manifest)
    return manifest


def _collect_second_reviews(output_root: Path) -> dict[str, dict[str, Any]]:
    ledger = {
        str(row["packet_id"]): row
        for row in iter_jsonl(output_root / "compact_second" / "PACKET_LEDGER.jsonl")
    }
    reviews: dict[str, dict[str, Any]] = {}
    packet_dir = output_root / "compact_second" / "packets"
    review_dir = output_root / "compact_second" / "reviews"
    for review_path in sorted(review_dir.glob("R?_CS????.jsonl")):
        match = re.fullmatch(r"(R[123])_(CS\d{4})", review_path.stem)
        if match is None:
            raise ValueError(f"invalid second-review filename: {review_path.name}")
        reviewer_id, packet_id = match.groups()
        if reviewer_id == str(ledger[packet_id]["excluded_reviewer_id"]):
            raise ValueError(f"non-independent second reviewer: {packet_id}")
        packet_path = packet_dir / f"{packet_id}.jsonl"
        validate_review(packet_path=packet_path, review_path=review_path)
        for row in iter_jsonl(review_path):
            review_id = str(row["review_id"])
            if review_id in reviews:
                raise ValueError(f"duplicate second review: {review_id}")
            reviews[review_id] = {**row, "reviewer_id": reviewer_id, "packet_id": packet_id}
    expected = sum(int(row["articles"]) for row in ledger.values())
    if len(reviews) != expected:
        raise ValueError(f"second-review coverage incomplete: {len(reviews)}/{expected}")
    return reviews


def prepare_full_text(*, output_root: Path, rendered_texts_path: Path) -> dict[str, Any]:
    full_root = output_root / "full_text"
    if full_root.exists():
        raise FileExistsError(full_root)
    compact_articles, first = _collect_first_reviews(output_root)
    second = _collect_second_reviews(output_root)
    selected: dict[str, set[str]] = {}
    for review_id, first_review in first.items():
        first_label = str(first_review["manual_label"])
        if first_label == "needs_full_text":
            selected.setdefault(review_id, set()).add("first_needs_full_text")
        if review_id not in second:
            continue
        second_label = str(second[review_id]["manual_label"])
        if second_label == "needs_full_text":
            selected.setdefault(review_id, set()).add("second_needs_full_text")
        elif first_label != second_label:
            selected.setdefault(review_id, set()).add("compact_disagreement")
        elif int(_digest(f"{AUDIT_VERSION}|compact-agreement-qc|{review_id}"), 16) % ELIGIBLE_QC_SAMPLE_MODULUS == 0:
            selected.setdefault(review_id, set()).add(f"{first_label}_agreement_quality_control")
    controller = {str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    source_to_review = {str(row["source_id"]): review_id for review_id, row in controller.items() if review_id in selected}
    rendered: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts_path):
        review_id = source_to_review.get(str(row["source_id"]))
        if review_id is None:
            continue
        text = str(row["rendered_text"])
        digest = _digest(text)
        if digest != str(row["rendered_text_hash"]) or digest != str(controller[review_id]["rendered_text_sha256"]):
            raise ValueError(f"rendered hash mismatch for full review: {review_id}")
        rendered[review_id] = text
    if rendered.keys() != selected.keys():
        raise ValueError("full-review rendered membership mismatch")
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    oversized_rows: list[dict[str, Any]] = []
    selection_counts: Counter[str] = Counter()
    for review_id, reasons in selected.items():
        selection_counts.update(reasons)
        article = compact_articles[review_id]
        row = {
            "review_id": review_id,
            "published_at_utc": article["published_at_utc"],
            "provider": article["provider"],
            "tickers": article["tickers"],
            "channels": article["channels"],
            "provider_tags": article["provider_tags"],
            "rendered_text": rendered[review_id],
            "rendered_text_sha256": article["rendered_text_sha256"],
        }
        excluded = {str(first[review_id]["reviewer_id"])}
        if review_id in second:
            excluded.add(str(second[review_id]["reviewer_id"]))
        if len(row["rendered_text"]) > OVERSIZED_FULL_TEXT_CHARACTERS:
            oversized_rows.append({**row, "excluded_reviewer_ids": sorted(excluded)})
        else:
            grouped.setdefault(tuple(sorted(excluded)), []).append(row)
    ledger_rows: list[dict[str, Any]] = []
    packet_number = 0
    for excluded in sorted(grouped):
        rows = sorted(grouped[excluded], key=lambda row: _digest(f"{AUDIT_VERSION}|full|{row['review_id']}"))
        for packet in _packetize_by(rows, text_key="rendered_text", article_limit=FULL_PACKET_ARTICLE_LIMIT, character_limit=FULL_PACKET_CHARACTER_LIMIT):
            packet_id = f"FT{packet_number:04d}"
            packet_number += 1
            packet_path = full_root / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(packet_path, packet)
            ledger_rows.append({
                "packet_id": packet_id,
                "packet_path": str(packet_path),
                "articles": len(packet),
                "rendered_characters": sum(len(str(row["rendered_text"])) for row in packet),
                "excluded_reviewer_ids": list(excluded),
                "packet_sha256": sha256_path(packet_path),
                "status": "pending",
            })
    ledger_path = full_root / "PACKET_LEDGER.jsonl"
    _write_jsonl_new(ledger_path, ledger_rows)
    oversized_path = full_root / "OVERSIZED_CONTROLLER.jsonl"
    _write_jsonl_new(oversized_path, oversized_rows)
    write_json_new(full_root / "FULL_REVIEW_INSTRUCTIONS.json", {
        "objective": "Classify forecast eligibility using only supplied metadata and complete rendered source text.",
        "eligible": "The article independently reports a new/current potentially material event or issuer guidance for an identifiable tradable issuer.",
        "ineligible": "Investment/analyst idea, technical/valuation setup, price narrative, screener/list, preview, recap, generic context, or routine notice without a new issuer event.",
        "insufficient_information": "The supplied complete rendered text is malformed or still does not contain enough source information to decide.",
        "allowed_labels": sorted(ALLOWED_FULL_LABELS),
        "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "blindness": "Do not inspect controller files, compact reviews, current labels, model data, feature statistics, or other reviews.",
        "isolation_attestation": {"used_only_supplied_packet": True, "used_external_context": False},
    })
    manifest = {
        "audit_version": AUDIT_VERSION,
        "status": "full_text_packets_frozen",
        "articles": len(selected),
        "ordinary_articles": len(selected) - len(oversized_rows),
        "oversized_articles": len(oversized_rows),
        "packets": len(ledger_rows),
        "selection_counts": dict(selection_counts),
        "rendered_characters": sum(len(value) for value in rendered.values()),
        "ordinary_rendered_characters": sum(int(row["rendered_characters"]) for row in ledger_rows),
        "oversized_rendered_characters": sum(len(str(row["rendered_text"])) for row in oversized_rows),
        "packet_ledger_sha256": sha256_path(ledger_path),
        "oversized_controller_sha256": sha256_path(oversized_path),
    }
    write_json_new(full_root / "MANIFEST.json", manifest)
    return manifest


def validate_full_review(*, packet_path: Path, review_path: Path) -> dict[str, Any]:
    packet = list(iter_jsonl(packet_path))
    reviews = list(iter_jsonl(review_path))
    by_id = {str(row["review_id"]): row for row in packet}
    if [str(row.get("review_id")) for row in reviews] != [str(row["review_id"]) for row in packet]:
        raise ValueError("full-review identity/order mismatch")
    required = {"review_id", "manual_label", "confidence_probability", "reason_code", "rationale", "evidence_excerpt", "isolation_attestation"}
    for row in reviews:
        review_id = str(row["review_id"])
        if set(row) != required:
            raise ValueError(f"full-review schema mismatch: {review_id}")
        if row["manual_label"] not in ALLOWED_FULL_LABELS or row["reason_code"] not in ALLOWED_REASONS:
            raise ValueError(f"invalid full decision: {review_id}")
        confidence = float(row["confidence_probability"])
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"invalid full confidence: {review_id}")
        rationale = str(row["rationale"]).strip()
        excerpt = str(row["evidence_excerpt"])
        if not rationale or len(rationale.split()) > 30 or not excerpt or len(excerpt) > 240:
            raise ValueError(f"invalid full rationale/evidence: {review_id}")
        if excerpt not in str(by_id[review_id]["rendered_text"]):
            raise ValueError(f"full evidence not in text: {review_id}")
        if row["isolation_attestation"] != {"used_only_supplied_packet": True, "used_external_context": False}:
            raise ValueError(f"invalid full isolation attestation: {review_id}")
    return {"status": "valid", "articles": len(packet), "review_sha256": sha256_path(review_path)}


def assign_full_packets(*, output_root: Path) -> dict[str, Any]:
    full_root = output_root / "full_text"
    assignment_path = full_root / "ASSIGNMENT_LEDGER.jsonl"
    if assignment_path.exists():
        raise FileExistsError(assignment_path)
    packets = list(iter_jsonl(full_root / "PACKET_LEDGER.jsonl"))
    totals = {"R1": 0, "R2": 0, "R3": 0}
    assignments: dict[str, str] = {}
    for row in sorted(packets, key=lambda value: (-int(value["rendered_characters"]), str(value["packet_id"]))):
        excluded = set(str(value) for value in row["excluded_reviewer_ids"])
        allowed = [reviewer for reviewer in totals if reviewer not in excluded]
        reviewer = min(allowed, key=lambda value: (totals[value], value))
        assignments[str(row["packet_id"])] = reviewer
        totals[reviewer] += int(row["rendered_characters"])
    rows = [
        {
            **row,
            "assigned_reviewer_id": assignments[str(row["packet_id"])],
            "status": "pending",
        }
        for row in sorted(packets, key=lambda value: str(value["packet_id"]))
    ]
    _write_jsonl_new(assignment_path, rows)
    result = {
        "status": "full_packets_assigned",
        "packets": len(rows),
        "assigned_packets": dict(Counter(assignments.values())),
        "assigned_characters": totals,
        "assignment_ledger_sha256": sha256_path(assignment_path),
    }
    write_json_new(full_root / "ASSIGNMENT_MANIFEST.json", result)
    return result


def ingest_full_staging(*, output_root: Path, staging_root: Path, next_count: int) -> dict[str, Any]:
    full_root = output_root / "full_text"
    destination = full_root / "reviews"
    destination.mkdir(parents=True, exist_ok=True)
    assignments = {
        str(row["packet_id"]): str(row["assigned_reviewer_id"])
        for row in iter_jsonl(full_root / "ASSIGNMENT_LEDGER.jsonl")
    }
    amendment_path = full_root / "ASSIGNMENT_AMENDMENT_1.jsonl"
    if amendment_path.exists():
        for row in iter_jsonl(amendment_path):
            assignments[str(row["packet_id"])] = str(row["new_reviewer_id"])
    ingested: list[str] = []
    rejected: dict[str, str] = {}
    for source in sorted(staging_root.glob("R?_FT????.jsonl")):
        match = re.fullmatch(r"(R[123])_(FT\d{4})", source.stem)
        if match is None:
            continue
        reviewer_id, packet_id = match.groups()
        if assignments.get(packet_id) != reviewer_id:
            raise ValueError(f"staged full review violates frozen assignment: {source.name}")
        target = destination / source.name
        if target.exists():
            if sha256_path(target) != sha256_path(source):
                raise ValueError(f"staged/runtime full review differs: {source.name}")
            continue
        try:
            validate_full_review(packet_path=full_root / "packets" / f"{packet_id}.jsonl", review_path=source)
        except (KeyError, TypeError, ValueError) as exc:
            rejected[source.name] = str(exc)
            continue
        shutil.copyfile(source, target)
        if sha256_path(target) != sha256_path(source):
            raise ValueError(f"full review copy changed: {source.name}")
        ingested.append(source.name)
    next_packets: dict[str, list[str]] = {}
    for reviewer_id in ("R1", "R2", "R3"):
        next_packets[reviewer_id] = [
            packet_id for packet_id, assigned in sorted(assignments.items())
            if assigned == reviewer_id and not (destination / f"{reviewer_id}_{packet_id}.jsonl").exists()
        ][:next_count]
    return {
        "status": "full_staging_ingested",
        "ingested_files": ingested,
        "rejected_files": rejected,
        "stored_files": len(list(destination.glob("R?_FT????.jsonl"))),
        "pending_files": len(assignments) - len(list(destination.glob("R?_FT????.jsonl"))),
        "next_packets": next_packets,
    }


def reassign_full_pending(*, output_root: Path) -> dict[str, Any]:
    full_root = output_root / "full_text"
    amendment_path = full_root / "ASSIGNMENT_AMENDMENT_1.jsonl"
    if amendment_path.exists():
        raise FileExistsError(amendment_path)
    rows = list(iter_jsonl(full_root / "ASSIGNMENT_LEDGER.jsonl"))
    review_dir = full_root / "reviews"
    eligible = [
        row for row in rows
        if str(row["assigned_reviewer_id"]) == "R1"
        and "R2" not in set(str(value) for value in row["excluded_reviewer_ids"])
        and not (review_dir / f"R1_{row['packet_id']}.jsonl").exists()
    ]
    ordered = sorted(eligible, key=lambda row: _digest(f"{AUDIT_VERSION}|assignment-amendment|{row['packet_id']}"))
    selected = ordered[::2]
    amendment_rows = [{
        "packet_id": str(row["packet_id"]),
        "original_reviewer_id": "R1",
        "new_reviewer_id": "R2",
        "excluded_reviewer_ids": row["excluded_reviewer_ids"],
        "rendered_characters": int(row["rendered_characters"]),
        "reason": "balance_remaining_independent_workload",
    } for row in selected]
    _write_jsonl_new(amendment_path, amendment_rows)
    return {
        "status": "full_assignment_amended",
        "eligible_pending_packets": len(eligible),
        "reassigned_packets": len(selected),
        "reassigned_characters": sum(int(row["rendered_characters"]) for row in selected),
        "amendment_sha256": sha256_path(amendment_path),
    }


def prepare_oversized_chunks(*, output_root: Path) -> dict[str, Any]:
    full_root = output_root / "full_text"
    chunk_root = full_root / "oversized_chunks"
    if chunk_root.exists():
        raise FileExistsError(chunk_root)
    rows = list(iter_jsonl(full_root / "OVERSIZED_CONTROLLER.jsonl"))
    if len(rows) != 1:
        raise ValueError(f"expected one oversized row, found {len(rows)}")
    controller = rows[0]
    text = str(controller["rendered_text"])
    chunks = [text[start:start + OVERSIZED_CHUNK_CHARACTERS] for start in range(0, len(text), OVERSIZED_CHUNK_CHARACTERS)]
    if "".join(chunks) != text:
        raise ValueError("oversized chunk reconstruction failed")
    chunk_root.mkdir(parents=True)
    worker = {key: value for key, value in controller.items() if key != "excluded_reviewer_ids"}
    worker_path = full_root / "OVERSIZED_WORKER.jsonl"
    if worker_path.exists():
        existing_worker = list(iter_jsonl(worker_path))
        if existing_worker != [worker]:
            raise ValueError("existing oversized worker differs from controller source")
    else:
        _write_jsonl_new(worker_path, [worker])
    ledger_rows: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        chunk_id = f"OS{index:04d}"
        path = chunk_root / f"{chunk_id}.json"
        write_json_new(path, {
            "review_id": controller["review_id"],
            "chunk_id": chunk_id,
            "chunk_index": index,
            "total_chunks": len(chunks),
            "published_at_utc": controller["published_at_utc"],
            "provider": controller["provider"],
            "tickers": controller["tickers"],
            "channels": controller["channels"],
            "provider_tags": controller["provider_tags"],
            "rendered_text_chunk": chunk,
            "chunk_sha256": _digest(chunk),
            "article_sha256": controller["rendered_text_sha256"],
        })
        ledger_rows.append({"chunk_id": chunk_id, "path": str(path), "characters": len(chunk), "sha256": sha256_path(path)})
    ledger_path = full_root / "OVERSIZED_CHUNK_LEDGER.jsonl"
    _write_jsonl_new(ledger_path, ledger_rows)
    result = {
        "status": "oversized_chunks_frozen",
        "review_id": controller["review_id"],
        "characters": len(text),
        "chunks": len(chunks),
        "excluded_reviewer_ids": controller["excluded_reviewer_ids"],
        "assigned_reviewer_id": "R1",
        "reconstruction_sha256": _digest("".join(chunks)),
        "worker_sha256": sha256_path(worker_path),
        "chunk_ledger_sha256": sha256_path(ledger_path),
    }
    write_json_new(full_root / "OVERSIZED_CHUNK_MANIFEST.json", result)
    return result


def prepare_full_expansion(*, output_root: Path, rendered_texts_path: Path) -> dict[str, Any]:
    """Escalate every article omitted from the initial full-text tranche.

    Compact-agreement QC exceeded the acceptable stopping error, so the compact
    decisions remain routing evidence only and cannot be final authority.
    """
    expansion_root = output_root / "full_expansion"
    if expansion_root.exists():
        raise FileExistsError(expansion_root)
    compact_articles, first = _collect_first_reviews(output_root)
    already_selected: set[str] = set()
    for packet_path in (output_root / "full_text" / "packets").glob("FT*.jsonl"):
        already_selected.update(str(row["review_id"]) for row in iter_jsonl(packet_path))
    already_selected.update(
        str(row["review_id"]) for row in iter_jsonl(output_root / "full_text" / "OVERSIZED_CONTROLLER.jsonl")
    )
    remaining = set(compact_articles) - already_selected
    if len(remaining) != EXPECTED_CANDIDATES - len(already_selected):
        raise ValueError("full-expansion membership mismatch")
    controller = {str(row["source_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    source_to_review = {
        source_id: str(row["review_id"])
        for source_id, row in controller.items()
        if str(row["review_id"]) in remaining
    }
    rendered: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts_path):
        review_id = source_to_review.get(str(row["source_id"]))
        if review_id is None:
            continue
        text = str(row["rendered_text"])
        digest = _digest(text)
        if digest != str(row["rendered_text_hash"]) or digest != str(controller[str(row["source_id"])]["rendered_text_sha256"]):
            raise ValueError(f"rendered hash mismatch for full expansion: {review_id}")
        rendered[review_id] = text
    if rendered.keys() != remaining:
        raise ValueError("full-expansion rendered membership mismatch")
    grouped: dict[str, list[dict[str, Any]]] = {}
    oversized_rows: list[dict[str, Any]] = []
    for review_id in remaining:
        article = compact_articles[review_id]
        row = {
            "review_id": review_id,
            "published_at_utc": article["published_at_utc"],
            "provider": article["provider"],
            "tickers": article["tickers"],
            "channels": article["channels"],
            "provider_tags": article["provider_tags"],
            "rendered_text": rendered[review_id],
            "rendered_text_sha256": article["rendered_text_sha256"],
        }
        excluded_reviewer = str(first[review_id]["reviewer_id"])
        if len(row["rendered_text"]) > OVERSIZED_FULL_TEXT_CHARACTERS:
            oversized_rows.append({**row, "excluded_reviewer_ids": [excluded_reviewer]})
        else:
            grouped.setdefault(excluded_reviewer, []).append(row)
    ledger_rows: list[dict[str, Any]] = []
    packet_number = 0
    for excluded_reviewer, rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda row: _digest(f"{AUDIT_VERSION}|full-expansion|{row['review_id']}"))
        for packet in _packetize_by(
            ordered,
            text_key="rendered_text",
            article_limit=FULL_PACKET_ARTICLE_LIMIT,
            character_limit=FULL_PACKET_CHARACTER_LIMIT,
        ):
            packet_id = f"FX{packet_number:04d}"
            packet_number += 1
            packet_path = expansion_root / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(packet_path, packet)
            ledger_rows.append({
                "packet_id": packet_id,
                "packet_path": str(packet_path),
                "articles": len(packet),
                "rendered_characters": sum(len(str(row["rendered_text"])) for row in packet),
                "excluded_reviewer_ids": [excluded_reviewer],
                "packet_sha256": sha256_path(packet_path),
                "status": "pending",
            })
    ledger_path = expansion_root / "PACKET_LEDGER.jsonl"
    _write_jsonl_new(ledger_path, ledger_rows)
    oversized_path = expansion_root / "OVERSIZED_CONTROLLER.jsonl"
    _write_jsonl_new(oversized_path, oversized_rows)
    manifest = {
        "audit_version": AUDIT_VERSION,
        "status": "full_expansion_frozen",
        "reason": "compact agreement QC error exceeded five percent for both labels",
        "articles": len(remaining),
        "ordinary_articles": len(remaining) - len(oversized_rows),
        "oversized_articles": len(oversized_rows),
        "packets": len(ledger_rows),
        "rendered_characters": sum(len(text) for text in rendered.values()),
        "ordinary_rendered_characters": sum(int(row["rendered_characters"]) for row in ledger_rows),
        "oversized_rendered_characters": sum(len(str(row["rendered_text"])) for row in oversized_rows),
        "packet_ledger_sha256": sha256_path(ledger_path),
        "oversized_controller_sha256": sha256_path(oversized_path),
    }
    write_json_new(expansion_root / "MANIFEST.json", manifest)
    return manifest


def prepare_expansion_oversized_chunks(*, output_root: Path) -> dict[str, Any]:
    expansion_root = output_root / "full_expansion"
    chunk_root = expansion_root / "oversized_chunks"
    if chunk_root.exists():
        raise FileExistsError(chunk_root)
    rows = list(iter_jsonl(expansion_root / "OVERSIZED_CONTROLLER.jsonl"))
    if len(rows) != 1:
        raise ValueError(f"expected one full-expansion oversized row, found {len(rows)}")
    controller = rows[0]
    text = str(controller["rendered_text"])
    chunks = [text[start:start + OVERSIZED_CHUNK_CHARACTERS] for start in range(0, len(text), OVERSIZED_CHUNK_CHARACTERS)]
    if "".join(chunks) != text:
        raise ValueError("full-expansion oversized chunk reconstruction failed")
    chunk_root.mkdir(parents=True)
    worker = {key: value for key, value in controller.items() if key != "excluded_reviewer_ids"}
    worker_path = expansion_root / "OVERSIZED_WORKER.jsonl"
    _write_jsonl_new(worker_path, [worker])
    excluded = set(str(value) for value in controller["excluded_reviewer_ids"])
    assigned_reviewer = "R2" if "R2" not in excluded else "R1"
    ledger_rows: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        chunk_id = f"EXOS{index:04d}"
        path = chunk_root / f"{chunk_id}.json"
        write_json_new(path, {
            "review_id": controller["review_id"],
            "chunk_id": chunk_id,
            "chunk_index": index,
            "total_chunks": len(chunks),
            "published_at_utc": controller["published_at_utc"],
            "provider": controller["provider"],
            "tickers": controller["tickers"],
            "channels": controller["channels"],
            "provider_tags": controller["provider_tags"],
            "rendered_text_chunk": chunk,
            "chunk_sha256": _digest(chunk),
            "article_sha256": controller["rendered_text_sha256"],
        })
        ledger_rows.append({
            "chunk_id": chunk_id,
            "path": str(path),
            "characters": len(chunk),
            "sha256": sha256_path(path),
        })
    ledger_path = expansion_root / "OVERSIZED_CHUNK_LEDGER.jsonl"
    _write_jsonl_new(ledger_path, ledger_rows)
    result = {
        "status": "full_expansion_oversized_chunks_frozen",
        "review_id": controller["review_id"],
        "characters": len(text),
        "chunks": len(chunks),
        "excluded_reviewer_ids": sorted(excluded),
        "assigned_reviewer_id": assigned_reviewer,
        "reconstruction_sha256": _digest("".join(chunks)),
        "worker_sha256": sha256_path(worker_path),
        "chunk_ledger_sha256": sha256_path(ledger_path),
    }
    write_json_new(expansion_root / "OVERSIZED_CHUNK_MANIFEST.json", result)
    return result


def validate_oversized_chunk_notes(*, chunk_root: Path, notes_path: Path) -> dict[str, Any]:
    notes = list(iter_jsonl(notes_path))
    if not notes:
        raise ValueError("oversized chunk notes are empty")
    expected_fields = {
        "review_id", "chunk_id", "contains_potential_new_issuer_event",
        "evidence_excerpt", "notes", "attestation",
    }
    seen: set[str] = set()
    for row in notes:
        if set(row) != expected_fields:
            raise ValueError(f"oversized chunk-note schema mismatch: {row.get('chunk_id')}")
        chunk_id = str(row["chunk_id"])
        if chunk_id in seen:
            raise ValueError(f"duplicate oversized chunk note: {chunk_id}")
        seen.add(chunk_id)
        chunk_path = chunk_root / f"{chunk_id}.json"
        chunk_rows = json.loads(chunk_path.read_text(encoding="utf-8"))
        if str(row["review_id"]) != str(chunk_rows["review_id"]):
            raise ValueError(f"oversized chunk-note review mismatch: {chunk_id}")
        if not isinstance(row["contains_potential_new_issuer_event"], bool):
            raise ValueError(f"oversized chunk-note event flag is not boolean: {chunk_id}")
        excerpt = str(row["evidence_excerpt"])
        if excerpt and excerpt not in str(chunk_rows["rendered_text_chunk"]):
            raise ValueError(f"oversized chunk-note evidence not in chunk: {chunk_id}")
        if len(excerpt) > 240 or not str(row["notes"]).strip() or len(str(row["notes"]).split()) > 50:
            raise ValueError(f"invalid oversized chunk-note text: {chunk_id}")
        if row["attestation"] != {"used_only_supplied_packet": True, "used_external_context": False}:
            raise ValueError(f"invalid oversized chunk-note attestation: {chunk_id}")
    return {"status": "valid", "chunks": len(notes), "notes_sha256": sha256_path(notes_path)}


def assign_full_expansion(*, output_root: Path) -> dict[str, Any]:
    expansion_root = output_root / "full_expansion"
    assignment_path = expansion_root / "ASSIGNMENT_LEDGER.jsonl"
    if assignment_path.exists():
        raise FileExistsError(assignment_path)
    packets = list(iter_jsonl(expansion_root / "PACKET_LEDGER.jsonl"))
    totals = {"R1": 0, "R2": 0, "R3": 0}
    assignments: list[dict[str, Any]] = []
    for row in sorted(packets, key=lambda value: (-int(value["rendered_characters"]), str(value["packet_id"]))):
        excluded = set(str(value) for value in row["excluded_reviewer_ids"])
        reviewer = min((value for value in totals if value not in excluded), key=lambda value: (totals[value], value))
        totals[reviewer] += int(row["rendered_characters"])
        assignments.append({**row, "assigned_reviewer_id": reviewer})
    assignments.sort(key=lambda row: str(row["packet_id"]))
    _write_jsonl_new(assignment_path, assignments)
    result = {
        "status": "full_expansion_assigned",
        "packets": len(assignments),
        "assigned_characters": totals,
        "assignment_sha256": sha256_path(assignment_path),
    }
    write_json_new(expansion_root / "ASSIGNMENT_MANIFEST.json", result)
    return result


def ingest_full_expansion_staging(
    *, output_root: Path, staging_root: Path, next_count: int, reviewer_id: str | None = None,
) -> dict[str, Any]:
    expansion_root = output_root / "full_expansion"
    destination = expansion_root / "reviews"
    destination.mkdir(parents=True, exist_ok=True)
    assignments = {
        str(row["packet_id"]): str(row["assigned_reviewer_id"])
        for row in iter_jsonl(expansion_root / "ASSIGNMENT_LEDGER.jsonl")
    }
    for amendment_path in sorted(expansion_root.glob("ASSIGNMENT_AMENDMENT_*.jsonl")):
        for row in iter_jsonl(amendment_path):
            assignments[str(row["packet_id"])] = str(row["new_reviewer_id"])
    ingested: list[str] = []
    rejected: dict[str, str] = {}
    pattern = f"{reviewer_id}_FX????.jsonl" if reviewer_id else "R?_FX????.jsonl"
    for source in sorted(staging_root.glob(pattern)):
        match = re.fullmatch(r"(R[123])_(FX\d{4})", source.stem)
        if match is None:
            continue
        reviewer_id, packet_id = match.groups()
        if assignments.get(packet_id) != reviewer_id:
            raise ValueError(f"staged full-expansion review violates frozen assignment: {source.name}")
        target = destination / source.name
        if target.exists():
            if sha256_path(target) != sha256_path(source):
                raise ValueError(f"staged/runtime full-expansion review differs: {source.name}")
            continue
        try:
            validate_full_review(packet_path=expansion_root / "packets" / f"{packet_id}.jsonl", review_path=source)
        except (KeyError, TypeError, ValueError) as exc:
            rejected[source.name] = str(exc)
            continue
        shutil.copyfile(source, target)
        if sha256_path(target) != sha256_path(source):
            raise ValueError(f"full-expansion review copy changed: {source.name}")
        ingested.append(source.name)
    stored = len(list(destination.glob("R?_FX????.jsonl")))
    next_packets = {
        reviewer_id: [
            packet_id for packet_id, assigned in sorted(assignments.items())
            if assigned == reviewer_id and not (destination / f"{reviewer_id}_{packet_id}.jsonl").exists()
        ][:next_count]
        for reviewer_id in ("R1", "R2", "R3")
    }
    return {
        "status": "full_expansion_staging_ingested",
        "ingested_files": ingested,
        "rejected_files": rejected,
        "stored_files": stored,
        "pending_files": len(assignments) - stored,
        "next_packets": next_packets,
    }


def reassign_full_expansion_pending(*, output_root: Path) -> dict[str, Any]:
    expansion_root = output_root / "full_expansion"
    assignments = list(iter_jsonl(expansion_root / "ASSIGNMENT_LEDGER.jsonl"))
    effective = {str(row["packet_id"]): str(row["assigned_reviewer_id"]) for row in assignments}
    existing_amendments = sorted(expansion_root.glob("ASSIGNMENT_AMENDMENT_*.jsonl"))
    for existing in existing_amendments:
        for row in iter_jsonl(existing):
            effective[str(row["packet_id"])] = str(row["new_reviewer_id"])
    amendment_path = expansion_root / f"ASSIGNMENT_AMENDMENT_{len(existing_amendments) + 1}.jsonl"
    review_dir = expansion_root / "reviews"
    pending = [
        row for row in assignments
        if not (review_dir / f"{effective[str(row['packet_id'])]}_{row['packet_id']}.jsonl").exists()
    ]
    remaining_characters = {"R1": 0, "R2": 0, "R3": 0}
    new_assignments: dict[str, str] = {}
    for row in sorted(pending, key=lambda value: (-int(value["rendered_characters"]), str(value["packet_id"]))):
        excluded = set(str(value) for value in row["excluded_reviewer_ids"])
        reviewer = min(
            (value for value in remaining_characters if value not in excluded),
            key=lambda value: (remaining_characters[value], value),
        )
        new_assignments[str(row["packet_id"])] = reviewer
        remaining_characters[reviewer] += int(row["rendered_characters"])
    amendment_rows = []
    for row in sorted(pending, key=lambda value: str(value["packet_id"])):
        packet_id = str(row["packet_id"])
        original = effective[packet_id]
        updated = new_assignments[packet_id]
        if updated == original:
            continue
        amendment_rows.append({
            "packet_id": packet_id,
            "original_reviewer_id": original,
            "new_reviewer_id": updated,
            "excluded_reviewer_ids": row["excluded_reviewer_ids"],
            "rendered_characters": int(row["rendered_characters"]),
            "reason": "rebalance_untouched_packets_after_reviewer_completion",
        })
    _write_jsonl_new(amendment_path, amendment_rows)
    return {
        "status": "full_expansion_assignment_amended",
        "pending_packets": len(pending),
        "reassigned_packets": len(amendment_rows),
        "remaining_characters": remaining_characters,
        "amendment_sha256": sha256_path(amendment_path),
    }


def _collect_full_first_pass_reviews(output_root: Path) -> dict[str, dict[str, Any]]:
    reviews: dict[str, dict[str, Any]] = {}
    sources = (
        (output_root / "full_text" / "reviews", re.compile(r"(R[123])_(FT\d{4}|OVERSIZED)")),
        (output_root / "full_expansion" / "reviews", re.compile(r"(R[123])_(FX\d{4}|EXP_OVERSIZED)")),
    )
    for review_root, filename_pattern in sources:
        for path in sorted(review_root.glob("*.jsonl")):
            match = filename_pattern.fullmatch(path.stem)
            if match is None:
                continue
            reviewer_id, packet_id = match.groups()
            for row in iter_jsonl(path):
                review_id = str(row["review_id"])
                if review_id in reviews:
                    raise ValueError(f"duplicate full first-pass review: {review_id}")
                reviews[review_id] = {**row, "reviewer_id": reviewer_id, "packet_id": packet_id}
    if len(reviews) != EXPECTED_CANDIDATES:
        raise ValueError(f"full first-pass coverage incomplete: {len(reviews)}/{EXPECTED_CANDIDATES}")
    return reviews


def prepare_full_confirmation(*, output_root: Path, rendered_texts_path: Path) -> dict[str, Any]:
    confirmation_root = output_root / "full_confirmation"
    if confirmation_root.exists():
        raise FileExistsError(confirmation_root)
    compact_articles, first = _collect_first_reviews(output_root)
    second = _collect_second_reviews(output_root)
    full = _collect_full_first_pass_reviews(output_root)
    selected: dict[str, str] = {}
    for review_id, decision in full.items():
        if str(decision["manual_label"]) == "insufficient_information":
            selected[review_id] = "full_insufficient"
        elif str(decision["manual_label"]) == "ineligible" and str(first[review_id]["manual_label"]) != "ineligible":
            selected[review_id] = "proposed_correction_without_compact_agreement"
    controller = {str(row["source_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")}
    source_to_review = {
        source_id: str(row["review_id"])
        for source_id, row in controller.items()
        if str(row["review_id"]) in selected
    }
    rendered: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts_path):
        review_id = source_to_review.get(str(row["source_id"]))
        if review_id is None:
            continue
        text = str(row["rendered_text"])
        digest = _digest(text)
        if digest != str(row["rendered_text_hash"]) or digest != str(controller[str(row["source_id"])]["rendered_text_sha256"]):
            raise ValueError(f"rendered hash mismatch for full confirmation: {review_id}")
        rendered[review_id] = text
    if rendered.keys() != selected.keys():
        raise ValueError("full-confirmation rendered membership mismatch")
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for review_id, reason in selected.items():
        article = compact_articles[review_id]
        excluded = {str(first[review_id]["reviewer_id"]), str(full[review_id]["reviewer_id"])}
        if review_id in second:
            excluded.add(str(second[review_id]["reviewer_id"]))
        grouped.setdefault(tuple(sorted(excluded)), []).append({
            "review_id": review_id,
            "published_at_utc": article["published_at_utc"],
            "provider": article["provider"],
            "tickers": article["tickers"],
            "channels": article["channels"],
            "provider_tags": article["provider_tags"],
            "rendered_text": rendered[review_id],
            "rendered_text_sha256": article["rendered_text_sha256"],
        })
    ledger_rows: list[dict[str, Any]] = []
    packet_number = 0
    for excluded, rows in sorted(grouped.items()):
        ordered = sorted(rows, key=lambda row: _digest(f"{AUDIT_VERSION}|full-confirmation|{row['review_id']}"))
        for packet in _packetize_by(
            ordered,
            text_key="rendered_text",
            article_limit=FULL_PACKET_ARTICLE_LIMIT,
            character_limit=FULL_PACKET_CHARACTER_LIMIT,
        ):
            packet_id = f"CC{packet_number:04d}"
            packet_number += 1
            packet_path = confirmation_root / "packets" / f"{packet_id}.jsonl"
            _write_jsonl_new(packet_path, packet)
            ledger_rows.append({
                "packet_id": packet_id,
                "packet_path": str(packet_path),
                "articles": len(packet),
                "rendered_characters": sum(len(str(row["rendered_text"])) for row in packet),
                "excluded_reviewer_ids": list(excluded),
                "packet_sha256": sha256_path(packet_path),
                "status": "pending",
            })
    ledger_path = confirmation_root / "PACKET_LEDGER.jsonl"
    _write_jsonl_new(ledger_path, ledger_rows)
    totals = {"R1": 0, "R2": 0, "R3": 0, "R4": 0}
    assignment_rows = []
    for row in sorted(ledger_rows, key=lambda value: (-int(value["rendered_characters"]), str(value["packet_id"]))):
        excluded = set(str(value) for value in row["excluded_reviewer_ids"])
        reviewer = min((value for value in totals if value not in excluded), key=lambda value: (totals[value], value))
        totals[reviewer] += int(row["rendered_characters"])
        assignment_rows.append({**row, "assigned_reviewer_id": reviewer})
    assignment_rows.sort(key=lambda row: str(row["packet_id"]))
    assignment_path = confirmation_root / "ASSIGNMENT_LEDGER.jsonl"
    _write_jsonl_new(assignment_path, assignment_rows)
    result = {
        "status": "full_confirmation_frozen",
        "articles": len(selected),
        "selection_counts": dict(Counter(selected.values())),
        "packets": len(ledger_rows),
        "rendered_characters": sum(len(text) for text in rendered.values()),
        "assigned_characters": totals,
        "packet_ledger_sha256": sha256_path(ledger_path),
        "assignment_sha256": sha256_path(assignment_path),
    }
    write_json_new(confirmation_root / "MANIFEST.json", result)
    return result


def ingest_full_confirmation_staging(
    *, output_root: Path, staging_root: Path, next_count: int, reviewer_id: str | None = None,
) -> dict[str, Any]:
    confirmation_root = output_root / "full_confirmation"
    destination = confirmation_root / "reviews"
    destination.mkdir(parents=True, exist_ok=True)
    assignments = {
        str(row["packet_id"]): str(row["assigned_reviewer_id"])
        for row in iter_jsonl(confirmation_root / "ASSIGNMENT_LEDGER.jsonl")
    }
    for amendment_path in sorted(confirmation_root.glob("ASSIGNMENT_AMENDMENT_*.jsonl")):
        for row in iter_jsonl(amendment_path):
            assignments[str(row["packet_id"])] = str(row["new_reviewer_id"])
    pattern = f"{reviewer_id}_CC????.jsonl" if reviewer_id else "R?_CC????.jsonl"
    ingested: list[str] = []
    rejected: dict[str, str] = {}
    for source in sorted(staging_root.glob(pattern)):
        match = re.fullmatch(r"(R[1234])_(CC\d{4})", source.stem)
        if match is None:
            continue
        assigned_reviewer, packet_id = match.groups()
        if assignments.get(packet_id) != assigned_reviewer:
            raise ValueError(f"staged confirmation violates frozen assignment: {source.name}")
        target = destination / source.name
        if target.exists():
            if sha256_path(target) != sha256_path(source):
                raise ValueError(f"staged/runtime confirmation differs: {source.name}")
            continue
        try:
            validate_full_review(packet_path=confirmation_root / "packets" / f"{packet_id}.jsonl", review_path=source)
        except (KeyError, TypeError, ValueError) as exc:
            rejected[source.name] = str(exc)
            continue
        shutil.copyfile(source, target)
        if sha256_path(target) != sha256_path(source):
            raise ValueError(f"confirmation copy changed: {source.name}")
        ingested.append(source.name)
    stored = len(list(destination.glob("R?_CC????.jsonl")))
    next_packets = {
        value: [
            packet_id for packet_id, assigned in sorted(assignments.items())
            if assigned == value and not (destination / f"{value}_{packet_id}.jsonl").exists()
        ][:next_count]
        for value in ("R1", "R2", "R3", "R4")
    }
    return {
        "status": "full_confirmation_staging_ingested",
        "ingested_files": ingested,
        "rejected_files": rejected,
        "stored_files": stored,
        "pending_files": len(assignments) - stored,
        "next_packets": next_packets,
    }


def reassign_full_confirmation_pending(*, output_root: Path) -> dict[str, Any]:
    confirmation_root = output_root / "full_confirmation"
    amendment_path = confirmation_root / "ASSIGNMENT_AMENDMENT_1.jsonl"
    if amendment_path.exists():
        raise FileExistsError(amendment_path)
    rows = list(iter_jsonl(confirmation_root / "ASSIGNMENT_LEDGER.jsonl"))
    review_dir = confirmation_root / "reviews"
    pending = [
        row for row in rows
        if not (review_dir / f"{row['assigned_reviewer_id']}_{row['packet_id']}.jsonl").exists()
    ]
    amendments = []
    for row in sorted(pending, key=lambda value: str(value["packet_id"])):
        if "R4" in set(str(value) for value in row["excluded_reviewer_ids"]):
            raise ValueError(f"R4 is excluded from confirmation packet: {row['packet_id']}")
        amendments.append({
            "packet_id": str(row["packet_id"]),
            "original_reviewer_id": str(row["assigned_reviewer_id"]),
            "new_reviewer_id": "R4",
            "excluded_reviewer_ids": row["excluded_reviewer_ids"],
            "rendered_characters": int(row["rendered_characters"]),
            "reason": "fresh_reviewer_available_after_completed_confirmation_lane",
        })
    _write_jsonl_new(amendment_path, amendments)
    return {
        "status": "full_confirmation_assignment_amended",
        "pending_packets": len(pending),
        "reassigned_packets": len(amendments),
        "reassigned_characters": sum(int(row["rendered_characters"]) for row in pending),
        "amendment_sha256": sha256_path(amendment_path),
    }


def finalize_successor_authority(
    *, audit_root: Path, parent_authority: Path, successor_authority: Path,
) -> dict[str, Any]:
    if successor_authority.exists():
        raise FileExistsError(successor_authority)
    parent_hashes = json.loads((parent_authority / "HASH_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    parent_labels = parent_authority / "article_forecast_eligibility_labels.jsonl"
    parent_sentiment = parent_authority / "gold_issuer_sentiment_labels.jsonl"
    if sha256_path(parent_labels) != str(parent_hashes[parent_labels.name]["sha256"]):
        raise ValueError("parent article-label hash mismatch")
    if sha256_path(parent_sentiment) != str(parent_hashes[parent_sentiment.name]["sha256"]):
        raise ValueError("parent sentiment hash mismatch")
    controller = {str(row["review_id"]): row for row in iter_jsonl(audit_root / "CONTROLLER.jsonl")}
    compact_articles, first = _collect_first_reviews(audit_root)
    second = _collect_second_reviews(audit_root)
    full = _collect_full_first_pass_reviews(audit_root)
    confirmation: dict[str, dict[str, Any]] = {}
    for path in sorted((audit_root / "full_confirmation" / "reviews").glob("R?_CC????.jsonl")):
        match = re.fullmatch(r"(R[1234])_(CC\d{4})", path.stem)
        if match is None:
            continue
        reviewer_id, packet_id = match.groups()
        for row in iter_jsonl(path):
            review_id = str(row["review_id"])
            if review_id in confirmation:
                raise ValueError(f"duplicate confirmation review: {review_id}")
            confirmation[review_id] = {**row, "reviewer_id": reviewer_id, "packet_id": packet_id}
    if len(confirmation) != 454:
        raise ValueError(f"confirmation coverage incomplete: {len(confirmation)}/454")
    final_by_source: dict[str, str] = {}
    ledger_rows: list[dict[str, Any]] = []
    final_counts: Counter[str] = Counter()
    decision_paths: Counter[str] = Counter()
    for review_id, first_pass in full.items():
        first_label = str(first[review_id]["manual_label"])
        full_label = str(first_pass["manual_label"])
        confirmation_row = confirmation.get(review_id)
        confirmation_label = str(confirmation_row["manual_label"]) if confirmation_row else None
        if full_label == "eligible":
            final_label = "eligible"
            decision_path = "full_text_eligible_preserve"
        elif full_label == "ineligible" and first_label == "ineligible":
            final_label = "ineligible"
            decision_path = "compact_full_ineligible_agreement"
        elif full_label == "ineligible" and confirmation_label == "ineligible":
            final_label = "ineligible"
            decision_path = "two_full_ineligible_agreement"
        elif full_label == "insufficient_information" and confirmation_label == "insufficient_information":
            final_label = "insufficient_short_text"
            decision_path = "two_full_insufficient_agreement"
        else:
            final_label = "eligible"
            decision_path = "fail_closed_preserve_eligible"
        source_id = str(controller[review_id]["source_id"])
        if str(controller[review_id]["current_label"]) != "eligible":
            raise ValueError(f"candidate parent label is not eligible: {source_id}")
        final_by_source[source_id] = final_label
        final_counts[final_label] += 1
        decision_paths[decision_path] += 1
        votes = [
            {**first[review_id], "stage": "compact_first"},
            {**first_pass, "stage": "full_first"},
        ]
        if review_id in second:
            votes.insert(1, {**second[review_id], "stage": "compact_second"})
        if confirmation_row:
            votes.append({**confirmation_row, "stage": "full_confirmation"})
        ledger_rows.append({
            "source_id": source_id,
            "review_id": review_id,
            "rendered_text_hash": str(controller[review_id]["rendered_text_sha256"]),
            "original_label": "eligible",
            "final_label": final_label,
            "changed": final_label != "eligible",
            "decision_path": decision_path,
            "votes": votes,
        })
    if len(final_by_source) != EXPECTED_CANDIDATES:
        raise ValueError("final candidate membership mismatch")
    successor_authority.mkdir(parents=True)
    ledger_path = successor_authority / "trading_ideas_correction_ledger.jsonl"
    _write_jsonl_new(ledger_path, sorted(ledger_rows, key=lambda row: str(row["source_id"])))
    labels_path = successor_authority / parent_labels.name
    parent_rows = 0
    updated_rows = 0
    output_label_counts: Counter[str] = Counter()
    seen_candidates: set[str] = set()
    with labels_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in iter_jsonl(parent_labels):
            parent_rows += 1
            source_id = str(row["source_id"])
            final_label = final_by_source.get(source_id)
            if final_label is not None:
                seen_candidates.add(source_id)
                if str(row["forecast_eligibility_label"]) != "eligible":
                    raise ValueError(f"parent candidate label drifted from eligible: {source_id}")
                if final_label != "eligible":
                    row = dict(row)
                    row.update({
                        "authority_class": "codex_multi_reader_full_text",
                        "authority_detail": AUDIT_VERSION,
                        "certification_level": "codex_adjudicated",
                        "decisive": final_label != "insufficient_short_text",
                        "forecast_eligibility_label": final_label,
                        "forecast_eligible": True if final_label == "eligible" else (False if final_label == "ineligible" else None),
                        "usage_policy": "model_development_adjudicated" if final_label == "ineligible" else "model_development_excluded",
                    })
                    updated_rows += 1
            output_label_counts[str(row["forecast_eligibility_label"])] += 1
            handle.write(canonical_json(row) + "\n")
    if parent_rows != 361_695 or seen_candidates != set(final_by_source):
        raise ValueError("successor article coverage mismatch")
    sentiment_path = successor_authority / parent_sentiment.name
    shutil.copyfile(parent_sentiment, sentiment_path)
    parent_ledger = parent_authority / "provider_filter_correction_ledger.jsonl"
    copied_parent_ledger = successor_authority / parent_ledger.name
    shutil.copyfile(parent_ledger, copied_parent_ledger)
    validation = {
        "status": "passed",
        "article_rows": parent_rows,
        "unique_candidate_source_ids": len(final_by_source),
        "reviewed_candidate_rows": EXPECTED_CANDIDATES,
        "full_first_pass_rows": len(full),
        "full_confirmation_rows": len(confirmation),
        "updated_primary_rows": updated_rows,
        "sentiment_sha256_equal": sha256_path(sentiment_path) == sha256_path(parent_sentiment),
        "parent_authority_unchanged": sha256_path(parent_labels) == str(parent_hashes[parent_labels.name]["sha256"]),
        "candidate_coverage_complete": seen_candidates == set(final_by_source),
    }
    report = {
        "status": "scoped_correction_grade_successor",
        "audit_version": AUDIT_VERSION,
        "authority_version": successor_authority.name,
        "parent_authority": str(parent_authority),
        "scope": "6,896 previously eligible articles carrying the trading ideas channel or tag",
        "reviewed_articles": EXPECTED_CANDIDATES,
        "candidate_final_label_counts": dict(final_counts),
        "decision_path_counts": dict(decision_paths),
        "corrections": {
            "ineligible": final_counts["ineligible"],
            "insufficient_short_text": final_counts["insufficient_short_text"],
        },
        "authority_label_counts": dict(output_label_counts),
        "confirmation_articles": len(confirmation),
        "sentiment_byte_identical": validation["sentiment_sha256_equal"],
        "limitations": [
            "This is a scoped trading-ideas correction successor, not completion of the separate model-mismatch audit.",
            "Reviewer decisions are local Codex multi-reader adjudications, not human certification.",
            "Full-text confirmation disagreements preserve the parent eligible label fail-closed.",
        ],
    }
    load_manifest = {
        "dataset_version": successor_authority.name,
        "status": "scoped_correction_grade_successor",
        "parent_authority": str(parent_authority),
        "audit_root": str(audit_root),
        "primary_tables": {
            "article_forecast_eligibility": {"path": str(labels_path), "rows": parent_rows, "primary_key": ["source_id"]},
            "gold_issuer_sentiment": {"path": str(sentiment_path), "rows": 16_983, "primary_key": ["unit_id"]},
        },
        "correction_ledger": str(ledger_path),
        "parent_correction_ledger": str(copied_parent_ledger),
    }
    write_json_new(successor_authority / "REPORT.json", report)
    write_json_new(successor_authority / "VALIDATION.json", validation)
    write_json_new(successor_authority / "LOAD_MANIFEST.json", load_manifest)
    hash_files = [labels_path, sentiment_path, ledger_path, copied_parent_ledger,
                  successor_authority / "REPORT.json", successor_authority / "VALIDATION.json",
                  successor_authority / "LOAD_MANIFEST.json"]
    write_json_new(successor_authority / "HASH_MANIFEST.json", {
        "files": {path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in hash_files}
    })
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    prep.add_argument("--article-features", type=Path, default=DEFAULT_ARTICLE_FEATURES)
    prep.add_argument("--rendered-texts", type=Path, default=DEFAULT_RENDERED_TEXTS)
    prep.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    validate = sub.add_parser("validate-review")
    validate.add_argument("--packet", type=Path, required=True)
    validate.add_argument("--review", type=Path, required=True)
    second = sub.add_parser("prepare-second-compact")
    second.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    full = sub.add_parser("prepare-full-text")
    full.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    full.add_argument("--rendered-texts", type=Path, default=DEFAULT_RENDERED_TEXTS)
    validate_full = sub.add_parser("validate-full-review")
    validate_full.add_argument("--packet", type=Path, required=True)
    validate_full.add_argument("--review", type=Path, required=True)
    assign_full = sub.add_parser("assign-full-packets")
    assign_full.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    ingest_full = sub.add_parser("ingest-full-staging")
    ingest_full.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    ingest_full.add_argument("--staging-root", type=Path, default=Path.home() / "AppData" / "Local" / "Temp" / "trading_ideas_blind_audit")
    ingest_full.add_argument("--next-count", type=int, default=10)
    reassign_full = sub.add_parser("reassign-full-pending")
    reassign_full.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    oversized = sub.add_parser("prepare-oversized-chunks")
    oversized.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    expansion = sub.add_parser("prepare-full-expansion")
    expansion.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    expansion.add_argument("--rendered-texts", type=Path, default=DEFAULT_RENDERED_TEXTS)
    assign_expansion = sub.add_parser("assign-full-expansion")
    assign_expansion.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    ingest_expansion = sub.add_parser("ingest-full-expansion-staging")
    ingest_expansion.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    ingest_expansion.add_argument("--staging-root", type=Path, default=Path.home() / "AppData" / "Local" / "Temp" / "trading_ideas_blind_audit")
    ingest_expansion.add_argument("--next-count", type=int, default=10)
    ingest_expansion.add_argument("--reviewer-id", choices=("R1", "R2", "R3"))
    expansion_oversized = sub.add_parser("prepare-expansion-oversized-chunks")
    expansion_oversized.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    validate_chunk_notes = sub.add_parser("validate-oversized-chunk-notes")
    validate_chunk_notes.add_argument("--chunk-root", type=Path, required=True)
    validate_chunk_notes.add_argument("--notes", type=Path, required=True)
    reassign_expansion = sub.add_parser("reassign-full-expansion-pending")
    reassign_expansion.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    confirmation = sub.add_parser("prepare-full-confirmation")
    confirmation.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    confirmation.add_argument("--rendered-texts", type=Path, default=DEFAULT_RENDERED_TEXTS)
    ingest_confirmation = sub.add_parser("ingest-full-confirmation-staging")
    ingest_confirmation.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    ingest_confirmation.add_argument("--staging-root", type=Path, default=Path.home() / "AppData" / "Local" / "Temp" / "trading_ideas_blind_audit")
    ingest_confirmation.add_argument("--next-count", type=int, default=10)
    ingest_confirmation.add_argument("--reviewer-id", choices=("R1", "R2", "R3", "R4"))
    reassign_confirmation = sub.add_parser("reassign-full-confirmation-pending")
    reassign_confirmation.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    finalize = sub.add_parser("finalize-successor-authority")
    finalize.add_argument("--audit-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    finalize.add_argument("--parent-authority", type=Path, default=DEFAULT_PARENT_AUTHORITY)
    finalize.add_argument("--successor-authority", type=Path, default=DEFAULT_SUCCESSOR_AUTHORITY)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        result = prepare(candidates_path=args.candidates, article_features_path=args.article_features, rendered_texts_path=args.rendered_texts, output_root=args.output_root)
    elif args.command == "prepare-second-compact":
        result = prepare_second_compact(output_root=args.output_root)
    elif args.command == "prepare-full-text":
        result = prepare_full_text(output_root=args.output_root, rendered_texts_path=args.rendered_texts)
    elif args.command == "validate-full-review":
        result = validate_full_review(packet_path=args.packet, review_path=args.review)
    elif args.command == "assign-full-packets":
        result = assign_full_packets(output_root=args.output_root)
    elif args.command == "ingest-full-staging":
        result = ingest_full_staging(output_root=args.output_root, staging_root=args.staging_root, next_count=args.next_count)
    elif args.command == "reassign-full-pending":
        result = reassign_full_pending(output_root=args.output_root)
    elif args.command == "prepare-oversized-chunks":
        result = prepare_oversized_chunks(output_root=args.output_root)
    elif args.command == "prepare-full-expansion":
        result = prepare_full_expansion(output_root=args.output_root, rendered_texts_path=args.rendered_texts)
    elif args.command == "assign-full-expansion":
        result = assign_full_expansion(output_root=args.output_root)
    elif args.command == "ingest-full-expansion-staging":
        result = ingest_full_expansion_staging(
            output_root=args.output_root,
            staging_root=args.staging_root,
            next_count=args.next_count,
            reviewer_id=args.reviewer_id,
        )
    elif args.command == "prepare-expansion-oversized-chunks":
        result = prepare_expansion_oversized_chunks(output_root=args.output_root)
    elif args.command == "validate-oversized-chunk-notes":
        result = validate_oversized_chunk_notes(chunk_root=args.chunk_root, notes_path=args.notes)
    elif args.command == "reassign-full-expansion-pending":
        result = reassign_full_expansion_pending(output_root=args.output_root)
    elif args.command == "prepare-full-confirmation":
        result = prepare_full_confirmation(output_root=args.output_root, rendered_texts_path=args.rendered_texts)
    elif args.command == "ingest-full-confirmation-staging":
        result = ingest_full_confirmation_staging(
            output_root=args.output_root,
            staging_root=args.staging_root,
            next_count=args.next_count,
            reviewer_id=args.reviewer_id,
        )
    elif args.command == "reassign-full-confirmation-pending":
        result = reassign_full_confirmation_pending(output_root=args.output_root)
    elif args.command == "finalize-successor-authority":
        result = finalize_successor_authority(
            audit_root=args.audit_root,
            parent_authority=args.parent_authority,
            successor_authority=args.successor_authority,
        )
    else:
        result = validate_review(packet_path=args.packet, review_path=args.review)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
