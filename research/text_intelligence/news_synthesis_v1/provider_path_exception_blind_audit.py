from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .provider_filter_analysis import canonical_json, iter_jsonl, sha256_path, write_json_new
from .trading_ideas_blind_audit import ALLOWED_REASONS, compact_preview


AUDIT_VERSION = "provider_path_exception_blind_audit_v1"
DEFAULT_EXCEPTION_QUEUE = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_merged_path_analysis_v3_trading_ideas_corrected"
    r"\UPDATED_PATH_EXCEPTION_CANDIDATES.jsonl"
)
DEFAULT_ARTICLE_FEATURES = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_feature_audit_v4_trading_ideas_corrected\ARTICLE_FEATURES.jsonl"
)
DEFAULT_RENDERED_TEXTS = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_rf_comparison_v1\rendered_texts.jsonl"
)
DEFAULT_AUTHORITY = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_trading_ideas_v1"
)
DEFAULT_COMPREHENSIVE_SAMPLE = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_metadata_comprehensive_audit_v1\enrichment_and_union\SHORT_SAMPLE_VALIDATION.json"
)
DEFAULT_CONTRADICTION_CONTROLLER = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_contradiction_review_v1\CONTROLLER_POPULATION.jsonl"
)
DEFAULT_TRADING_LEDGER = DEFAULT_AUTHORITY / "trading_ideas_correction_ledger.jsonl"
DEFAULT_TRADING_PRIOR = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\trading_ideas_review_candidates_v2\PREVIOUSLY_REVIEWED_ELIGIBLE.jsonl"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_path_exception_blind_audit_v1"
)
DEFAULT_SUCCESSOR_AUTHORITY = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_provider_path_exceptions_v1"
)
DEFAULT_REFRESHED_EXCEPTION_QUEUE = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_merged_path_analysis_v4_provider_path_exceptions_corrected"
    r"\UPDATED_PATH_EXCEPTION_CANDIDATES.jsonl"
)
DEFAULT_REFRESHED_ARTICLE_FEATURES = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_filter_feature_audit_v5_provider_path_exceptions_corrected\ARTICLE_FEATURES.jsonl"
)
DEFAULT_REFINEMENT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\news_synthesis_v1"
    r"\provider_path_exception_blind_audit_v1\refinement_round_2"
)
DEFAULT_FINAL_SUCCESSOR_AUTHORITY = Path(
    r"D:\TradingML\runtimes\text_intelligence\llm_issuer_labeling_v4"
    r"\forecast_eligibility_sentiment_authority_provider_path_exceptions_v2"
)
EXPECTED_EXCEPTION_ROWS = 2_257
EXPECTED_EVENT_QUEUE = 120
EXPECTED_NOISE_QUEUE = 840
PACKET_ARTICLE_LIMIT = 80
PACKET_CHARACTER_LIMIT = 80_000
REVIEWERS = ("R1", "R2", "R4")
ALLOWED_COMPACT_LABELS = {"eligible", "ineligible", "needs_full_text"}
ALLOWED_FULL_LABELS = {"eligible", "ineligible", "insufficient_information"}


def _write_jsonl_new(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _packetize(rows: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    packets: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    characters = 0
    for row in rows:
        size = len(str(row["preview_text"]))
        if current and (
            len(current) >= PACKET_ARTICLE_LIMIT or characters + size > PACKET_CHARACTER_LIMIT
        ):
            packets.append(current)
            current = []
            characters = 0
        current.append(row)
        characters += size
    if current:
        packets.append(current)
    return packets


def _load_reviewed_sets(
    *,
    comprehensive_sample: Path,
    contradiction_controller: Path,
    trading_ledger: Path,
    trading_prior: Path,
    authority: Path,
) -> dict[str, set[str]]:
    comprehensive = json.loads(comprehensive_sample.read_text(encoding="utf-8"))
    labels = authority / "article_forecast_eligibility_labels.jsonl"
    result = {
        "comprehensive_metadata_sample": set(map(str, comprehensive["sampled_source_ids"])),
        "provider_filter_contradiction": {
            str(row["source_id"]) for row in iter_jsonl(contradiction_controller)
        },
        "trading_ideas_current": {str(row["source_id"]) for row in iter_jsonl(trading_ledger)},
        "trading_ideas_prior": {str(row["source_id"]) for row in iter_jsonl(trading_prior)},
        "human_certified_current": {
            str(row["source_id"]) for row in iter_jsonl(labels) if bool(row.get("human_certified"))
        },
    }
    expected = {
        "comprehensive_metadata_sample": 8_730,
        "provider_filter_contradiction": 2_767,
        "trading_ideas_current": 6_896,
        "trading_ideas_prior": 103,
        "human_certified_current": 1_944,
    }
    actual = {name: len(values) for name, values in result.items()}
    if actual != expected:
        raise ValueError(f"prior-review authority count mismatch: {actual}")
    return result


def prepare(
    *,
    exception_queue: Path = DEFAULT_EXCEPTION_QUEUE,
    article_features: Path = DEFAULT_ARTICLE_FEATURES,
    rendered_texts: Path = DEFAULT_RENDERED_TEXTS,
    authority: Path = DEFAULT_AUTHORITY,
    comprehensive_sample: Path = DEFAULT_COMPREHENSIVE_SAMPLE,
    contradiction_controller: Path = DEFAULT_CONTRADICTION_CONTROLLER,
    trading_ledger: Path = DEFAULT_TRADING_LEDGER,
    trading_prior: Path = DEFAULT_TRADING_PRIOR,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite audit output: {output_root}")
    output_root.mkdir(parents=True)
    exceptions = list(iter_jsonl(exception_queue))
    if len(exceptions) != EXPECTED_EXCEPTION_ROWS:
        raise ValueError(f"expected {EXPECTED_EXCEPTION_ROWS} exception rows")
    if len({str(row["source_id"]) for row in exceptions}) != len(exceptions):
        raise ValueError("exception queue source IDs are not unique")
    reviewed_sets = _load_reviewed_sets(
        comprehensive_sample=comprehensive_sample,
        contradiction_controller=contradiction_controller,
        trading_ledger=trading_ledger,
        trading_prior=trading_prior,
        authority=authority,
    )
    reviewed_union = set().union(*reviewed_sets.values())
    selected = [row for row in exceptions if str(row["source_id"]) not in reviewed_union]
    counts = Counter(str(row["candidate_reason"]) for row in selected)
    if counts != {
        "ineligible_under_updated_stable_eligible_path": EXPECTED_EVENT_QUEUE,
        "eligible_under_updated_stable_ineligible_path": EXPECTED_NOISE_QUEUE,
    }:
        raise ValueError(f"unexpected unreviewed queue counts: {dict(counts)}")

    selected_by_id = {str(row["source_id"]): row for row in selected}
    metadata: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(article_features):
        source_id = str(row["source_id"])
        if source_id not in selected_by_id:
            continue
        if str(row["label"]) != str(selected_by_id[source_id]["current_label"]):
            raise ValueError(f"candidate/current-feature label mismatch: {source_id}")
        metadata[source_id] = {
            "provider": str(row.get("provider") or ""),
            "tickers": list(row.get("tickers") or ()),
            "channels": list(row.get("channels") or ()),
            "provider_tags": list(row.get("provider_tags") or ()),
        }
    if metadata.keys() != selected_by_id.keys():
        raise ValueError("selected candidate/article-feature membership mismatch")

    previews: dict[str, dict[str, Any]] = {}
    rendered_hashes: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts):
        source_id = str(row["source_id"])
        if source_id not in selected_by_id:
            continue
        text = str(row["rendered_text"])
        digest = _digest(text)
        if digest != str(row["rendered_text_hash"]):
            raise ValueError(f"rendered-text self-hash mismatch: {source_id}")
        previews[source_id] = compact_preview(text)
        rendered_hashes[source_id] = digest
    if previews.keys() != selected_by_id.keys():
        raise ValueError("selected candidate/rendered-text membership mismatch")

    ordered_ids = sorted(
        selected_by_id,
        key=lambda source_id: (
            0 if selected_by_id[source_id]["candidate_reason"].startswith("ineligible_") else 1,
            _digest(f"{AUDIT_VERSION}|candidate-order|{source_id}"),
        ),
    )
    controller_rows: list[dict[str, Any]] = []
    worker_rows: list[dict[str, Any]] = []
    for source_id in ordered_ids:
        candidate = selected_by_id[source_id]
        review_id = "PE" + _digest(f"{AUDIT_VERSION}|{source_id}")[:20]
        controller_rows.append({
            **candidate,
            "review_id": review_id,
            "rendered_text_sha256": rendered_hashes[source_id],
            "prior_review_exclusion_sources": [],
        })
        worker_rows.append({
            "review_id": review_id,
            "published_at_utc": str(candidate["published_at_utc"]),
            **metadata[source_id],
            **previews[source_id],
            "rendered_text_sha256": rendered_hashes[source_id],
        })

    controller_path = output_root / "CONTROLLER.jsonl"
    exclusion_path = output_root / "PRIOR_REVIEW_EXCLUSION_MANIFEST.json"
    _write_jsonl_new(controller_path, controller_rows)
    write_json_new(exclusion_path, {
        "source_counts": {name: len(values) for name, values in reviewed_sets.items()},
        "union_count": len(reviewed_union),
        "exception_rows": len(exceptions),
        "excluded_exception_rows": len(exceptions) - len(selected),
        "selected_exception_rows": len(selected),
        "selected_by_reason": dict(counts),
    })

    packets = _packetize(worker_rows)
    packet_root = output_root / "compact" / "packets"
    ledger_rows: list[dict[str, Any]] = []
    reviewer_load = Counter({reviewer: 0 for reviewer in REVIEWERS})
    for index, packet in enumerate(packets):
        packet_id = f"PC{index:04d}"
        reviewer = min(REVIEWERS, key=lambda value: (reviewer_load[value], value))
        reviewer_load[reviewer] += len(packet)
        packet_path = packet_root / f"{packet_id}.jsonl"
        _write_jsonl_new(packet_path, packet)
        ledger_rows.append({
            "packet_id": packet_id,
            "packet_path": str(packet_path),
            "assigned_reviewer": reviewer,
            "articles": len(packet),
            "preview_characters": sum(len(str(row["preview_text"])) for row in packet),
            "packet_sha256": sha256_path(packet_path),
        })
    ledger_path = output_root / "compact" / "PACKET_LEDGER.jsonl"
    _write_jsonl_new(ledger_path, ledger_rows)
    instructions_path = output_root / "COMPACT_REVIEW_INSTRUCTIONS.json"
    write_json_new(instructions_path, {
        "objective": "Classify forecast eligibility from only supplied metadata, title, teaser, and first three sentences.",
        "eligible": "The supplied preview independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The preview is analyst/investment opinion, technical/valuation material, price movement, a list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "needs_full_text": "The preview cannot safely establish whether a new material issuer event is independently reported.",
        "allowed_labels": sorted(ALLOWED_COMPACT_LABELS),
        "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "required_fields": [
            "review_id", "manual_label", "confidence_probability", "reason_code",
            "rationale", "evidence_excerpt", "isolation_attestation",
        ],
        "blindness": "Do not inspect controller files, current labels, matched paths, statistics, model outputs, prior reviews, or full source text.",
    })
    manifest = {
        "audit_version": AUDIT_VERSION,
        "status": "compact_packets_frozen",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "candidates": len(selected),
        "packets": len(packets),
        "reviewer_load": dict(reviewer_load),
        "inputs": {
            "exception_queue": str(exception_queue),
            "exception_queue_sha256": sha256_path(exception_queue),
            "article_features": str(article_features),
            "article_features_sha256": sha256_path(article_features),
            "rendered_texts": str(rendered_texts),
            "rendered_texts_sha256": sha256_path(rendered_texts),
        },
        "outputs": {
            "controller_sha256": sha256_path(controller_path),
            "exclusion_manifest_sha256": sha256_path(exclusion_path),
            "packet_ledger_sha256": sha256_path(ledger_path),
            "instructions_sha256": sha256_path(instructions_path),
        },
    }
    write_json_new(output_root / "PREPARE_MANIFEST.json", manifest)
    return manifest


def validate_compact_review(*, packet_path: Path, review_path: Path) -> dict[str, Any]:
    packet = list(iter_jsonl(packet_path))
    reviews = list(iter_jsonl(review_path))
    if [str(row.get("review_id")) for row in reviews] != [str(row["review_id"]) for row in packet]:
        raise ValueError("compact review identity/order mismatch")
    for source, review in zip(packet, reviews, strict=True):
        review_id = str(review["review_id"])
        if set(review) != {
            "review_id", "manual_label", "confidence_probability", "reason_code",
            "rationale", "evidence_excerpt", "isolation_attestation",
        }:
            raise ValueError(f"compact review schema mismatch: {review_id}")
        if review["manual_label"] not in ALLOWED_COMPACT_LABELS:
            raise ValueError(f"invalid compact label: {review_id}")
        if review["reason_code"] not in ALLOWED_REASONS:
            raise ValueError(f"invalid compact reason: {review_id}")
        confidence = float(review["confidence_probability"])
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"invalid compact confidence: {review_id}")
        rationale = str(review["rationale"]).strip()
        excerpt = str(review["evidence_excerpt"])
        if not rationale or len(rationale.split()) > 30:
            raise ValueError(f"invalid compact rationale: {review_id}")
        if not excerpt or len(excerpt) > 240 or excerpt not in str(source["preview_text"]):
            raise ValueError(f"compact evidence absent from preview: {review_id}")
        if review["isolation_attestation"] != {
            "used_only_supplied_packet": True,
            "used_external_context": False,
        }:
            raise ValueError(f"invalid compact isolation attestation: {review_id}")
    return {"status": "valid", "articles": len(reviews), "sha256": sha256_path(review_path)}


def ingest_compact_reviews(*, staging_root: Path, output_root: Path = DEFAULT_OUTPUT_ROOT) -> dict[str, Any]:
    ledger = list(iter_jsonl(output_root / "compact" / "PACKET_LEDGER.jsonl"))
    review_root = output_root / "compact" / "reviews"
    review_root.mkdir()
    label_counts: Counter[str] = Counter()
    output_rows: list[dict[str, Any]] = []
    for assignment in ledger:
        packet_id = str(assignment["packet_id"])
        reviewer = str(assignment["assigned_reviewer"])
        source = staging_root / "compact_reviews" / f"{reviewer}_{packet_id}.jsonl"
        if not source.exists():
            raise FileNotFoundError(f"missing compact review: {source}")
        validation = validate_compact_review(
            packet_path=Path(str(assignment["packet_path"])), review_path=source
        )
        destination = review_root / source.name
        if destination.exists():
            raise FileExistsError(destination)
        shutil.copyfile(source, destination)
        rows = list(iter_jsonl(destination))
        label_counts.update(str(row["manual_label"]) for row in rows)
        output_rows.append({
            "packet_id": packet_id,
            "reviewer_id": reviewer,
            "articles": validation["articles"],
            "review_path": str(destination),
            "review_sha256": sha256_path(destination),
        })
    if sum(label_counts.values()) != EXPECTED_EVENT_QUEUE + EXPECTED_NOISE_QUEUE:
        raise ValueError("compact review coverage mismatch")
    result = {
        "audit_version": AUDIT_VERSION,
        "status": "compact_reviews_ingested",
        "articles": sum(label_counts.values()),
        "labels": dict(label_counts),
        "review_files": output_rows,
    }
    write_json_new(output_root / "compact" / "VALIDATION.json", result)
    return result


def _compact_review_maps(output_root: Path) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    reviews: dict[str, dict[str, Any]] = {}
    reviewer_by_id: dict[str, str] = {}
    ledger = {
        str(row["packet_id"]): row for row in iter_jsonl(output_root / "compact" / "PACKET_LEDGER.jsonl")
    }
    for path in sorted((output_root / "compact" / "reviews").glob("R*_PC????.jsonl")):
        match = re.fullmatch(r"(R\d+)_(PC\d{4})", path.stem)
        if match is None:
            raise ValueError(f"invalid compact review filename: {path.name}")
        reviewer, packet_id = match.groups()
        if str(ledger[packet_id]["assigned_reviewer"]) != reviewer:
            raise ValueError(f"compact reviewer assignment mismatch: {path.name}")
        validate_compact_review(packet_path=Path(str(ledger[packet_id]["packet_path"])), review_path=path)
        for row in iter_jsonl(path):
            review_id = str(row["review_id"])
            if review_id in reviews:
                raise ValueError(f"duplicate compact review ID: {review_id}")
            reviews[review_id] = row
            reviewer_by_id[review_id] = reviewer
    if len(reviews) != EXPECTED_EVENT_QUEUE + EXPECTED_NOISE_QUEUE:
        raise ValueError("compact review collection incomplete")
    return reviews, reviewer_by_id


def _packetize_full(rows: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    packets: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    characters = 0
    for row in rows:
        size = len(str(row["rendered_text"]))
        if current and (len(current) >= 20 or characters + size > 80_000):
            packets.append(current)
            current = []
            characters = 0
        current.append(row)
        characters += size
    if current:
        packets.append(current)
    return packets


def prepare_full_first(
    *,
    rendered_texts: Path = DEFAULT_RENDERED_TEXTS,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
) -> dict[str, Any]:
    full_root = output_root / "full_first"
    if full_root.exists():
        raise FileExistsError(full_root)
    full_root.mkdir()
    compact, compact_reviewer = _compact_review_maps(output_root)
    controller = {
        str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")
    }
    worker_metadata: dict[str, dict[str, Any]] = {}
    for packet in sorted((output_root / "compact" / "packets").glob("PC????.jsonl")):
        for row in iter_jsonl(packet):
            worker_metadata[str(row["review_id"])] = {
                "published_at_utc": str(row["published_at_utc"]),
                "provider": str(row["provider"]),
                "tickers": list(row["tickers"]),
                "channels": list(row["channels"]),
                "provider_tags": list(row["provider_tags"]),
            }
    selected: dict[str, list[str]] = {}
    for review_id, review in compact.items():
        current = str(controller[review_id]["current_label"])
        compact_label = str(review["manual_label"])
        reasons: list[str] = []
        if compact_label == "needs_full_text":
            reasons.append("compact_needs_full_text")
        elif compact_label != current:
            reasons.append("compact_proposed_change")
        elif int(_digest(f"{AUDIT_VERSION}|preserve-qc|{review_id}"), 16) % 10 == 0:
            reasons.append("compact_preserve_quality_control")
        if reasons:
            selected[review_id] = reasons

    rendered: dict[str, str] = {}
    source_to_review = {
        str(row["source_id"]): review_id for review_id, row in controller.items() if review_id in selected
    }
    for row in iter_jsonl(rendered_texts):
        review_id = source_to_review.get(str(row["source_id"]))
        if review_id is None:
            continue
        text = str(row["rendered_text"])
        digest = _digest(text)
        if digest != str(row["rendered_text_hash"]) or digest != str(controller[review_id]["rendered_text_sha256"]):
            raise ValueError(f"full-text hash mismatch: {review_id}")
        rendered[review_id] = text
    if rendered.keys() != selected.keys():
        raise ValueError("full-first rendered membership mismatch")

    rows_by_excluded: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in REVIEWERS}
    for review_id in sorted(selected, key=lambda value: _digest(f"{AUDIT_VERSION}|full-first|{value}")):
        rows_by_excluded[compact_reviewer[review_id]].append({
            "review_id": review_id,
            **worker_metadata[review_id],
            "rendered_text": rendered[review_id],
            "rendered_text_sha256": str(controller[review_id]["rendered_text_sha256"]),
        })
    packet_root = full_root / "packets"
    ledger_rows: list[dict[str, Any]] = []
    reviewer_load = Counter({reviewer: 0 for reviewer in REVIEWERS})
    packet_index = 0
    oversized = 0
    for excluded in REVIEWERS:
        for packet in _packetize_full(rows_by_excluded[excluded]):
            packet_id = f"PF{packet_index:04d}"
            packet_index += 1
            allowed = [reviewer for reviewer in REVIEWERS if reviewer != excluded]
            reviewer = min(allowed, key=lambda value: (reviewer_load[value], value))
            reviewer_load[reviewer] += len(packet)
            if max(len(str(row["rendered_text"])) for row in packet) > 300_000:
                oversized += 1
            packet_path = packet_root / f"{packet_id}.jsonl"
            _write_jsonl_new(packet_path, packet)
            ledger_rows.append({
                "packet_id": packet_id,
                "packet_path": str(packet_path),
                "assigned_reviewer": reviewer,
                "excluded_reviewer": excluded,
                "articles": len(packet),
                "rendered_characters": sum(len(str(row["rendered_text"])) for row in packet),
                "packet_sha256": sha256_path(packet_path),
            })
    ledger_path = full_root / "PACKET_LEDGER.jsonl"
    _write_jsonl_new(ledger_path, ledger_rows)
    selection_path = full_root / "SELECTION.jsonl"
    _write_jsonl_new(selection_path, [
        {"review_id": review_id, "selection_reasons": reasons}
        for review_id, reasons in sorted(selected.items())
    ])
    instructions_path = full_root / "INSTRUCTIONS.json"
    write_json_new(instructions_path, {
        "objective": "Classify forecast eligibility from the complete supplied article text.",
        "eligible": "The article independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The article is opinion, technical/valuation material, price movement, list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "insufficient_information": "The complete supplied record still lacks enough evidence for a safe decision.",
        "allowed_labels": sorted(ALLOWED_FULL_LABELS),
        "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "blindness": "Do not inspect controller files, current labels, path statistics, compact reviews, models, prior reviews, or other full reviews.",
    })
    result = {
        "audit_version": AUDIT_VERSION,
        "status": "full_first_packets_frozen",
        "articles": len(selected),
        "selection_reasons": dict(Counter(reason for reasons in selected.values() for reason in reasons)),
        "packets": len(ledger_rows),
        "reviewer_load": dict(reviewer_load),
        "oversized_packets": oversized,
        "outputs": {
            "ledger_sha256": sha256_path(ledger_path),
            "selection_sha256": sha256_path(selection_path),
            "instructions_sha256": sha256_path(instructions_path),
        },
    }
    write_json_new(full_root / "MANIFEST.json", result)
    return result


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
        if review["manual_label"] not in ALLOWED_FULL_LABELS:
            raise ValueError(f"invalid full label: {review_id}")
        if review["reason_code"] not in ALLOWED_REASONS:
            raise ValueError(f"invalid full reason: {review_id}")
        confidence = float(review["confidence_probability"])
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"invalid full confidence: {review_id}")
        rationale = str(review["rationale"]).strip()
        excerpt = str(review["evidence_excerpt"])
        if not rationale or len(rationale.split()) > 30:
            raise ValueError(f"invalid full rationale: {review_id}")
        if not excerpt or len(excerpt) > 240 or excerpt not in str(source["rendered_text"]):
            raise ValueError(f"full evidence absent from text: {review_id}")
        if review["isolation_attestation"] != {
            "used_only_supplied_packet": True,
            "used_external_context": False,
        }:
            raise ValueError(f"invalid full isolation attestation: {review_id}")
    return {"status": "valid", "articles": len(reviews), "sha256": sha256_path(review_path)}


def _collect_full_reviews(
    *, output_root: Path, stage: str, packet_prefix: str
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    stage_root = output_root / stage
    ledger = {
        str(row["packet_id"]): row for row in iter_jsonl(stage_root / "PACKET_LEDGER.jsonl")
    }
    reviews: dict[str, dict[str, Any]] = {}
    reviewer_by_id: dict[str, str] = {}
    pattern = re.compile(rf"(R\d+)_({packet_prefix}\d{{4}})")
    for path in sorted((stage_root / "reviews").glob(f"R?_{packet_prefix}????.jsonl")):
        match = pattern.fullmatch(path.stem)
        if match is None:
            raise ValueError(f"invalid {stage} review filename: {path.name}")
        reviewer, packet_id = match.groups()
        assignment = ledger.get(packet_id)
        if assignment is None or str(assignment["assigned_reviewer"]) != reviewer:
            raise ValueError(f"{stage} reviewer assignment mismatch: {path.name}")
        validate_full_review(packet_path=Path(str(assignment["packet_path"])), review_path=path)
        for row in iter_jsonl(path):
            review_id = str(row["review_id"])
            if review_id in reviews:
                raise ValueError(f"duplicate {stage} review ID: {review_id}")
            reviews[review_id] = row
            reviewer_by_id[review_id] = reviewer
    expected = sum(int(row["articles"]) for row in ledger.values())
    if len(reviews) != expected:
        raise ValueError(f"{stage} review collection incomplete: {len(reviews)}/{expected}")
    return reviews, reviewer_by_id


def ingest_full_reviews(
    *, staging_root: Path, stage: str, packet_prefix: str, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    stage_root = output_root / stage
    ledger = list(iter_jsonl(stage_root / "PACKET_LEDGER.jsonl"))
    review_root = stage_root / "reviews"
    review_root.mkdir()
    label_counts: Counter[str] = Counter()
    output_rows: list[dict[str, Any]] = []
    for assignment in ledger:
        packet_id = str(assignment["packet_id"])
        reviewer = str(assignment["assigned_reviewer"])
        source = staging_root / f"{stage}_reviews" / f"{reviewer}_{packet_id}.jsonl"
        if not source.exists():
            raise FileNotFoundError(f"missing {stage} review: {source}")
        validation = validate_full_review(
            packet_path=Path(str(assignment["packet_path"])), review_path=source
        )
        destination = review_root / source.name
        if destination.exists():
            raise FileExistsError(destination)
        shutil.copyfile(source, destination)
        rows = list(iter_jsonl(destination))
        label_counts.update(str(row["manual_label"]) for row in rows)
        output_rows.append({
            "packet_id": packet_id,
            "reviewer_id": reviewer,
            "articles": validation["articles"],
            "review_path": str(destination),
            "review_sha256": sha256_path(destination),
        })
    expected = sum(int(row["articles"]) for row in ledger)
    if sum(label_counts.values()) != expected:
        raise ValueError(f"{stage} review coverage mismatch")
    result = {
        "audit_version": AUDIT_VERSION,
        "status": f"{stage}_reviews_ingested",
        "articles": expected,
        "labels": dict(label_counts),
        "review_files": output_rows,
    }
    write_json_new(stage_root / "VALIDATION.json", result)
    return result


def prepare_full_confirmation(
    *, rendered_texts: Path = DEFAULT_RENDERED_TEXTS, output_root: Path = DEFAULT_OUTPUT_ROOT
) -> dict[str, Any]:
    confirmation_root = output_root / "full_confirmation"
    if confirmation_root.exists():
        raise FileExistsError(confirmation_root)
    confirmation_root.mkdir()
    compact, compact_reviewer = _compact_review_maps(output_root)
    full, full_reviewer = _collect_full_reviews(
        output_root=output_root, stage="full_first", packet_prefix="PF"
    )
    controller = {
        str(row["review_id"]): row for row in iter_jsonl(output_root / "CONTROLLER.jsonl")
    }
    selected: dict[str, str] = {}
    for review_id, review in full.items():
        label = str(review["manual_label"])
        current = str(controller[review_id]["current_label"])
        if label == "insufficient_information":
            selected[review_id] = "full_first_insufficient"
        elif label != current:
            selected[review_id] = "full_first_proposed_change"

    worker_metadata: dict[str, dict[str, Any]] = {}
    for packet in sorted((output_root / "compact" / "packets").glob("PC????.jsonl")):
        for row in iter_jsonl(packet):
            worker_metadata[str(row["review_id"])] = {
                "published_at_utc": str(row["published_at_utc"]),
                "provider": str(row["provider"]),
                "tickers": list(row["tickers"]),
                "channels": list(row["channels"]),
                "provider_tags": list(row["provider_tags"]),
            }
    source_to_review = {
        str(controller[review_id]["source_id"]): review_id for review_id in selected
    }
    rendered: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts):
        review_id = source_to_review.get(str(row["source_id"]))
        if review_id is None:
            continue
        text = str(row["rendered_text"])
        digest = _digest(text)
        if digest != str(row["rendered_text_hash"]) or digest != str(controller[review_id]["rendered_text_sha256"]):
            raise ValueError(f"confirmation full-text hash mismatch: {review_id}")
        rendered[review_id] = text
    if rendered.keys() != selected.keys():
        raise ValueError("full-confirmation rendered membership mismatch")

    rows_by_reviewer: dict[str, list[dict[str, Any]]] = {reviewer: [] for reviewer in REVIEWERS}
    for review_id in sorted(selected, key=lambda value: _digest(f"{AUDIT_VERSION}|confirmation|{value}")):
        excluded = {compact_reviewer[review_id], full_reviewer[review_id]}
        allowed = [reviewer for reviewer in REVIEWERS if reviewer not in excluded]
        if len(allowed) != 1:
            raise ValueError(f"confirmation reviewer resolution failed: {review_id}")
        reviewer = allowed[0]
        rows_by_reviewer[reviewer].append({
            "review_id": review_id,
            **worker_metadata[review_id],
            "rendered_text": rendered[review_id],
            "rendered_text_sha256": str(controller[review_id]["rendered_text_sha256"]),
        })
    packet_root = confirmation_root / "packets"
    ledger_rows: list[dict[str, Any]] = []
    reviewer_load = Counter({reviewer: 0 for reviewer in REVIEWERS})
    packet_index = 0
    oversized = 0
    for reviewer in REVIEWERS:
        for packet in _packetize_full(rows_by_reviewer[reviewer]):
            packet_id = f"CF{packet_index:04d}"
            packet_index += 1
            reviewer_load[reviewer] += len(packet)
            if max(len(str(row["rendered_text"])) for row in packet) > 300_000:
                oversized += 1
            packet_path = packet_root / f"{packet_id}.jsonl"
            _write_jsonl_new(packet_path, packet)
            ledger_rows.append({
                "packet_id": packet_id,
                "packet_path": str(packet_path),
                "assigned_reviewer": reviewer,
                "articles": len(packet),
                "rendered_characters": sum(len(str(row["rendered_text"])) for row in packet),
                "packet_sha256": sha256_path(packet_path),
            })
    ledger_path = confirmation_root / "PACKET_LEDGER.jsonl"
    _write_jsonl_new(ledger_path, ledger_rows)
    selection_path = confirmation_root / "SELECTION.jsonl"
    _write_jsonl_new(selection_path, [
        {"review_id": review_id, "selection_reason": reason}
        for review_id, reason in sorted(selected.items())
    ])
    instructions_path = confirmation_root / "INSTRUCTIONS.json"
    write_json_new(instructions_path, {
        "objective": "Independently confirm forecast eligibility from the complete supplied article text.",
        "eligible": "The article independently reports a new/current potentially material issuer event or issuer guidance.",
        "ineligible": "The article is opinion, technical/valuation material, price movement, list/screener, preview, recap, generic context, or routine notice without a new issuer event.",
        "insufficient_information": "The complete supplied record still lacks enough evidence for a safe decision.",
        "allowed_labels": sorted(ALLOWED_FULL_LABELS),
        "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "blindness": "Do not inspect controller files, current labels, path statistics, compact reviews, first full reviews, models, or prior reviews.",
    })
    result = {
        "audit_version": AUDIT_VERSION,
        "status": "full_confirmation_packets_frozen",
        "articles": len(selected),
        "selection_reasons": dict(Counter(selected.values())),
        "packets": len(ledger_rows),
        "reviewer_load": dict(reviewer_load),
        "oversized_packets": oversized,
        "outputs": {
            "ledger_sha256": sha256_path(ledger_path),
            "selection_sha256": sha256_path(selection_path),
            "instructions_sha256": sha256_path(instructions_path),
        },
    }
    write_json_new(confirmation_root / "MANIFEST.json", result)
    return result


def resolve_final_label(
    *, current: str, full_first: str | None, full_confirmation: str | None
) -> tuple[str, str]:
    if current not in {"eligible", "ineligible"}:
        raise ValueError(f"unsupported current label: {current}")
    if full_first is None:
        return current, "compact_preserve_no_full_escalation"
    if full_first == current:
        return current, "full_first_preserve"
    if full_first in {"eligible", "ineligible"} and full_confirmation == full_first:
        return full_first, "two_full_reviews_agree_change"
    if full_first == "insufficient_information" and full_confirmation == full_first:
        return current, "two_full_insufficient_preserve"
    return current, "full_disagreement_fail_closed_preserve"


def prepare_refinement_round_two(
    *,
    exception_queue: Path = DEFAULT_REFRESHED_EXCEPTION_QUEUE,
    article_features: Path = DEFAULT_REFRESHED_ARTICLE_FEATURES,
    rendered_texts: Path = DEFAULT_RENDERED_TEXTS,
    prior_audit_root: Path = DEFAULT_OUTPUT_ROOT,
    output_root: Path = DEFAULT_REFINEMENT_ROOT,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(output_root)
    reviewed = {str(row["source_id"]) for row in iter_jsonl(prior_audit_root / "CONTROLLER.jsonl")}
    candidates = [
        row for row in iter_jsonl(exception_queue) if str(row["source_id"]) not in reviewed
    ]
    # Every exception not in the immediately prior audit must still have been excluded by an
    # older correction-grade review. Reconstruct that exact exclusion authority here.
    older = _load_reviewed_sets(
        comprehensive_sample=DEFAULT_COMPREHENSIVE_SAMPLE,
        contradiction_controller=DEFAULT_CONTRADICTION_CONTROLLER,
        trading_ledger=DEFAULT_TRADING_LEDGER,
        trading_prior=DEFAULT_TRADING_PRIOR,
        authority=DEFAULT_AUTHORITY,
    )
    older_union = set().union(*older.values())
    candidates = [row for row in candidates if str(row["source_id"]) not in older_union]
    if len(candidates) != 8 or {
        str(row["candidate_reason"]) for row in candidates
    } != {"eligible_under_updated_stable_ineligible_path"}:
        raise ValueError(f"unexpected refinement queue: {len(candidates)}")
    by_source = {str(row["source_id"]): row for row in candidates}
    metadata: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(article_features):
        source_id = str(row["source_id"])
        if source_id in by_source:
            metadata[source_id] = {
                "published_at_utc": str(by_source[source_id]["published_at_utc"]),
                "provider": str(row.get("provider") or ""),
                "tickers": list(row.get("tickers") or ()),
                "channels": list(row.get("channels") or ()),
                "provider_tags": list(row.get("provider_tags") or ()),
            }
    full_text: dict[str, str] = {}
    full_hash: dict[str, str] = {}
    for row in iter_jsonl(rendered_texts):
        source_id = str(row["source_id"])
        if source_id in by_source:
            text = str(row["rendered_text"])
            digest = _digest(text)
            if digest != str(row["rendered_text_hash"]):
                raise ValueError(f"refinement text hash mismatch: {source_id}")
            full_text[source_id] = text
            full_hash[source_id] = digest
    if metadata.keys() != by_source.keys() or full_text.keys() != by_source.keys():
        raise ValueError("refinement input membership mismatch")
    output_root.mkdir(parents=True)
    controller_rows: list[dict[str, Any]] = []
    worker_rows: list[dict[str, Any]] = []
    for source_id in sorted(by_source, key=lambda value: _digest(f"{AUDIT_VERSION}|round2|{value}")):
        review_id = "PR" + _digest(f"{AUDIT_VERSION}|round2|{source_id}")[:20]
        controller_rows.append({
            **by_source[source_id], "review_id": review_id,
            "rendered_text_sha256": full_hash[source_id],
        })
        worker_rows.append({
            "review_id": review_id, **metadata[source_id],
            "rendered_text": full_text[source_id],
            "rendered_text_sha256": full_hash[source_id],
        })
    controller_path = output_root / "CONTROLLER.jsonl"
    packet_path = output_root / "packet.jsonl"
    _write_jsonl_new(controller_path, controller_rows)
    _write_jsonl_new(packet_path, worker_rows)
    write_json_new(output_root / "INSTRUCTIONS.json", {
        "objective": "Independently classify forecast eligibility from complete supplied text.",
        "allowed_labels": sorted(ALLOWED_FULL_LABELS),
        "allowed_reason_codes": sorted(ALLOWED_REASONS),
        "blindness": "Do not inspect controller, current labels, paths, statistics, prior reviews, or the other review.",
    })
    result = {
        "status": "refinement_round_two_frozen",
        "articles": len(worker_rows),
        "first_reviewer": "R2",
        "confirmation_reviewer": "R4",
        "controller_sha256": sha256_path(controller_path),
        "packet_sha256": sha256_path(packet_path),
    }
    write_json_new(output_root / "MANIFEST.json", result)
    return result


def finalize_refinement_round_two(
    *,
    staging_root: Path,
    refinement_root: Path = DEFAULT_REFINEMENT_ROOT,
    parent_authority: Path = DEFAULT_SUCCESSOR_AUTHORITY,
    successor_authority: Path = DEFAULT_FINAL_SUCCESSOR_AUTHORITY,
) -> dict[str, Any]:
    if successor_authority.exists():
        raise FileExistsError(successor_authority)
    packet_path = refinement_root / "packet.jsonl"
    staged = {
        "R2": staging_root / "refinement_reviews" / "R2_round2.jsonl",
        "R4": staging_root / "refinement_reviews" / "R4_round2.jsonl",
    }
    reviews: dict[str, dict[str, dict[str, Any]]] = {}
    review_root = refinement_root / "reviews"
    review_root.mkdir()
    for reviewer, path in staged.items():
        validate_full_review(packet_path=packet_path, review_path=path)
        destination = review_root / path.name
        shutil.copyfile(path, destination)
        reviews[reviewer] = {str(row["review_id"]): row for row in iter_jsonl(destination)}
    controller = {
        str(row["review_id"]): row for row in iter_jsonl(refinement_root / "CONTROLLER.jsonl")
    }
    final_by_source: dict[str, str] = {}
    ledger_rows: list[dict[str, Any]] = []
    decisions: Counter[str] = Counter()
    for review_id, candidate in controller.items():
        current = str(candidate["current_label"])
        first = str(reviews["R2"][review_id]["manual_label"])
        confirmation = str(reviews["R4"][review_id]["manual_label"])
        final, decision = resolve_final_label(
            current=current, full_first=first, full_confirmation=confirmation
        )
        source_id = str(candidate["source_id"])
        final_by_source[source_id] = final
        decisions[decision] += 1
        ledger_rows.append({
            "source_id": source_id, "review_id": review_id,
            "original_label": current, "final_label": final, "changed": final != current,
            "decision_path": decision,
            "votes": [
                {**reviews["R2"][review_id], "reviewer_id": "R2", "stage": "full_first"},
                {**reviews["R4"][review_id], "reviewer_id": "R4", "stage": "full_confirmation"},
            ],
        })
    parent_hashes = json.loads((parent_authority / "HASH_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    parent_labels = parent_authority / "article_forecast_eligibility_labels.jsonl"
    if sha256_path(parent_labels) != str(parent_hashes[parent_labels.name]["sha256"]):
        raise ValueError("refinement parent label hash mismatch")
    successor_authority.mkdir(parents=True)
    ledger_path = successor_authority / "provider_path_exception_refinement_ledger.jsonl"
    _write_jsonl_new(ledger_path, sorted(ledger_rows, key=lambda row: str(row["source_id"])))
    labels_path = successor_authority / parent_labels.name
    seen: set[str] = set()
    updated = 0
    rows = 0
    counts: Counter[str] = Counter()
    with labels_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in iter_jsonl(parent_labels):
            rows += 1
            source_id = str(row["source_id"])
            if source_id in final_by_source:
                seen.add(source_id)
                original = str(row["forecast_eligibility_label"])
                final = final_by_source[source_id]
                if original != "eligible":
                    raise ValueError(f"refinement parent label drifted: {source_id}")
                if final != original:
                    row = dict(row)
                    row.update({
                        "authority_class": "codex_multi_reader_full_text",
                        "authority_detail": f"{AUDIT_VERSION}_refinement_round_2",
                        "certification_level": "codex_adjudicated", "decisive": True,
                        "forecast_eligibility_label": final,
                        "forecast_eligible": final == "eligible",
                        "usage_policy": "model_development_adjudicated",
                    })
                    updated += 1
            counts[str(row["forecast_eligibility_label"])] += 1
            handle.write(canonical_json(row) + "\n")
    if rows != 361_695 or seen != set(final_by_source):
        raise ValueError("refinement successor coverage mismatch")
    inherited = [
        "gold_issuer_sentiment_labels.jsonl", "provider_filter_correction_ledger.jsonl",
        "trading_ideas_correction_ledger.jsonl", "provider_path_exception_correction_ledger.jsonl",
    ]
    copied: list[Path] = []
    for name in inherited:
        source = parent_authority / name
        if sha256_path(source) != str(parent_hashes[name]["sha256"]):
            raise ValueError(f"refinement parent hash mismatch: {name}")
        destination = successor_authority / name
        shutil.copyfile(source, destination)
        copied.append(destination)
    report = {
        "status": "scoped_correction_grade_successor",
        "authority_version": successor_authority.name,
        "parent_authority": str(parent_authority),
        "reviewed_articles": len(final_by_source),
        "updated_primary_rows": updated,
        "decision_path_counts": dict(decisions),
        "authority_label_counts": dict(counts),
        "sentiment_byte_identical": sha256_path(copied[0]) == sha256_path(parent_authority / copied[0].name),
    }
    validation = {
        "status": "passed", "article_rows": rows, "reviewed_rows": len(final_by_source),
        "updated_rows": updated, "coverage_complete": seen == set(final_by_source),
        "parent_authority_unchanged": sha256_path(parent_labels) == str(parent_hashes[parent_labels.name]["sha256"]),
    }
    write_json_new(successor_authority / "REPORT.json", report)
    write_json_new(successor_authority / "VALIDATION.json", validation)
    write_json_new(successor_authority / "LOAD_MANIFEST.json", {
        "dataset_version": successor_authority.name, "status": report["status"],
        "parent_authority": str(parent_authority), "audit_root": str(refinement_root),
        "primary_tables": {
            "article_forecast_eligibility": {"path": str(labels_path), "rows": rows, "primary_key": ["source_id"]},
            "gold_issuer_sentiment": {"path": str(copied[0]), "rows": 16_983, "primary_key": ["unit_id"]},
        },
        "correction_ledger": str(ledger_path),
        "inherited_correction_ledgers": [str(path) for path in copied[1:]],
    })
    hash_files = [labels_path, ledger_path, *copied, successor_authority / "REPORT.json",
                  successor_authority / "VALIDATION.json", successor_authority / "LOAD_MANIFEST.json"]
    write_json_new(successor_authority / "HASH_MANIFEST.json", {
        "files": {path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in hash_files}
    })
    return report


def finalize_successor_authority(
    *,
    audit_root: Path = DEFAULT_OUTPUT_ROOT,
    parent_authority: Path = DEFAULT_AUTHORITY,
    successor_authority: Path = DEFAULT_SUCCESSOR_AUTHORITY,
) -> dict[str, Any]:
    if successor_authority.exists():
        raise FileExistsError(successor_authority)
    parent_hashes = json.loads((parent_authority / "HASH_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    required_parent_files = (
        "article_forecast_eligibility_labels.jsonl",
        "gold_issuer_sentiment_labels.jsonl",
        "provider_filter_correction_ledger.jsonl",
        "trading_ideas_correction_ledger.jsonl",
    )
    for name in required_parent_files:
        path = parent_authority / name
        if sha256_path(path) != str(parent_hashes[name]["sha256"]):
            raise ValueError(f"parent authority hash mismatch: {name}")
    controller = {
        str(row["review_id"]): row for row in iter_jsonl(audit_root / "CONTROLLER.jsonl")
    }
    compact, compact_reviewer = _compact_review_maps(audit_root)
    full, full_reviewer = _collect_full_reviews(
        output_root=audit_root, stage="full_first", packet_prefix="PF"
    )
    confirmation, confirmation_reviewer = _collect_full_reviews(
        output_root=audit_root, stage="full_confirmation", packet_prefix="CF"
    )
    final_by_source: dict[str, str] = {}
    ledger_rows: list[dict[str, Any]] = []
    decision_paths: Counter[str] = Counter()
    final_counts: Counter[str] = Counter()
    changed_counts: Counter[str] = Counter()
    for review_id, candidate in controller.items():
        source_id = str(candidate["source_id"])
        current = str(candidate["current_label"])
        first_full = full.get(review_id)
        confirmation_row = confirmation.get(review_id)
        full_label = str(first_full["manual_label"]) if first_full is not None else None
        confirmation_label = (
            str(confirmation_row["manual_label"]) if confirmation_row is not None else None
        )
        final_label, decision_path = resolve_final_label(
            current=current, full_first=full_label, full_confirmation=confirmation_label
        )
        final_by_source[source_id] = final_label
        final_counts[final_label] += 1
        decision_paths[decision_path] += 1
        if final_label != current:
            changed_counts[f"{current}_to_{final_label}"] += 1
        votes: list[dict[str, Any]] = [{
            **compact[review_id], "stage": "compact", "reviewer_id": compact_reviewer[review_id]
        }]
        if first_full is not None:
            votes.append({**first_full, "stage": "full_first", "reviewer_id": full_reviewer[review_id]})
        if confirmation_row is not None:
            votes.append({
                **confirmation_row,
                "stage": "full_confirmation",
                "reviewer_id": confirmation_reviewer[review_id],
            })
        ledger_rows.append({
            "source_id": source_id,
            "review_id": review_id,
            "candidate_reason": str(candidate["candidate_reason"]),
            "rendered_text_hash": str(candidate["rendered_text_sha256"]),
            "original_label": current,
            "final_label": final_label,
            "changed": final_label != current,
            "decision_path": decision_path,
            "votes": votes,
        })
    if len(final_by_source) != EXPECTED_EVENT_QUEUE + EXPECTED_NOISE_QUEUE:
        raise ValueError("final candidate membership mismatch")
    original_by_source = {
        str(row["source_id"]): str(row["original_label"]) for row in ledger_rows
    }

    successor_authority.mkdir(parents=True)
    ledger_path = successor_authority / "provider_path_exception_correction_ledger.jsonl"
    _write_jsonl_new(ledger_path, sorted(ledger_rows, key=lambda row: str(row["source_id"])))
    parent_labels = parent_authority / "article_forecast_eligibility_labels.jsonl"
    labels_path = successor_authority / parent_labels.name
    parent_rows = 0
    updated_rows = 0
    seen: set[str] = set()
    authority_label_counts: Counter[str] = Counter()
    with labels_path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in iter_jsonl(parent_labels):
            parent_rows += 1
            source_id = str(row["source_id"])
            final_label = final_by_source.get(source_id)
            if final_label is not None:
                seen.add(source_id)
                original = str(row["forecast_eligibility_label"])
                if original != original_by_source[source_id]:
                    raise ValueError(f"parent candidate label drifted: {source_id}")
                if final_label != original:
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
                    updated_rows += 1
            authority_label_counts[str(row["forecast_eligibility_label"])] += 1
            handle.write(canonical_json(row) + "\n")
    if parent_rows != 361_695 or seen != set(final_by_source):
        raise ValueError("successor article coverage mismatch")

    copied: list[Path] = []
    for name in required_parent_files[1:]:
        destination = successor_authority / name
        shutil.copyfile(parent_authority / name, destination)
        copied.append(destination)
    validation = {
        "status": "passed",
        "article_rows": parent_rows,
        "reviewed_exception_rows": len(final_by_source),
        "full_first_rows": len(full),
        "full_confirmation_rows": len(confirmation),
        "updated_primary_rows": updated_rows,
        "candidate_coverage_complete": seen == set(final_by_source),
        "sentiment_sha256_equal": sha256_path(copied[0]) == sha256_path(parent_authority / copied[0].name),
        "parent_authority_unchanged": sha256_path(parent_labels) == str(parent_hashes[parent_labels.name]["sha256"]),
    }
    report = {
        "status": "scoped_correction_grade_successor",
        "audit_version": AUDIT_VERSION,
        "authority_version": successor_authority.name,
        "parent_authority": str(parent_authority),
        "scope": "960 unreviewed exceptions under merged stable provider metadata paths",
        "reviewed_articles": len(final_by_source),
        "candidate_final_label_counts": dict(final_counts),
        "decision_path_counts": dict(decision_paths),
        "correction_counts": dict(changed_counts),
        "authority_label_counts": dict(authority_label_counts),
        "sentiment_byte_identical": validation["sentiment_sha256_equal"],
        "limitations": [
            "Reviewer decisions are local Codex multi-reader adjudications, not human certification.",
            "A label changes only when two independent full-text reviewers agree; all disagreements preserve the parent label.",
        ],
    }
    load_manifest = {
        "dataset_version": successor_authority.name,
        "status": "scoped_correction_grade_successor",
        "parent_authority": str(parent_authority),
        "audit_root": str(audit_root),
        "primary_tables": {
            "article_forecast_eligibility": {"path": str(labels_path), "rows": parent_rows, "primary_key": ["source_id"]},
            "gold_issuer_sentiment": {"path": str(copied[0]), "rows": 16_983, "primary_key": ["unit_id"]},
        },
        "correction_ledger": str(ledger_path),
        "inherited_correction_ledgers": [str(path) for path in copied[1:]],
    }
    write_json_new(successor_authority / "REPORT.json", report)
    write_json_new(successor_authority / "VALIDATION.json", validation)
    write_json_new(successor_authority / "LOAD_MANIFEST.json", load_manifest)
    hash_files = [labels_path, ledger_path, *copied,
                  successor_authority / "REPORT.json", successor_authority / "VALIDATION.json",
                  successor_authority / "LOAD_MANIFEST.json"]
    write_json_new(successor_authority / "HASH_MANIFEST.json", {
        "files": {path.name: {"bytes": path.stat().st_size, "sha256": sha256_path(path)} for path in hash_files}
    })
    return report
