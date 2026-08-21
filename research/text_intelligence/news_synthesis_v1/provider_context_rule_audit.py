from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .provider_filter_analysis import _expected_input_paths, canonical_json, iter_jsonl, sha256_path


RULE_AUDIT_VERSION = "news_synthesis_provider_context_rule_audit_v1"
REVIEW_LABELS = frozenset(("eligible", "ineligible", "insufficient_information"))
SAFE_ANALYST_CHANNELS = frozenset((
    "analyst ratings", "news", "price target", "hot", "reiteration",
    "initiation", "upgrades", "downgrades",
))
ZERO_EXCEPTION_CONTEXT_TAGS = frozenset((
    "bzi-aar", "bzi-shorthist", "bzi-uoa", "bzi-pe", "rsi", "$500 dividend",
    "bzi-ipopreview", "overbought stocks", "oversold stocks",
))


def proposed_rule_names(row: Mapping[str, Any]) -> tuple[str, ...]:
    if str(row.get("provider") or "").casefold() != "benzinga":
        return ()
    channels = frozenset(str(value).casefold() for value in row.get("channels") or ())
    tags = frozenset(str(value).casefold() for value in row.get("provider_tags") or ())
    names: list[str] = []
    if (
        "analyst ratings" in channels
        and channels.issubset(SAFE_ANALYST_CHANNELS)
        and (channels == {"analyst ratings"} or "news" in channels)
    ):
        names.append("benzinga_direct_analyst_action_family")
    if channels == {"options"}:
        names.append("benzinga_options_only_family")
    if channels == {"movers"}:
        names.append("benzinga_movers_only_family")
    if "most accurate analysts" in tags:
        names.append("benzinga_most_accurate_analysts_family")
    if tags.intersection(ZERO_EXCEPTION_CONTEXT_TAGS):
        names.append("benzinga_validated_context_tag_family_v2")
    return tuple(names)


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    count = 0
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
            count += 1
    return count


def _review_id(source_id: str) -> str:
    return "R" + hashlib.sha256(f"{RULE_AUDIT_VERSION}\0{source_id}".encode()).hexdigest()[:20]


def prepare_rule_audit(
    *,
    authority_root: Path,
    metadata_root: Path,
    article_features: Path,
    output_root: Path,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"create-new audit root exists: {output_root}")
    paths = _expected_input_paths(authority_root, metadata_root)
    feature_rows = list(iter_jsonl(article_features))
    candidate_counts: dict[str, Counter[str]] = defaultdict(Counter)
    selected: dict[str, dict[str, Any]] = {}
    for row in feature_rows:
        label = str(row["label"])
        for rule_name in proposed_rule_names(row):
            candidate_counts[rule_name][str(row["split"])] += 1
            candidate_counts[rule_name][f"{row['split']}:{label}"] += 1
            candidate_counts[rule_name][label] += 1
            if label == "eligible":
                source_id = str(row["source_id"])
                selected.setdefault(source_id, {
                    "source_id": source_id,
                    "published_at_utc": row["published_at_text"],
                    "split": row["split"],
                    "matched_rules": [],
                })["matched_rules"].append(rule_name)

    rendered: dict[str, dict[str, str]] = {}
    for row in iter_jsonl(paths.rendered_texts):
        source_id = str(row["source_id"])
        if source_id not in selected:
            continue
        text = str(row.get("rendered_text") or "")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if digest != str(row.get("rendered_text_hash") or ""):
            raise ValueError(f"rendered-text hash mismatch: {source_id}")
        rendered[source_id] = {"rendered_text": text, "rendered_text_hash": digest}
    if set(rendered) != set(selected):
        raise ValueError("eligible exception/rendered membership mismatch")

    output_root.mkdir(parents=True)
    controller_rows = []
    worker_rows = []
    for source_id in sorted(selected, key=lambda value: hashlib.sha256(value.encode()).hexdigest()):
        review_id = _review_id(source_id)
        controller_rows.append({
            "review_id": review_id,
            **selected[source_id],
            "authority_label": "eligible",
            "rendered_text_hash": rendered[source_id]["rendered_text_hash"],
        })
        worker_rows.append({
            "review_id": review_id,
            "published_at_utc": selected[source_id]["published_at_utc"],
            **rendered[source_id],
        })
    controller_path = output_root / "CONTROLLER_ELIGIBLE_EXCEPTIONS.jsonl"
    worker_path = output_root / "BLIND_REVIEW_PACKET.jsonl"
    _write_jsonl(controller_path, controller_rows)
    _write_jsonl(worker_path, worker_rows)
    metrics = {
        name: dict(sorted(counts.items()))
        for name, counts in sorted(candidate_counts.items())
    }
    manifest = {
        "audit_version": RULE_AUDIT_VERSION,
        "status": "prepared",
        "candidate_metrics": metrics,
        "eligible_exception_articles": len(worker_rows),
        "blinding": {
            "worker_fields": ["review_id", "published_at_utc", "rendered_text", "rendered_text_hash"],
            "hidden": ["source_id", "authority_label", "split", "matched_rules", "candidate_metrics"],
        },
        "inputs": {
            "article_features": str(article_features),
            "article_features_sha256": sha256_path(article_features),
            "rendered_texts": str(paths.rendered_texts),
            "rendered_texts_sha256": sha256_path(paths.rendered_texts),
        },
        "outputs": {
            controller_path.name: {"rows": len(controller_rows), "sha256": sha256_path(controller_path)},
            worker_path.name: {"rows": len(worker_rows), "sha256": sha256_path(worker_path)},
        },
    }
    (output_root / "MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def _load_validated_review(packet_rows: list[dict[str, Any]], path: Path) -> dict[str, dict[str, Any]]:
    rows = list(iter_jsonl(path))
    expected_ids = [str(row["review_id"]) for row in packet_rows]
    actual_ids = [str(row.get("review_id") or "") for row in rows]
    if actual_ids != expected_ids or len(actual_ids) != len(set(actual_ids)):
        raise ValueError(f"review identity/order mismatch: {path}")
    packet_by_id = {str(row["review_id"]): row for row in packet_rows}
    for row in rows:
        review_id = str(row["review_id"])
        if row.get("manual_label") not in REVIEW_LABELS:
            raise ValueError(f"invalid review label: {review_id}")
        confidence = row.get("confidence_probability")
        if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
            raise ValueError(f"invalid review confidence: {review_id}")
        excerpt = str(row.get("evidence_excerpt") or "")
        if not excerpt or excerpt not in str(packet_by_id[review_id]["rendered_text"]):
            raise ValueError(f"review evidence is not an exact source substring: {review_id}")
        attestation = row.get("isolation_attestation")
        if attestation != {"used_only_supplied_packet": True, "used_external_context": False}:
            raise ValueError(f"invalid isolation attestation: {review_id}")
    return {str(row["review_id"]): row for row in rows}


def prepare_adjudication(output_root: Path, review_one: Path, review_two: Path) -> dict[str, Any]:
    packet_rows = list(iter_jsonl(output_root / "BLIND_REVIEW_PACKET.jsonl"))
    first = _load_validated_review(packet_rows, review_one)
    second = _load_validated_review(packet_rows, review_two)
    disagreements = [
        row for row in packet_rows
        if first[str(row["review_id"])]["manual_label"]
        != second[str(row["review_id"])]["manual_label"]
    ]
    path = output_root / "BLIND_ADJUDICATION_PACKET.jsonl"
    _write_jsonl(path, disagreements)
    report = {
        "audit_version": RULE_AUDIT_VERSION,
        "status": "adjudication_prepared",
        "first_review_sha256": sha256_path(review_one),
        "second_review_sha256": sha256_path(review_two),
        "articles": len(packet_rows),
        "agreements": len(packet_rows) - len(disagreements),
        "disagreements": len(disagreements),
        "adjudication_packet": str(path),
        "adjudication_packet_sha256": sha256_path(path),
        "votes_hidden_from_adjudicator": True,
    }
    (output_root / "ADJUDICATION_MANIFEST.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def finalize_rule_audit(
    output_root: Path,
    review_one: Path,
    review_two: Path,
    adjudication: Path,
) -> dict[str, Any]:
    packet_rows = list(iter_jsonl(output_root / "BLIND_REVIEW_PACKET.jsonl"))
    first = _load_validated_review(packet_rows, review_one)
    second = _load_validated_review(packet_rows, review_two)
    disagreements = [
        row for row in packet_rows
        if first[str(row["review_id"])]["manual_label"]
        != second[str(row["review_id"])]["manual_label"]
    ]
    third = _load_validated_review(disagreements, adjudication)
    controller = {
        str(row["review_id"]): row
        for row in iter_jsonl(output_root / "CONTROLLER_ELIGIBLE_EXCEPTIONS.jsonl")
    }
    decisions = []
    rule_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for packet in packet_rows:
        review_id = str(packet["review_id"])
        labels = (first[review_id]["manual_label"], second[review_id]["manual_label"])
        if labels[0] == labels[1]:
            final_label = labels[0]
            decision_source = "reader_agreement"
        else:
            final_label = third[review_id]["manual_label"]
            decision_source = "blind_adjudication"
        hidden = controller[review_id]
        for rule_name in hidden["matched_rules"]:
            rule_counts[str(rule_name)][final_label] += 1
        decisions.append({
            "review_id": review_id,
            "source_id": hidden["source_id"],
            "authority_label": hidden["authority_label"],
            "blind_final_label": final_label,
            "decision_source": decision_source,
            "matched_rules": hidden["matched_rules"],
            "rendered_text_hash": hidden["rendered_text_hash"],
        })
    decisions_path = output_root / "FINAL_BLIND_DECISIONS.jsonl"
    _write_jsonl(decisions_path, decisions)
    report = {
        "audit_version": RULE_AUDIT_VERSION,
        "status": "complete",
        "articles": len(packet_rows),
        "reader_agreements": len(packet_rows) - len(disagreements),
        "blind_adjudications": len(disagreements),
        "final_labels": dict(sorted(Counter(row["blind_final_label"] for row in decisions).items())),
        "rule_exception_labels": {
            name: dict(sorted(counts.items())) for name, counts in sorted(rule_counts.items())
        },
        "inputs": {
            review_one.name: sha256_path(review_one),
            review_two.name: sha256_path(review_two),
            adjudication.name: sha256_path(adjudication),
        },
        "output": {"path": str(decisions_path), "rows": len(decisions), "sha256": sha256_path(decisions_path)},
    }
    (output_root / "FINAL_REPORT.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
