from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping


CONTRACT_VERSION = "news_synthesis_forecast_eligibility_gold_v1"
IDENTITY_VALUES = frozenset(("resolved_focal_issuer", "resolved_non_focal", "unresolved"))
ELIGIBILITY_VALUES = frozenset(("eligible", "ineligible", "policy_uncertain"))
SENTIMENT_VALUES = frozenset(("positive", "negative", "mixed", "not_applicable", "policy_uncertain"))
REASON_CODES = frozenset((
    "current_material_issuer_event",
    "issuer_forward_guidance",
    "directional_economic_implication",
    "neutral_or_routine_communication",
    "historical_only",
    "conditional_or_speculative_only",
    "analyst_origin",
    "non_report_or_non_news",
    "counterparty_or_other_issuer_event",
    "ticker_not_focal",
    "identity_unresolved",
    "insufficient_directional_evidence",
    "policy_ambiguous",
))


def prepare_full_source_review(
    sampling_root: Path,
    output_root: Path,
    *,
    max_articles_per_batch: int = 40,
    max_body_chars_per_batch: int = 60_000,
    seed: str = "forecast-gold-full-source-v1",
) -> dict[str, Any]:
    if max_articles_per_batch < 1 or max_body_chars_per_batch < 1:
        raise ValueError("batch limits must be positive")
    labels = {
        row["source_id"]: row
        for row in _read_jsonl(sampling_root / "silver_screening_labels.jsonl")
        if row.get("screening_label") == "eligible"
    }
    articles = {
        row["source_id"]: row
        for row in _read_jsonl(sampling_root / "sampled_articles.jsonl")
        if row.get("source_id") in labels
    }
    if set(articles) != set(labels):
        raise RuntimeError("Silver eligible labels and preserved source articles differ")
    output_root.mkdir(parents=True, exist_ok=False)

    review_rows = []
    answer_key = []
    for source_id in sorted(articles, key=lambda value: _order_key(value, seed)):
        article = articles[source_id]
        review_id = _review_id(source_id, seed)
        tickers = sorted({str(value).strip().upper() for value in article.get("tickers", []) if str(value).strip()})
        if not tickers:
            raise RuntimeError(f"Gold candidate has no provider ticker: {source_id}")
        review_rows.append({
            "review_id": review_id,
            "published_at_utc": article["source_timestamp"],
            "provider_tickers": tickers,
            "author": article.get("author") or "",
            "provider_domain": article.get("url_domain") or "",
            "channels": article.get("channels") or [],
            "provider_tags": article.get("provider_tags") or [],
            "title": article.get("title") or "",
            "full_rendered_body": article.get("text") or "",
        })
        answer_key.append({
            "review_id": review_id,
            "source_id": source_id,
            "source_timestamp": article["source_timestamp"],
            "title_sha256": _sha256_text(str(article.get("title") or "")),
            "body_sha256": _sha256_text(str(article.get("text") or "")),
        })

    batches = _bounded_batches(
        review_rows,
        max_articles=max_articles_per_batch,
        max_body_chars=max_body_chars_per_batch,
    )
    batch_root = output_root / "blind_full_source_batches"
    batch_root.mkdir()
    assignments = []
    for index, batch in enumerate(batches, 1):
        path = batch_root / f"batch_{index:03d}.jsonl"
        _write_jsonl(path, batch)
        assignments.append({
            "batch": index,
            "path": str(path),
            "articles": len(batch),
            "issuer_units": sum(len(row["provider_tickers"]) for row in batch),
            "body_chars": sum(len(row["full_rendered_body"]) for row in batch),
            "sha256": _sha256_file(path),
        })
    _write_jsonl(output_root / "review_answer_key.jsonl", answer_key)
    (output_root / "REVIEW_INSTRUCTIONS.md").write_text(_review_instructions(), encoding="utf-8")
    manifest = {
        "version": CONTRACT_VERSION,
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "awaiting_two_independent_full_source_reviews",
        "source_sampling_manifest_sha256": _sha256_file(sampling_root / "completion_manifest.json"),
        "population": {
            "articles": len(review_rows),
            "issuer_units": sum(len(row["provider_tickers"]) for row in review_rows),
            "article_ids_sha256": _sha256_json(sorted(articles)),
        },
        "review": {
            "prediction_blind": True,
            "silver_label_blind": True,
            "sentiment_required_only_when_eligible": True,
            "independent_full_source_passes": 2,
            "third_pass": "unit disagreement or policy uncertainty",
            "max_articles_per_batch": max_articles_per_batch,
            "max_body_chars_per_batch": max_body_chars_per_batch,
            "batches": len(batches),
        },
        "assignments": assignments,
    }
    (output_root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def prepare_adjudication(
    review_root: Path,
    pass_one_files: Iterable[Path],
    pass_two_files: Iterable[Path],
) -> dict[str, Any]:
    inputs = _load_inputs(review_root / "blind_full_source_batches")
    first = validate_reviews(pass_one_files, inputs)
    second = validate_reviews(pass_two_files, inputs)
    review_dir = review_root / "reviews"
    review_dir.mkdir(exist_ok=True)
    _write_jsonl(review_dir / "pass_one.jsonl", (first[key] for key in sorted(first)))
    _write_jsonl(review_dir / "pass_two.jsonl", (second[key] for key in sorted(second)))

    selected = []
    reason_counts: Counter[str] = Counter()
    unit_disagreements = 0
    for review_id in sorted(inputs):
        first_units = _unit_map(first[review_id])
        second_units = _unit_map(second[review_id])
        reasons = []
        for ticker in sorted(first_units):
            left, right = _decision_tuple(first_units[ticker]), _decision_tuple(second_units[ticker])
            if "policy_uncertain" in left or "policy_uncertain" in right:
                reasons.append(f"policy_uncertain:{ticker}")
                unit_disagreements += 1
            elif left != right:
                reasons.append(f"unit_disagreement:{ticker}")
                unit_disagreements += 1
        if reasons:
            selected.append(inputs[review_id])
            for reason in reasons:
                reason_counts[reason.split(":", 1)[0]] += 1

    batches = _bounded_batches(selected, max_articles=40, max_body_chars=60_000)
    batch_root = review_root / "blind_adjudication_batches"
    batch_root.mkdir(exist_ok=False)
    assignments = []
    for index, batch in enumerate(batches, 1):
        path = batch_root / f"batch_{index:03d}.jsonl"
        _write_jsonl(path, batch)
        assignments.append({"batch": index, "path": str(path), "articles": len(batch), "sha256": _sha256_file(path)})
    manifest = {
        "version": f"{CONTRACT_VERSION}_adjudication_v1",
        "articles_reviewed_twice": len(inputs),
        "articles_requiring_third_review": len(selected),
        "unit_disagreements_or_uncertain": unit_disagreements,
        "selection_reasons": dict(sorted(reason_counts.items())),
        "batches": len(batches),
        "selected_review_ids_sha256": _sha256_json(sorted(row["review_id"] for row in selected)),
        "assignments": assignments,
    }
    (review_root / "adjudication_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def certify_consensus(review_root: Path, third_pass_files: Iterable[Path]) -> dict[str, Any]:
    inputs = _load_inputs(review_root / "blind_full_source_batches")
    adjudication_inputs = _load_inputs(review_root / "blind_adjudication_batches")
    third = validate_reviews(third_pass_files, adjudication_inputs) if adjudication_inputs else {}
    first = {row["review_id"]: row for row in _read_jsonl(review_root / "reviews" / "pass_one.jsonl")}
    second = {row["review_id"]: row for row in _read_jsonl(review_root / "reviews" / "pass_two.jsonl")}
    answer_key = {row["review_id"]: row for row in _read_jsonl(review_root / "review_answer_key.jsonl")}
    _write_jsonl(review_root / "reviews" / "pass_three.jsonl", (third[key] for key in sorted(third)))

    certified_root = review_root / "certified_labels"
    uncertain_root = review_root / "policy_uncertain"
    certified_root.mkdir(exist_ok=True)
    uncertain_root.mkdir(exist_ok=True)
    ledger = []
    article_eligibility_counts: Counter[str] = Counter()
    eligibility_counts: Counter[str] = Counter()
    sentiment_counts: Counter[str] = Counter()
    uncertain_units = 0
    quarantined_issuer_units = 0
    for review_id in sorted(inputs):
        source = inputs[review_id]
        reviews = [first[review_id], second[review_id]]
        if review_id in third:
            reviews.append(third[review_id])
        resolved_units = []
        article_uncertain = False
        for ticker in source["provider_tickers"]:
            units = [_unit_map(review)[ticker] for review in reviews]
            decisions = Counter(_decision_tuple(unit) for unit in units)
            decision, votes = decisions.most_common(1)[0]
            needed = 2 if len(reviews) == 3 else len(reviews)
            if votes < needed or "policy_uncertain" in decision:
                article_uncertain = True
                uncertain_units += 1
                resolved_units.append({
                    "ticker": ticker,
                    "gold_status": "policy_uncertain",
                    "review_decisions": [_decision_record(unit) for unit in units],
                })
                continue
            matching = [unit for unit in units if _decision_tuple(unit) == decision]
            evidence = _dedupe_evidence(span for unit in matching for span in unit["evidence"])
            reason_codes = sorted({code for unit in matching for code in unit["reason_codes"]})
            identity, eligibility, sentiment = decision
            resolved_units.append({
                "ticker": ticker,
                "gold_status": "certified_consensus",
                "identity_status": identity,
                "forecast_eligibility": eligibility,
                "sentiment": sentiment,
                "evidence": evidence,
                "reason_codes": reason_codes,
                "review_votes": votes,
                "review_count": len(reviews),
            })
        if article_uncertain:
            quarantined_issuer_units += len(resolved_units)
        else:
            for unit in resolved_units:
                eligibility_counts[str(unit["forecast_eligibility"])] += 1
                sentiment_counts[str(unit["sentiment"])] += 1
        document = {
            "contract_version": CONTRACT_VERSION,
            "review_id": review_id,
            "source_id": answer_key[review_id]["source_id"],
            "source_timestamp": answer_key[review_id]["source_timestamp"],
            "title_sha256": answer_key[review_id]["title_sha256"],
            "body_sha256": answer_key[review_id]["body_sha256"],
            "article_forecast_eligible": any(
                unit.get("forecast_eligibility") == "eligible" for unit in resolved_units
            ) if not article_uncertain else None,
            "issuer_units": resolved_units,
            "certification": {
                "status": "policy_uncertain" if article_uncertain else "certified",
                "method": "two_independent_full_source_reviews_plus_blind_third_on_disagreement",
                "prediction_blind": True,
                "silver_label_blind": True,
                "certified_at_utc": datetime.now(UTC).isoformat(),
            },
        }
        target_root = uncertain_root if article_uncertain else certified_root
        target = target_root / f"{answer_key[review_id]['source_id']}.json"
        target.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        if not article_uncertain:
            article_eligibility_counts[
                "eligible" if document["article_forecast_eligible"] else "ineligible"
            ] += 1
        ledger.append({
            "review_id": review_id,
            "source_id": answer_key[review_id]["source_id"],
            "status": "policy_uncertain" if article_uncertain else "certified",
            "issuer_units": len(resolved_units),
            "label_sha256": _sha256_file(target),
        })

    _write_jsonl(review_root / "certification_ledger.jsonl", ledger)
    certified = sum(row["status"] == "certified" for row in ledger)
    uncertain = len(ledger) - certified
    reviewed_issuer_units = sum(row["issuer_units"] for row in ledger)
    manifest = {
        "version": CONTRACT_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "status": "complete",
        "population": {
            "reviewed_articles": len(ledger),
            "certified_articles": certified,
            "policy_uncertain_articles": uncertain,
            "issuer_units": reviewed_issuer_units,
            "certified_issuer_units": reviewed_issuer_units - quarantined_issuer_units,
            "quarantined_issuer_units": quarantined_issuer_units,
            "decision_policy_uncertain_units": uncertain_units,
        },
        "article_eligibility_distribution": dict(sorted(article_eligibility_counts.items())),
        "eligibility_distribution": dict(sorted(eligibility_counts.items())),
        "sentiment_distribution": dict(sorted(sentiment_counts.items())),
        "authority": {
            "ledger_sha256": _sha256_file(review_root / "certification_ledger.jsonl"),
            "pass_one_sha256": _sha256_file(review_root / "reviews" / "pass_one.jsonl"),
            "pass_two_sha256": _sha256_file(review_root / "reviews" / "pass_two.jsonl"),
            "pass_three_sha256": _sha256_file(review_root / "reviews" / "pass_three.jsonl"),
            "certified_set_sha256": _sha256_json(ledger),
        },
    }
    (review_root / "gold_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (review_root / "SUMMARY.md").write_text(_summary(manifest), encoding="utf-8")
    return manifest


def validate_certified_authority(review_root: Path) -> dict[str, Any]:
    manifest = json.loads((review_root / "gold_manifest.json").read_text(encoding="utf-8"))
    ledger = _read_jsonl(review_root / "certification_ledger.jsonl")
    inputs = _load_inputs(review_root / "blind_full_source_batches")
    answer_key = {row["review_id"]: row for row in _read_jsonl(review_root / "review_answer_key.jsonl")}
    if len(ledger) != len(inputs) or set(inputs) != set(answer_key):
        raise RuntimeError("authority population mismatch")
    if len({row["review_id"] for row in ledger}) != len(ledger):
        raise RuntimeError("duplicate review_id in certification ledger")
    if len({row["source_id"] for row in ledger}) != len(ledger):
        raise RuntimeError("duplicate source_id in certification ledger")

    first = {row["review_id"]: row for row in _read_jsonl(review_root / "reviews" / "pass_one.jsonl")}
    second = {row["review_id"]: row for row in _read_jsonl(review_root / "reviews" / "pass_two.jsonl")}
    if set(first) != set(inputs) or set(second) != set(inputs):
        raise RuntimeError("review pass population mismatch")
    agreement = Counter()
    exact_articles = 0
    for review_id in sorted(inputs):
        left = _unit_map(first[review_id])
        right = _unit_map(second[review_id])
        article_exact = True
        for ticker in sorted(left):
            first_decision = _decision_tuple(left[ticker])
            second_decision = _decision_tuple(right[ticker])
            agreement["issuer_units"] += 1
            agreement["exact"] += first_decision == second_decision
            agreement["identity"] += first_decision[0] == second_decision[0]
            agreement["eligibility"] += first_decision[1] == second_decision[1]
            agreement["sentiment"] += first_decision[2] == second_decision[2]
            article_exact = article_exact and first_decision == second_decision
            article_exact = article_exact and "policy_uncertain" not in first_decision
            article_exact = article_exact and "policy_uncertain" not in second_decision
        exact_articles += article_exact

    article_counts: Counter[str] = Counter()
    eligibility_counts: Counter[str] = Counter()
    sentiment_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    reviewed_units = certified_units = quarantined_units = evidence_spans = 0
    for row in ledger:
        review_id = str(row["review_id"])
        source_id = str(row["source_id"])
        if review_id not in inputs or source_id != answer_key[review_id]["source_id"]:
            raise RuntimeError(f"ledger source binding mismatch: {review_id}")
        status = str(row["status"])
        if status not in {"certified", "policy_uncertain"}:
            raise RuntimeError(f"invalid certification status: {review_id}")
        folder = "certified_labels" if status == "certified" else "policy_uncertain"
        path = review_root / folder / f"{source_id}.json"
        if not path.is_file() or _sha256_file(path) != row["label_sha256"]:
            raise RuntimeError(f"label file hash mismatch: {review_id}")
        document = json.loads(path.read_text(encoding="utf-8"))
        authority = answer_key[review_id]
        if document.get("title_sha256") != authority["title_sha256"] or document.get("body_sha256") != authority["body_sha256"]:
            raise RuntimeError(f"label source hash mismatch: {review_id}")
        units = document.get("issuer_units")
        if not isinstance(units, list) or len(units) != row["issuer_units"]:
            raise RuntimeError(f"label issuer-unit count mismatch: {review_id}")
        reviewed_units += len(units)
        status_counts[status] += 1
        source = inputs[review_id]
        for unit in units:
            for span in unit.get("evidence", []):
                if span.get("source_field") not in {"title", "full_rendered_body"} or span.get("quote") not in str(source[span["source_field"]]):
                    raise RuntimeError(f"certified evidence mismatch: {review_id}:{unit.get('ticker')}")
                evidence_spans += 1
        if status == "certified":
            certified_units += len(units)
            article_counts["eligible" if document["article_forecast_eligible"] else "ineligible"] += 1
            for unit in units:
                eligibility_counts[str(unit["forecast_eligibility"])] += 1
                sentiment_counts[str(unit["sentiment"])] += 1
        else:
            quarantined_units += len(units)

    expected_hashes = {
        "ledger_sha256": review_root / "certification_ledger.jsonl",
        "pass_one_sha256": review_root / "reviews" / "pass_one.jsonl",
        "pass_two_sha256": review_root / "reviews" / "pass_two.jsonl",
        "pass_three_sha256": review_root / "reviews" / "pass_three.jsonl",
    }
    for key, path in expected_hashes.items():
        if _sha256_file(path) != manifest["authority"][key]:
            raise RuntimeError(f"authority hash mismatch: {key}")
    if _sha256_json(ledger) != manifest["authority"]["certified_set_sha256"]:
        raise RuntimeError("certified set hash mismatch")
    if dict(sorted(article_counts.items())) != manifest["article_eligibility_distribution"]:
        raise RuntimeError("article eligibility distribution mismatch")
    if dict(sorted(eligibility_counts.items())) != manifest["eligibility_distribution"]:
        raise RuntimeError("issuer eligibility distribution mismatch")
    if dict(sorted(sentiment_counts.items())) != manifest["sentiment_distribution"]:
        raise RuntimeError("sentiment distribution mismatch")
    population = manifest["population"]
    if (reviewed_units, certified_units, quarantined_units) != (
        population["issuer_units"],
        population["certified_issuer_units"],
        population["quarantined_issuer_units"],
    ):
        raise RuntimeError("issuer-unit population summary mismatch")

    report = {
        "version": f"{CONTRACT_VERSION}_validation_v1",
        "validated_at_utc": datetime.now(UTC).isoformat(),
        "status": "pass",
        "gold_manifest_sha256": _sha256_file(review_root / "gold_manifest.json"),
        "articles": len(ledger),
        "issuer_units": reviewed_units,
        "certified_issuer_units": certified_units,
        "quarantined_issuer_units": quarantined_units,
        "evidence_spans_verified": evidence_spans,
        "certification_status": dict(sorted(status_counts.items())),
        "article_eligibility_distribution": dict(sorted(article_counts.items())),
        "independent_review_agreement": {
            "exact_articles": exact_articles,
            "exact_article_rate": exact_articles / len(inputs),
            "exact_issuer_units": agreement["exact"],
            "exact_issuer_unit_rate": agreement["exact"] / agreement["issuer_units"],
            "identity_rate": agreement["identity"] / agreement["issuer_units"],
            "eligibility_rate": agreement["eligibility"] / agreement["issuer_units"],
            "sentiment_rate": agreement["sentiment"] / agreement["issuer_units"],
        },
        "authority_hashes_verified": True,
    }
    (review_root / "VALIDATION.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def validate_reviews(
    paths: Iterable[Path],
    inputs: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in paths:
        for row in _read_jsonl(path):
            review_id = str(row.get("review_id") or "")
            if review_id not in inputs or review_id in rows:
                raise RuntimeError(f"unexpected or duplicate review_id: {review_id}")
            source = inputs[review_id]
            units = row.get("issuer_units")
            if not isinstance(units, list):
                raise RuntimeError(f"issuer_units must be a list: {review_id}")
            tickers = [str(unit.get("ticker") or "").strip().upper() for unit in units]
            if len(tickers) != len(set(tickers)) or set(tickers) != set(source["provider_tickers"]):
                raise RuntimeError(f"review ticker coverage mismatch: {review_id}")
            for unit in units:
                _validate_unit(review_id, unit, source)
            if not isinstance(row.get("article_reason"), str) or not row["article_reason"].strip():
                raise RuntimeError(f"article_reason required: {review_id}")
            rows[review_id] = row
    if set(rows) != set(inputs):
        raise RuntimeError(f"review population mismatch: rows={len(rows)} expected={len(inputs)}")
    return rows


def _validate_unit(review_id: str, unit: Mapping[str, Any], source: Mapping[str, Any]) -> None:
    ticker = str(unit.get("ticker") or "").strip().upper()
    identity = unit.get("identity_status")
    eligibility = unit.get("forecast_eligibility")
    sentiment = unit.get("sentiment")
    if identity not in IDENTITY_VALUES or eligibility not in ELIGIBILITY_VALUES or sentiment not in SENTIMENT_VALUES:
        raise RuntimeError(f"invalid unit decision: {review_id}:{ticker}")
    if eligibility == "eligible" and sentiment not in {"positive", "negative", "mixed"}:
        raise RuntimeError(f"eligible unit requires directional sentiment: {review_id}:{ticker}")
    if eligibility == "eligible" and identity != "resolved_focal_issuer":
        raise RuntimeError(f"eligible unit requires resolved focal identity: {review_id}:{ticker}")
    if eligibility == "ineligible" and sentiment != "not_applicable":
        raise RuntimeError(f"ineligible unit requires not_applicable sentiment: {review_id}:{ticker}")
    if eligibility == "policy_uncertain" and sentiment != "policy_uncertain":
        raise RuntimeError(f"uncertain unit requires uncertain sentiment: {review_id}:{ticker}")
    if not isinstance(unit.get("reason"), str) or not str(unit["reason"]).strip():
        raise RuntimeError(f"unit reason required: {review_id}:{ticker}")
    codes = unit.get("reason_codes")
    if not isinstance(codes, list) or not codes or any(code not in REASON_CODES for code in codes):
        raise RuntimeError(f"invalid reason_codes: {review_id}:{ticker}")
    evidence = unit.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise RuntimeError(f"evidence required: {review_id}:{ticker}")
    for span in evidence:
        field = span.get("source_field")
        quote = span.get("quote")
        if field not in {"title", "full_rendered_body"} or not isinstance(quote, str) or not quote.strip():
            raise RuntimeError(f"invalid evidence: {review_id}:{ticker}")
        if quote not in str(source[field]):
            raise RuntimeError(f"evidence quote not found: {review_id}:{ticker}")


def _bounded_batches(rows: list[dict[str, Any]], *, max_articles: int, max_body_chars: int) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    chars = 0
    for row in rows:
        row_chars = len(str(row["full_rendered_body"]))
        if current and (len(current) >= max_articles or chars + row_chars > max_body_chars):
            batches.append(current)
            current, chars = [], 0
        current.append(row)
        chars += row_chars
    if current:
        batches.append(current)
    return batches


def _load_inputs(root: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for path in sorted(root.glob("*.jsonl")):
        for row in _read_jsonl(path):
            review_id = str(row["review_id"])
            if review_id in rows:
                raise RuntimeError(f"duplicate input review_id: {review_id}")
            rows[review_id] = row
    return rows


def _unit_map(review: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(unit["ticker"]).upper(): dict(unit) for unit in review["issuer_units"]}


def _decision_tuple(unit: Mapping[str, Any]) -> tuple[str, str, str]:
    return str(unit["identity_status"]), str(unit["forecast_eligibility"]), str(unit["sentiment"])


def _decision_record(unit: Mapping[str, Any]) -> dict[str, str]:
    identity, eligibility, sentiment = _decision_tuple(unit)
    return {"identity_status": identity, "forecast_eligibility": eligibility, "sentiment": sentiment}


def _dedupe_evidence(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, str]]:
    values = {(str(row["source_field"]), str(row["quote"])) for row in rows}
    return [{"source_field": field, "quote": quote} for field, quote in sorted(values)]


def _review_id(source_id: str, seed: str) -> str:
    return "G" + hashlib.sha256(f"{seed}|{source_id}".encode()).hexdigest()[:20]


def _order_key(value: str, seed: str) -> str:
    return hashlib.sha256(f"{seed}|order|{value}".encode()).hexdigest()


def _review_instructions() -> str:
    reasons = ", ".join(sorted(REASON_CODES))
    return f"""# Full-source forecast eligibility gold review

Review every article independently using the complete rendered body. Prior silver labels and News Synthesis predictions are hidden.

For every provider ticker, decide issuer identity/binding, forecast eligibility, and—only when eligible—overall directional sentiment. Eligibility requires a resolved focal tradable issuer; substantive supported evidence; a current issuer event or issuer forward guidance; positive or negative economic implication; report/news purpose; and non-analyst origin. Neutral routine communications, historical-only material, analyst opinions, unresolved identity, counterparty-only events, or evidence without directional implication are ineligible. Company announcements through a news service may qualify when substantive and directional.

Sentiment is the overall issuer economic implication: `positive`, `negative`, or `mixed`. Use `mixed` only when material positive and negative implications genuinely counterbalance; choose the dominant direction otherwise.

Return one JSON object per article with exactly:

- `review_id`
- `issuer_units`: one object for every provider ticker, with `ticker`, `identity_status`, `forecast_eligibility`, `sentiment`, `evidence`, `reason_codes`, and `reason`
- `article_reason`: concise overall explanation

Allowed `identity_status`: {', '.join(sorted(IDENTITY_VALUES))}.
Allowed `forecast_eligibility`: {', '.join(sorted(ELIGIBILITY_VALUES))}.
Allowed `sentiment`: {', '.join(sorted(SENTIMENT_VALUES))}.
Allowed `reason_codes`: {reasons}.

Every unit needs at least one exact verbatim evidence object: `{{"source_field":"title"|"full_rendered_body","quote":"exact substring"}}`. Do not infer missing facts. For `ineligible`, sentiment must be `not_applicable`; for `policy_uncertain`, sentiment must be `policy_uncertain`. Output JSONL only, with no markdown or commentary.
"""


def _summary(manifest: Mapping[str, Any]) -> str:
    population = manifest["population"]
    return f"""# Full-source forecast eligibility gold v1

- Reviewed articles: {population['reviewed_articles']:,}
- Certified articles: {population['certified_articles']:,}
- Policy-uncertain articles: {population['policy_uncertain_articles']:,}
- Reviewed issuer units: {population['issuer_units']:,}
- Certified issuer units: {population['certified_issuer_units']:,}
- Quarantined issuer units: {population['quarantined_issuer_units']:,}
- Decision-level policy-uncertain units: {population['decision_policy_uncertain_units']:,}
- Certified eligible articles: {manifest['article_eligibility_distribution'].get('eligible', 0):,}
- Certified ineligible articles: {manifest['article_eligibility_distribution'].get('ineligible', 0):,}
- Method: two independent full-source reviews, with a blind third review on disagreement or uncertainty.
- Predictions and silver labels were hidden from every reviewer.
"""


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
