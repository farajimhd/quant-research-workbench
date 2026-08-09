from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from .forecast_eligibility_sampling import REVIEW_BATCH_SIZE


ELIGIBILITY_VALUES = frozenset(("eligible", "ineligible", "insufficient_context"))
CONFIDENCE_VALUES = frozenset(("high", "medium", "low"))


def prepare_second_pass(
    run_root: Path,
    review_files: Iterable[Path],
    *,
    qa_fraction: float = 0.10,
) -> dict[str, Any]:
    if not 0 <= qa_fraction <= 1:
        raise ValueError("qa_fraction must be between zero and one")
    inputs = _load_blind_inputs(run_root / "blind_review_batches")
    first = _load_and_validate_reviews(review_files, expected_inputs=inputs)
    answer_key = {row["review_id"]: row["source_id"] for row in _read_jsonl(run_root / "review_answer_key.jsonl")}
    predictions = {row["source_id"]: row["forecast_eligible_predicted"] for row in _read_jsonl(run_root / "engine_predictions.jsonl")}

    selected: dict[str, str] = {}
    agreement_ids: list[str] = []
    for review_id, review in first.items():
        label = review["eligibility"]
        if label == "insufficient_context":
            selected[review_id] = "first_pass_abstention"
            continue
        prediction = predictions[answer_key[review_id]]
        if prediction is None:
            selected[review_id] = "engine_failure"
            continue
        human = label == "eligible"
        if human != bool(prediction):
            selected[review_id] = "human_engine_disagreement"
        else:
            agreement_ids.append(review_id)

    qa_count = math.ceil(len(agreement_ids) * qa_fraction)
    qa_ids = sorted(agreement_ids, key=lambda value: _order_key(value, "qa"))[:qa_count]
    for review_id in qa_ids:
        selected[review_id] = "deterministic_agreement_qa"

    review_root = run_root / "reviews"
    review_root.mkdir(exist_ok=True)
    _write_jsonl(review_root / "first_pass.jsonl", (first[key] for key in sorted(first)))
    selected_rows = [inputs[key] for key in sorted(selected, key=lambda value: _order_key(value, "second"))]
    batch_root = run_root / "blind_review_batches_second"
    batch_root.mkdir(exist_ok=False)
    for index in range(0, len(selected_rows), REVIEW_BATCH_SIZE):
        _write_jsonl(
            batch_root / f"batch_{index // REVIEW_BATCH_SIZE + 1:03d}.jsonl",
            selected_rows[index:index + REVIEW_BATCH_SIZE],
        )
    manifest = {
        "version": "news_synthesis_forecast_eligibility_review_selection_v1",
        "first_pass_rows": len(first),
        "first_pass_counts": dict(sorted(Counter(row["eligibility"] for row in first.values()).items())),
        "qa_fraction": qa_fraction,
        "second_pass_rows": len(selected_rows),
        "second_pass_batches": math.ceil(len(selected_rows) / REVIEW_BATCH_SIZE),
        "selection_reasons": dict(sorted(Counter(selected.values()).items())),
        "selected_review_ids_sha256": _sha256_json(sorted(selected)),
        "selected": [{"review_id": key, "reason": selected[key]} for key in sorted(selected)],
    }
    (run_root / "second_pass_selection_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def prepare_third_pass(run_root: Path, review_files: Iterable[Path]) -> dict[str, Any]:
    inputs = _load_blind_inputs(run_root / "blind_review_batches_second")
    second = _load_and_validate_reviews(review_files, expected_inputs=inputs)
    first = {row["review_id"]: row for row in _read_jsonl(run_root / "reviews" / "first_pass.jsonl")}
    selected: dict[str, str] = {}
    for review_id, second_review in second.items():
        first_label = first[review_id]["eligibility"]
        second_label = second_review["eligibility"]
        if first_label == second_label and first_label != "insufficient_context":
            continue
        if first_label == "insufficient_context" or second_label == "insufficient_context":
            selected[review_id] = "human_abstention"
        else:
            selected[review_id] = "human_disagreement"

    _write_jsonl(run_root / "reviews" / "second_pass.jsonl", (second[key] for key in sorted(second)))
    original_inputs = _load_blind_inputs(run_root / "blind_review_batches")
    selected_rows = [original_inputs[key] for key in sorted(selected, key=lambda value: _order_key(value, "third"))]
    batch_root = run_root / "blind_review_batches_third"
    batch_root.mkdir(exist_ok=False)
    for index in range(0, len(selected_rows), REVIEW_BATCH_SIZE):
        _write_jsonl(
            batch_root / f"batch_{index // REVIEW_BATCH_SIZE + 1:03d}.jsonl",
            selected_rows[index:index + REVIEW_BATCH_SIZE],
        )
    manifest = {
        "version": "news_synthesis_forecast_eligibility_third_pass_selection_v1",
        "second_pass_rows": len(second),
        "second_pass_counts": dict(sorted(Counter(row["eligibility"] for row in second.values()).items())),
        "third_pass_rows": len(selected_rows),
        "third_pass_batches": math.ceil(len(selected_rows) / REVIEW_BATCH_SIZE),
        "selection_reasons": dict(sorted(Counter(selected.values()).items())),
        "selected_review_ids_sha256": _sha256_json(sorted(selected)),
        "selected": [{"review_id": key, "reason": selected[key]} for key in sorted(selected)],
    }
    (run_root / "third_pass_selection_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


def finalize_screening(run_root: Path, review_files: Iterable[Path]) -> dict[str, Any]:
    third_inputs = _load_blind_inputs(run_root / "blind_review_batches_third")
    third = _load_and_validate_reviews(review_files, expected_inputs=third_inputs) if third_inputs else {}
    first = {row["review_id"]: row for row in _read_jsonl(run_root / "reviews" / "first_pass.jsonl")}
    second = {row["review_id"]: row for row in _read_jsonl(run_root / "reviews" / "second_pass.jsonl")}
    answer_key = {row["review_id"]: row["source_id"] for row in _read_jsonl(run_root / "review_answer_key.jsonl")}
    predictions = {row["source_id"]: row["forecast_eligible_predicted"] for row in _read_jsonl(run_root / "engine_predictions.jsonl")}
    articles = {row["source_id"]: row for row in _read_jsonl(run_root / "sampled_articles.jsonl")}
    second_selection = json.loads((run_root / "second_pass_selection_manifest.json").read_text(encoding="utf-8"))
    second_reasons = {row["review_id"]: row["reason"] for row in second_selection["selected"]}

    final_rows: list[dict[str, Any]] = []
    table_rows: list[dict[str, Any]] = []
    for review_id in sorted(first):
        reviews = [first[review_id]]
        if review_id in second:
            reviews.append(second[review_id])
        if review_id in third:
            reviews.append(third[review_id])
        decided = [row for row in reviews if row["eligibility"] != "insufficient_context"]
        votes = Counter(row["eligibility"] for row in decided)
        if votes["eligible"] > votes["ineligible"]:
            label = "eligible"
        elif votes["ineligible"] > votes["eligible"]:
            label = "ineligible"
        else:
            label = "unresolved"
        winning = [row for row in decided if row["eligibility"] == label]
        tickers = sorted({str(ticker) for row in winning for ticker in row.get("eligible_tickers", [])}) if label == "eligible" else []
        source_id = answer_key[review_id]
        article = articles[source_id]
        final_rows.append({
            "source_id": source_id,
            "review_id": review_id,
            "screening_label": label,
            "eligible_tickers_suggested": tickers,
            "review_count": len(reviews),
            "decided_vote_counts": dict(sorted(votes.items())),
            "engine_forecast_eligible": predictions[source_id],
            "year": article["year"],
            "session_bucket": article["session_bucket"],
            "authority_status": "silver_screening_not_certified_gold",
        })
        table_rows.append({
            "source_id": source_id,
            "review_id": review_id,
            "publication_time_utc": article["source_timestamp"],
            "year": article["year"],
            "session_bucket_et": article["session_bucket"],
            "tickers": json.dumps(article.get("tickers") or [], ensure_ascii=False),
            "author": article.get("author") or "",
            "provider_domain": article.get("url_domain") or "",
            "channels": json.dumps(article.get("channels") or [], ensure_ascii=False),
            "provider_tags": json.dumps(article.get("provider_tags") or [], ensure_ascii=False),
            "title": article.get("title") or "",
            "first_substantive_sentence": article.get("first_substantive_sentence") or "",
            "forecast_eligible_v48": predictions[source_id],
            "forecast_eligible_silver_screen": label,
            "eligible_tickers_suggested": json.dumps(tickers, ensure_ascii=False),
            "review_count": len(reviews),
            "authority_status": "silver_screening_not_certified_gold",
        })

    _write_jsonl(run_root / "reviews" / "third_pass.jsonl", (third[key] for key in sorted(third)))
    _write_jsonl(run_root / "silver_screening_labels.jsonl", final_rows)
    _write_csv(run_root / "forecast_eligibility_screening_table.csv", table_rows)
    qa_ids = {key for key, reason in second_reasons.items() if reason == "deterministic_agreement_qa"}
    qa_matches = sum(first[key]["eligibility"] == second[key]["eligibility"] for key in qa_ids)
    qa_decided = sum(
        first[key]["eligibility"] != "insufficient_context" and second[key]["eligibility"] != "insufficient_context"
        for key in qa_ids
    )
    manifest = {
        "version": "news_synthesis_forecast_eligibility_silver_screening_v1",
        "articles": len(final_rows),
        "screening_counts": dict(sorted(Counter(row["screening_label"] for row in final_rows).items())),
        "by_year": _group_counts(final_rows, "year"),
        "by_session": _group_counts(final_rows, "session_bucket"),
        "engine_cross_tab": dict(sorted(Counter(
            f"human_{row['screening_label']}|engine_{row['engine_forecast_eligible']}" for row in final_rows
        ).items())),
        "qa": {
            "agreement_sample_rows": len(qa_ids),
            "same_label": qa_matches,
            "same_label_rate": qa_matches / len(qa_ids) if qa_ids else None,
            "both_decided": qa_decided,
        },
        "review_cost": dict(sorted(Counter(str(row["review_count"]) for row in final_rows).items())),
        "authority_status": "silver_screening_not_certified_gold",
        "eligible_requires_future_full_source_issuer_level_review": True,
        "labels_sha256": _sha256_json(final_rows),
        "screening_table_sha256": hashlib.sha256(
            (run_root / "forecast_eligibility_screening_table.csv").read_bytes()
        ).hexdigest(),
    }
    (run_root / "completion_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (run_root / "SUMMARY.md").write_text(_summary_markdown(manifest), encoding="utf-8")
    return manifest


def _load_blind_inputs(root: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.jsonl")):
        for row in _read_jsonl(path):
            review_id = str(row.get("review_id") or "")
            if not review_id or review_id in rows:
                raise RuntimeError(f"missing or duplicate blind review_id: {review_id}")
            rows[review_id] = row
    return rows


def _load_and_validate_reviews(
    paths: Iterable[Path],
    *,
    expected_inputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    expected_ids = set(expected_inputs)
    rows: dict[str, dict[str, Any]] = {}
    for path in paths:
        for row in _read_jsonl(path):
            review_id = str(row.get("review_id") or "")
            if review_id not in expected_ids:
                raise RuntimeError(f"unexpected review_id in {path}: {review_id}")
            if review_id in rows:
                raise RuntimeError(f"duplicate reviewed row: {review_id}")
            if row.get("eligibility") not in ELIGIBILITY_VALUES:
                raise RuntimeError(f"invalid eligibility for {review_id}: {row.get('eligibility')}")
            if row.get("confidence") not in CONFIDENCE_VALUES:
                raise RuntimeError(f"invalid confidence for {review_id}: {row.get('confidence')}")
            if not isinstance(row.get("eligible_tickers"), list):
                raise RuntimeError(f"eligible_tickers must be a list for {review_id}")
            given_tickers = {str(value).strip().upper() for value in row["eligible_tickers"] if str(value).strip()}
            allowed_tickers = {
                str(value).strip().upper()
                for value in expected_inputs[review_id].get("tickers", [])
                if str(value).strip()
            }
            if not given_tickers.issubset(allowed_tickers):
                raise RuntimeError(f"eligible_tickers contains a non-input ticker for {review_id}")
            if row.get("eligibility") != "eligible" and given_tickers:
                raise RuntimeError(f"non-eligible review has eligible_tickers for {review_id}")
            if not isinstance(row.get("reason"), str) or not row["reason"].strip():
                raise RuntimeError(f"reason is required for {review_id}")
            rows[review_id] = row
    missing = expected_ids - set(rows)
    extra = set(rows) - expected_ids
    if missing or extra:
        raise RuntimeError(f"review population mismatch: missing={len(missing)} extra={len(extra)}")
    return rows


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _write_csv(path: Path, rows: list[Mapping[str, Any]]) -> None:
    if not rows:
        raise RuntimeError("screening table cannot be empty")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _order_key(review_id: str, purpose: str) -> str:
    return hashlib.sha256(f"{purpose}|{review_id}".encode()).hexdigest()


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _group_counts(rows: Iterable[Mapping[str, Any]], field: str) -> dict[str, dict[str, int]]:
    grouped: dict[str, Counter[str]] = {}
    for row in rows:
        grouped.setdefault(str(row[field]), Counter())[str(row["screening_label"])] += 1
    return {key: dict(sorted(value.items())) for key, value in sorted(grouped.items())}


def _summary_markdown(manifest: Mapping[str, Any]) -> str:
    counts = manifest["screening_counts"]
    total = int(manifest["articles"])
    sessions = manifest["by_session"]
    qa = manifest["qa"]
    costs = manifest["review_cost"]
    return f"""# Forecast eligibility screening: 5,000 new articles

## Outcome

- Eligible silver-screen candidates: **{counts.get('eligible', 0):,}** ({counts.get('eligible', 0) / total:.2%}).
- Ineligible silver-screen candidates: **{counts.get('ineligible', 0):,}** ({counts.get('ineligible', 0) / total:.2%}).
- Unresolved after tie-breaking: **{counts.get('unresolved', 0):,}** ({counts.get('unresolved', 0) / total:.2%}).
- These labels are screening/sampling evidence only. They are **not certified gold**.

## Eligible candidates by Eastern session

| Session | Eligible | Ineligible | Unresolved |
|---|---:|---:|---:|
| Premarket | {sessions['premarket'].get('eligible', 0):,} | {sessions['premarket'].get('ineligible', 0):,} | {sessions['premarket'].get('unresolved', 0):,} |
| Regular | {sessions['regular'].get('eligible', 0):,} | {sessions['regular'].get('ineligible', 0):,} | {sessions['regular'].get('unresolved', 0):,} |
| After hours | {sessions['after_hours'].get('eligible', 0):,} | {sessions['after_hours'].get('ineligible', 0):,} | {sessions['after_hours'].get('unresolved', 0):,} |

## Review design and quality signal

- First pass: one blinded reviewer on all 5,000 articles.
- Second pass: human-engine disagreements, abstentions, and a deterministic 10% QA sample of agreements.
- Third pass: human disagreement or abstention after the second pass.
- Independent two-reviewer exact agreement on the random agreement-QA sample: **{qa['same_label_rate']:.2%}** ({qa['same_label']:,}/{qa['agreement_sample_rows']:,}).
- Review cost: {costs.get('1', 0):,} articles received one review, {costs.get('2', 0):,} received two, and {costs.get('3', 0):,} received three.
- Sentiment was intentionally not requested.

## Next decision

Use the 2,165 eligible candidates plus the 146 unresolved cases as the priority pool for full-source, issuer-level manual review. Only that later complete review may promote records into the manual-certification authority.
"""
