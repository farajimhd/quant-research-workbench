from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .provider_filter_analysis import feature_names


AUDIT_VERSION = "provider_filter_contradiction_review_v1"
PACKET_CHAR_LIMIT = 80_000
PACKET_ARTICLE_LIMIT = 45
SECOND_READ_CONFIDENCE = 0.80
LABELS = {"eligible", "ineligible", "insufficient_information"}
REASON_CODES = {
    "material_event", "issuer_guidance", "financing_capital", "regulatory_clinical",
    "m_and_a", "operations_contract", "earnings_current", "analyst_action_only",
    "preview_scheduled", "recap_already_reported", "price_movement_only",
    "technical_valuation", "short_interest", "routine_halt_listing", "macro_generic",
    "screener_list", "background_reference", "insufficient_evidence",
    "other_ineligible", "other_eligible",
}
ATTESTATION = {"used_only_supplied_packet": True, "used_external_context": False}


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected object at {path}:{line_number}")
            yield value


def write_jsonl_new(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")


def write_json_new(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def word_count(value: str) -> int:
    return len(value.split())


def audit_id(source_id: str) -> str:
    return "A" + sha256_bytes(f"{AUDIT_VERSION}|article|{source_id}".encode())[:20]


def packet_sort_key(source_id: str) -> str:
    return sha256_bytes(f"{AUDIT_VERSION}|order|{source_id}".encode())


def load_semantic_ineligible(path: Path) -> set[str]:
    rows = list(iter_jsonl(path))
    if len(rows) != 709 or len({str(row["feature"]) for row in rows}) != 709:
        raise ValueError("semantic label authority must contain 709 unique features")
    return {
        str(row["feature"])
        for row in rows
        if row.get("semantic_label") == "likely_ineligible"
    }


def packetize(rows: Sequence[Mapping[str, Any]]) -> list[list[Mapping[str, Any]]]:
    packets: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    current_chars = 0
    for row in rows:
        chars = len(str(row["rendered_text"]))
        if current and (len(current) >= PACKET_ARTICLE_LIMIT or current_chars + chars > PACKET_CHAR_LIMIT):
            packets.append(current)
            current = []
            current_chars = 0
        current.append(row)
        current_chars += chars
        if chars > PACKET_CHAR_LIMIT:
            packets.append(current)
            current = []
            current_chars = 0
    if current:
        packets.append(current)
    return packets


def worker_instructions() -> dict[str, Any]:
    return {
        "objective": "Assign forecast eligibility from only the supplied complete article text.",
        "eligible": "A new or current potentially material event or issuer guidance for an identifiable tradable issuer.",
        "ineligible": (
            "Analyst-rating or price-target-only items; previews; recaps or price explanations based only on already "
            "reported events; technical, valuation, or short-interest commentary; routine halt, resumption, listing, "
            "or index notices; generic macro or political commentary; screeners, lists, and background articles."
        ),
        "insufficient_information": "The complete supplied text cannot establish eligibility reliably.",
        "required_output_fields": [
            "review_id", "manual_label", "confidence_probability", "reason_code", "rationale",
            "evidence_excerpt", "isolation_attestation",
        ],
        "allowed_labels": sorted(LABELS),
        "allowed_reason_codes": sorted(REASON_CODES),
        "rationale_max_words": 30,
        "evidence_excerpt_max_characters": 240,
        "isolation_attestation": ATTESTATION,
    }


def prepare(args: argparse.Namespace) -> None:
    if args.output.exists():
        raise FileExistsError(f"create-new audit root already exists: {args.output}")
    manifest = json.loads(args.load_manifest.read_text(encoding="utf-8"))
    labels_path = Path(manifest["primary_tables"]["article_forecast_eligibility"]["path"])
    sentiment_path = Path(manifest["primary_tables"]["gold_issuer_sentiment"]["path"])
    rendered_path = Path(manifest["external_source_text_authority"]["path"])
    expected_rendered_hash = str(manifest["external_source_text_authority"]["sha256"])
    actual_rendered_hash = sha256_file(rendered_path)
    if actual_rendered_hash != expected_rendered_hash:
        raise ValueError("rendered-text authority SHA-256 mismatch")

    ineligible_features = load_semantic_ineligible(args.semantic_labels)
    selected: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(args.article_features):
        if row.get("label") != "eligible":
            continue
        matches = sorted(ineligible_features.intersection(feature_names(row)))
        if matches:
            source_id = str(row["source_id"])
            selected[source_id] = {
                "source_id": source_id,
                "matched_semantic_ineligible_features": matches,
                "analysis_split": row["split"],
                "published_at_utc": row["published_at_text"],
            }
    if len(selected) != 2767:
        raise ValueError(f"expected 2,767 selected articles, found {len(selected)}")

    authority_rows: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(labels_path):
        source_id = str(row["source_id"])
        if source_id in selected:
            if row.get("forecast_eligibility_label") != "eligible":
                raise ValueError(f"selected current label is not eligible: {source_id}")
            authority_rows[source_id] = row
    if authority_rows.keys() != selected.keys():
        raise ValueError("selected/authority membership mismatch")

    rendered: dict[str, dict[str, Any]] = {}
    for row in iter_jsonl(rendered_path):
        source_id = str(row["source_id"])
        if source_id not in selected:
            continue
        text = str(row["rendered_text"])
        digest = sha256_bytes(text.encode())
        if digest != str(row["rendered_text_hash"]):
            raise ValueError(f"rendered-text row hash mismatch: {source_id}")
        rendered[source_id] = {"rendered_text": text, "rendered_text_hash": digest}
    if rendered.keys() != selected.keys():
        raise ValueError("selected/rendered membership mismatch")

    ordered_ids = sorted(selected, key=packet_sort_key)
    controller_rows: list[dict[str, Any]] = []
    worker_rows: list[dict[str, Any]] = []
    for source_id in ordered_ids:
        review_id = audit_id(source_id)
        controller_rows.append({
            "review_id": review_id,
            **selected[source_id],
            "current_label": "eligible",
            "published_at_utc": selected[source_id]["published_at_utc"],
            "rendered_text_hash": rendered[source_id]["rendered_text_hash"],
        })
        worker_rows.append({
            "review_id": review_id,
            "published_at_utc": selected[source_id]["published_at_utc"],
            "rendered_text": rendered[source_id]["rendered_text"],
            "rendered_text_hash": rendered[source_id]["rendered_text_hash"],
        })

    args.output.mkdir(parents=True)
    write_jsonl_new(args.output / "CONTROLLER_POPULATION.jsonl", controller_rows)
    packets = packetize(worker_rows)
    packet_index: list[dict[str, Any]] = []
    for index, packet_rows in enumerate(packets, 1):
        packet_id = f"F{index:04d}"
        payload = {
            "audit_version": AUDIT_VERSION,
            "stage": "first",
            "packet_id": packet_id,
            "instructions": worker_instructions(),
            "articles": packet_rows,
        }
        path = args.output / "packets" / "first" / f"{packet_id}.json"
        write_json_new(path, payload)
        packet_index.append({
            "packet_id": packet_id,
            "articles": len(packet_rows),
            "rendered_characters": sum(len(str(row["rendered_text"])) for row in packet_rows),
            "packet_sha256": sha256_file(path),
            "status": "pending",
        })
    write_jsonl_new(args.output / "FIRST_PACKET_INDEX.jsonl", packet_index)
    output_manifest = {
        "audit_version": AUDIT_VERSION,
        "status": "first_pass_prepared",
        "population": len(controller_rows),
        "semantic_ineligible_features": len(ineligible_features),
        "first_packets": len(packet_index),
        "rendered_characters": sum(len(str(row["rendered_text"])) for row in worker_rows),
        "packet_article_limit": PACKET_ARTICLE_LIMIT,
        "packet_character_limit": PACKET_CHAR_LIMIT,
        "source_authority": {
            "load_manifest": str(args.load_manifest),
            "load_manifest_sha256": sha256_file(args.load_manifest),
            "article_labels": str(labels_path),
            "article_labels_sha256": sha256_file(labels_path),
            "gold_sentiment": str(sentiment_path),
            "gold_sentiment_sha256": sha256_file(sentiment_path),
            "rendered_texts": str(rendered_path),
            "rendered_texts_sha256": actual_rendered_hash,
            "article_features": str(args.article_features),
            "article_features_sha256": sha256_file(args.article_features),
            "semantic_labels": str(args.semantic_labels),
            "semantic_labels_sha256": sha256_file(args.semantic_labels),
        },
        "blinding": {
            "worker_fields": ["review_id", "published_at_utc", "rendered_text", "rendered_text_hash"],
            "hidden": [
                "source_id", "current_label", "matched_feature", "feature_label", "metadata", "tags",
                "channels", "analysis_split", "support", "eligible_rate", "model_prediction",
            ],
        },
    }
    write_json_new(args.output / "MANIFEST.json", output_manifest)
    print(canonical_json(output_manifest))


def load_packets(root: Path, stage: str) -> dict[str, dict[str, Any]]:
    packets: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "packets" / stage).glob("*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        packets[str(value["packet_id"])] = value
    return packets


def validate_review_rows(packet: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    articles = list(packet["articles"])
    if len(rows) != len(articles):
        raise ValueError(f"review row count mismatch for {packet['packet_id']}")
    expected_ids = [str(row["review_id"]) for row in articles]
    actual_ids = [str(row.get("review_id") or "") for row in rows]
    if actual_ids != expected_ids or len(set(actual_ids)) != len(actual_ids):
        raise ValueError(f"review identity/order mismatch for {packet['packet_id']}")
    texts = {str(row["review_id"]): str(row["rendered_text"]) for row in articles}
    validated: list[dict[str, Any]] = []
    required = {
        "review_id", "manual_label", "confidence_probability", "reason_code", "rationale",
        "evidence_excerpt", "isolation_attestation",
    }
    for row in rows:
        if set(row) != required:
            raise ValueError(f"review schema mismatch: {row.get('review_id')}")
        review_id = str(row["review_id"])
        label = str(row["manual_label"])
        confidence = float(row["confidence_probability"])
        reason = str(row["reason_code"])
        rationale = str(row["rationale"]).strip()
        excerpt = str(row["evidence_excerpt"])
        if label not in LABELS or reason not in REASON_CODES or not 0 <= confidence <= 1:
            raise ValueError(f"invalid review decision: {review_id}")
        if not rationale or word_count(rationale) > 30:
            raise ValueError(f"invalid review rationale: {review_id}")
        if not excerpt or len(excerpt) > 240 or excerpt not in texts[review_id]:
            raise ValueError(f"invalid evidence excerpt: {review_id}")
        if row["isolation_attestation"] != ATTESTATION:
            raise ValueError(f"invalid isolation attestation: {review_id}")
        validated.append({
            "review_id": review_id,
            "manual_label": label,
            "confidence_probability": confidence,
            "reason_code": reason,
            "rationale": rationale,
            "evidence_excerpt": excerpt,
            "isolation_attestation": ATTESTATION,
        })
    return validated


def ingest(args: argparse.Namespace) -> None:
    packets = load_packets(args.root, args.stage)
    if args.packet_id not in packets:
        raise ValueError(f"unknown {args.stage} packet: {args.packet_id}")
    if args.stage != "first":
        index_path = args.root / f"{args.stage.upper()}_PACKET_INDEX.jsonl"
        index = {str(row["packet_id"]): row for row in iter_jsonl(index_path)}
        excluded = set(index[args.packet_id]["excluded_reviewer_ids"])
        if args.reviewer_id in excluded:
            raise ValueError(f"reviewer is not independent for {args.packet_id}")
    rows = list(iter_jsonl(args.input))
    validated = validate_review_rows(packets[args.packet_id], rows)
    output = args.root / "reviews" / args.stage / f"{args.packet_id}.jsonl"
    lineage = args.root / "reviews" / args.stage / f"{args.packet_id}.lineage.json"
    if output.exists() or lineage.exists():
        raise FileExistsError(f"duplicate review completion: {args.packet_id}")
    enriched = [
        {**row, "stage": args.stage, "packet_id": args.packet_id, "reviewer_id": args.reviewer_id}
        for row in validated
    ]
    write_jsonl_new(output, enriched)
    write_json_new(lineage, {
        "audit_version": AUDIT_VERSION,
        "stage": args.stage,
        "packet_id": args.packet_id,
        "reviewer_id": args.reviewer_id,
        "input_sha256": sha256_file(args.input),
        "packet_sha256": sha256_bytes(canonical_json(packets[args.packet_id]).encode()),
        "validated_rows": len(validated),
        "labels": dict(Counter(row["manual_label"] for row in validated)),
        "output_sha256": sha256_file(output),
    })
    print(canonical_json({"packet_id": args.packet_id, "validated_rows": len(validated)}))


def collect_reviews(root: Path, stage: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "reviews" / stage).glob("*.jsonl")):
        for row in iter_jsonl(path):
            review_id = str(row["review_id"])
            if review_id in rows:
                raise ValueError(f"duplicate {stage} review: {review_id}")
            rows[review_id] = row
    return rows


def prepare_followup(root: Path, stage: str) -> dict[str, Any]:
    if stage not in {"second", "third"}:
        raise ValueError(stage)
    first = collect_reviews(root, "first")
    population = list(iter_jsonl(root / "CONTROLLER_POPULATION.jsonl"))
    expected = {str(row["review_id"]) for row in population}
    if first.keys() != expected:
        raise ValueError(f"first-pass coverage incomplete: {len(first)}/{len(expected)}")
    prior_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for review_id, row in first.items():
        prior_by_id[review_id].append(row)
    if stage == "second":
        selected_ids = {
            review_id for review_id, row in first.items()
            if row["manual_label"] != "eligible" or float(row["confidence_probability"]) < SECOND_READ_CONFIDENCE
        }
    else:
        second = collect_reviews(root, "second")
        second_packets = load_packets(root, "second")
        expected_second = {
            str(article["review_id"])
            for packet in second_packets.values()
            for article in packet["articles"]
        }
        if second.keys() != expected_second:
            raise ValueError(f"second-pass coverage incomplete: {len(second)}/{len(expected_second)}")
        for review_id, row in second.items():
            prior_by_id[review_id].append(row)
        selected_ids = {
            review_id for review_id in second
            if first[review_id]["manual_label"] != second[review_id]["manual_label"]
        }

    first_packets = load_packets(root, "first")
    article_by_id = {
        str(article["review_id"]): article
        for packet in first_packets.values()
        for article in packet["articles"]
    }
    selected_rows = [article_by_id[review_id] for review_id in sorted(selected_ids, key=lambda value: sha256_bytes(f"{AUDIT_VERSION}|{stage}|{value}".encode()))]
    grouped_rows: dict[tuple[str, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in selected_rows:
        excluded = tuple(sorted({
            str(prior["reviewer_id"])
            for prior in prior_by_id[str(row["review_id"])]
        }))
        grouped_rows[excluded].append(row)
    packets_with_exclusions: list[tuple[list[Mapping[str, Any]], tuple[str, ...]]] = []
    for excluded in sorted(grouped_rows):
        packets_with_exclusions.extend((packet, excluded) for packet in packetize(grouped_rows[excluded]))
    index: list[dict[str, Any]] = []
    for number, (packet_rows, excluded_reviewers) in enumerate(packets_with_exclusions, 1):
        packet_id = f"{'S' if stage == 'second' else 'T'}{number:04d}"
        payload = {
            "audit_version": AUDIT_VERSION,
            "stage": stage,
            "packet_id": packet_id,
            "instructions": worker_instructions(),
            "articles": packet_rows,
        }
        path = root / "packets" / stage / f"{packet_id}.json"
        write_json_new(path, payload)
        index.append({
            "packet_id": packet_id,
            "articles": len(packet_rows),
            "rendered_characters": sum(len(str(row["rendered_text"])) for row in packet_rows),
            "excluded_reviewer_ids": list(excluded_reviewers),
            "packet_sha256": sha256_file(path),
            "status": "pending",
        })
    write_jsonl_new(root / f"{stage.upper()}_PACKET_INDEX.jsonl", index)
    result = {"stage": stage, "articles": len(selected_rows), "packets": len(packets_with_exclusions)}
    print(canonical_json(result))
    return result


def reconcile_labels(first: Mapping[str, Mapping[str, Any]], second: Mapping[str, Mapping[str, Any]], third: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, str], set[str]]:
    final: dict[str, str] = {}
    unresolved: set[str] = set()
    for review_id, first_row in first.items():
        if review_id not in second:
            final[review_id] = str(first_row["manual_label"])
            continue
        votes = [str(first_row["manual_label"]), str(second[review_id]["manual_label"])]
        if votes[0] == votes[1]:
            final[review_id] = votes[0]
            continue
        if review_id not in third:
            raise ValueError(f"missing third review: {review_id}")
        votes.append(str(third[review_id]["manual_label"]))
        counts = Counter(votes)
        label, count = counts.most_common(1)[0]
        if count < 2:
            unresolved.add(review_id)
            final[review_id] = "eligible"
        else:
            final[review_id] = label
    return final, unresolved


def finalize(args: argparse.Namespace) -> None:
    if args.successor.exists():
        raise FileExistsError(f"create-new successor already exists: {args.successor}")
    manifest = json.loads((args.root / "MANIFEST.json").read_text(encoding="utf-8"))
    source = manifest["source_authority"]
    labels_path = Path(source["article_labels"])
    sentiment_path = Path(source["gold_sentiment"])
    if sha256_file(labels_path) != source["article_labels_sha256"] or sha256_file(sentiment_path) != source["gold_sentiment_sha256"]:
        raise ValueError("source primary authority changed")
    population = {str(row["review_id"]): row for row in iter_jsonl(args.root / "CONTROLLER_POPULATION.jsonl")}
    source_to_review = {str(row["source_id"]): review_id for review_id, row in population.items()}
    first = collect_reviews(args.root, "first")
    if first.keys() != population.keys():
        raise ValueError("first review coverage incomplete")
    second = collect_reviews(args.root, "second")
    third = collect_reviews(args.root, "third")
    final, unresolved = reconcile_labels(first, second, third)

    args.successor.mkdir(parents=True)
    output_labels = args.successor / "article_forecast_eligibility_labels.jsonl"
    ledger_rows: list[dict[str, Any]] = []
    label_counts: Counter[str] = Counter()
    corrections = Counter()
    output_rows: list[dict[str, Any]] = []
    for row in iter_jsonl(labels_path):
        value = dict(row)
        source_id = str(value["source_id"])
        if source_id in source_to_review:
            review_id = source_to_review[source_id]
            final_label = final[review_id]
            authority_label = "insufficient_short_text" if final_label == "insufficient_information" else final_label
            changed = authority_label != "eligible"
            votes = [first[review_id]]
            if review_id in second:
                votes.append(second[review_id])
            if review_id in third:
                votes.append(third[review_id])
            if changed:
                value.update({
                    "forecast_eligibility_label": authority_label,
                    "forecast_eligible": authority_label == "eligible",
                    "decisive": authority_label in {"eligible", "ineligible"},
                    "authority_class": "codex_multi_reader_full_text",
                    "authority_detail": AUDIT_VERSION,
                    "certification_level": "codex_adjudicated",
                    "human_certified": False,
                    "usage_policy": "model_development_adjudicated",
                })
                corrections[authority_label] += 1
            ledger_rows.append({
                "review_id": review_id,
                "source_id": source_id,
                "original_label": "eligible",
                "final_label": authority_label,
                "changed": changed,
                "unresolved_three_distinct_votes": review_id in unresolved,
                "rendered_text_hash": population[review_id]["rendered_text_hash"],
                "matched_semantic_ineligible_features": population[review_id]["matched_semantic_ineligible_features"],
                "votes": votes,
            })
        label_counts[str(value["forecast_eligibility_label"])] += 1
        output_rows.append(value)
    if len(output_rows) != 361695 or len({str(row["source_id"]) for row in output_rows}) != 361695:
        raise ValueError("successor article membership failure")
    write_jsonl_new(output_labels, output_rows)
    write_jsonl_new(args.successor / "provider_filter_correction_ledger.jsonl", ledger_rows)
    shutil.copyfile(sentiment_path, args.successor / "gold_issuer_sentiment_labels.jsonl")
    if sha256_file(sentiment_path) != sha256_file(args.successor / "gold_issuer_sentiment_labels.jsonl"):
        raise ValueError("gold sentiment copy changed")

    report = {
        "authority_version": "forecast_eligibility_sentiment_authority_provider_filter_v1",
        "audit_version": AUDIT_VERSION,
        "scope": "2,767 current-eligible articles matching at least one blind-semantic likely-ineligible feature",
        "article_rows": len(output_rows),
        "reviewed_articles": len(ledger_rows),
        "first_reviews": len(first),
        "second_reviews": len(second),
        "third_reviews": len(third),
        "unresolved_three_distinct_votes": len(unresolved),
        "corrections": dict(corrections),
        "unchanged_reviewed": sum(not bool(row["changed"]) for row in ledger_rows),
        "label_counts": dict(label_counts),
        "sentiment_byte_identical": True,
        "limitations": [
            "This is a scoped correction successor, not completion of the separate 35,995 model-mismatch audit.",
            "Reviewer decisions are Codex multi-reader adjudications, not human certification.",
            "Matched metadata features selected the population but were hidden from semantic reviewers.",
        ],
    }
    write_json_new(args.successor / "REPORT.json", report)
    validation = {
        "status": "passed",
        "article_rows": len(output_rows),
        "unique_article_ids": len({str(row["source_id"]) for row in output_rows}),
        "review_population": len(population),
        "review_ledger_rows": len(ledger_rows),
        "first_coverage_complete": first.keys() == population.keys(),
        "second_required_articles": len(second),
        "third_required_articles": len(third),
        "gold_sentiment_sha256_equal": True,
        "original_authority_unchanged": sha256_file(labels_path) == source["article_labels_sha256"],
    }
    write_json_new(args.successor / "VALIDATION.json", validation)
    load_manifest = {
        "dataset_version": report["authority_version"],
        "status": "scoped_correction_grade_successor",
        "primary_tables": {
            "article_forecast_eligibility": {"path": str(output_labels), "rows": len(output_rows), "primary_key": ["source_id"]},
            "gold_issuer_sentiment": {"path": str(args.successor / "gold_issuer_sentiment_labels.jsonl"), "rows": 16983, "primary_key": ["unit_id"]},
        },
        "correction_ledger": str(args.successor / "provider_filter_correction_ledger.jsonl"),
        "parent_authority": str(labels_path.parent),
        "audit_root": str(args.root),
    }
    write_json_new(args.successor / "LOAD_MANIFEST.json", load_manifest)
    hash_names = [
        "article_forecast_eligibility_labels.jsonl", "gold_issuer_sentiment_labels.jsonl",
        "provider_filter_correction_ledger.jsonl", "REPORT.json", "VALIDATION.json", "LOAD_MANIFEST.json",
    ]
    write_json_new(args.successor / "HASH_MANIFEST.json", {
        "files": {
            name: {"bytes": (args.successor / name).stat().st_size, "sha256": sha256_file(args.successor / name)}
            for name in hash_names
        }
    })
    print(canonical_json(report))


def summarize_audit(args: argparse.Namespace) -> None:
    root = args.root
    if any((root / name).exists() for name in ("REPORT.json", "REPORT.md", "VALIDATION.json", "HASH_MANIFEST.json")):
        raise FileExistsError("audit summary outputs already exist")
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    population = {str(row["review_id"]): row for row in iter_jsonl(root / "CONTROLLER_POPULATION.jsonl")}
    first = collect_reviews(root, "first")
    second = collect_reviews(root, "second")
    third = collect_reviews(root, "third")
    final, unresolved = reconcile_labels(first, second, third)
    second_independent = all(first[review_id]["reviewer_id"] != row["reviewer_id"] for review_id, row in second.items())
    third_independent = all(
        row["reviewer_id"] not in {first[review_id]["reviewer_id"], second[review_id]["reviewer_id"]}
        for review_id, row in third.items()
    )
    successor_report = json.loads((args.successor / "REPORT.json").read_text(encoding="utf-8"))
    report = {
        "audit_version": AUDIT_VERSION,
        "status": "complete",
        "population": len(population),
        "first_reviews": len(first),
        "second_reviews": len(second),
        "third_reviews": len(third),
        "final_labels": dict(Counter(final.values())),
        "unresolved_three_distinct_votes": len(unresolved),
        "successor": str(args.successor),
        "successor_corrections": successor_report["corrections"],
        "worker_pool": {"created": 3, "reviewer_ids": ["R1", "R2", "R3"]},
    }
    write_json_new(root / "REPORT.json", report)
    lines = [
        "# Provider-Filter Contradiction Review",
        "",
        f"- Population: {len(population):,}",
        f"- First reviews: {len(first):,}",
        f"- Second reviews: {len(second):,}",
        f"- Third reviews: {len(third):,}",
        f"- Final eligible: {sum(label == 'eligible' for label in final.values()):,}",
        f"- Final ineligible: {sum(label == 'ineligible' for label in final.values()):,}",
        f"- Final insufficient: {sum(label == 'insufficient_information' for label in final.values()):,}",
        f"- Three-distinct-vote unresolved: {len(unresolved):,}",
        "",
        "Workers saw only opaque review IDs, publication time, complete rendered text, and verified rendered-text hashes. Current labels, matched features, metadata, statistics, model outputs, and prior votes were hidden.",
        "",
        f"Scoped successor: `{args.successor}`",
    ]
    (root / "REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    validation = {
        "status": "passed",
        "population_rows": len(population),
        "unique_review_ids": len(population),
        "first_coverage_complete": first.keys() == population.keys(),
        "second_reviewer_independence": second_independent,
        "third_reviewer_independence": third_independent,
        "third_coverage_complete": len(third) == sum(
            first[review_id]["manual_label"] != row["manual_label"] for review_id, row in second.items()
        ),
        "successor_validation_status": json.loads((args.successor / "VALIDATION.json").read_text(encoding="utf-8"))["status"],
    }
    if not all(value is True for key, value in validation.items() if key != "status" and isinstance(value, bool)):
        raise ValueError(f"audit validation failed: {validation}")
    write_json_new(root / "VALIDATION.json", validation)
    artifact_paths = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path.name != "HASH_MANIFEST.json"
    )
    write_json_new(root / "HASH_MANIFEST.json", {
        "audit_version": AUDIT_VERSION,
        "files": {
            str(path.relative_to(root)): {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in artifact_paths
        },
    })
    print(canonical_json({**report, "hashed_files": len(artifact_paths)}))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Blindly review provider-filter label contradictions.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--load-manifest", type=Path, required=True)
    prepare_parser.add_argument("--article-features", type=Path, required=True)
    prepare_parser.add_argument("--semantic-labels", type=Path, required=True)
    prepare_parser.add_argument("--output", type=Path, required=True)
    ingest_parser = sub.add_parser("ingest")
    ingest_parser.add_argument("--root", type=Path, required=True)
    ingest_parser.add_argument("--stage", choices=("first", "second", "third"), required=True)
    ingest_parser.add_argument("--packet-id", required=True)
    ingest_parser.add_argument("--reviewer-id", required=True)
    ingest_parser.add_argument("--input", type=Path, required=True)
    followup_parser = sub.add_parser("prepare-followup")
    followup_parser.add_argument("--root", type=Path, required=True)
    followup_parser.add_argument("--stage", choices=("second", "third"), required=True)
    finalize_parser = sub.add_parser("finalize")
    finalize_parser.add_argument("--root", type=Path, required=True)
    finalize_parser.add_argument("--successor", type=Path, required=True)
    summarize_parser = sub.add_parser("summarize")
    summarize_parser.add_argument("--root", type=Path, required=True)
    summarize_parser.add_argument("--successor", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "prepare":
        prepare(args)
    elif args.command == "ingest":
        ingest(args)
    elif args.command == "prepare-followup":
        prepare_followup(args.root, args.stage)
    elif args.command == "finalize":
        finalize(args)
    else:
        summarize_audit(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
