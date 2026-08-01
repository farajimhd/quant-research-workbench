from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Mapping

from .schema import ANNOTATION_VERSION_V3, stable_json_hash, validate_annotation
from .storage import (
    annotation_directory,
    assert_runtime_root,
    materialize_evidence_spans,
    read_json,
    refresh_annotation_state,
    unique_preferred_match,
    write_json_atomic,
)


EXCHANGE_TICKER_RE = re.compile(
    r"\b(?:NASDAQ|NYSE|NYSEMKT|AMEX|OTC(?:QB|QX|MKTS)?|TSX|CSE)\s*:\s*([A-Z][A-Z0-9.\-]{0,9})\b",
    re.I,
)
ANALYST_RE = re.compile(
    r"\b(?:analysts?|brokerage|research)\b.{0,180}\b(?:initiated|maintained|reiterated|upgraded|downgraded|rating|price target)\b",
    re.I,
)
PRICE_MOVE_RE = re.compile(
    r"\b(?:shares?|stock)?\s*(?:jumped|rose|gained|surged|fell|declined|dropped|lost|closed|trading)\b",
    re.I,
)
CAUSAL_RE = re.compile(
    r"\b(?:after|following|because|announced|reported|posted|issued|received|secured|signed|entered|acquired|"
    r"priced|approved|rejected|filed|launched|raised|lowered|guidance|offering|agreement|results?|earnings|"
    r"trial|endpoint|patent|contract|merger|acquisition|investigation|lawsuit|bankruptcy|delisting)\b",
    re.I,
)


def prepare_coverage_review(root: Path) -> dict[str, Any]:
    """Inventory every supplied or text-explicit ticker without semantic judgment."""
    assert_runtime_root(root)
    manifest = read_json(root / "sample_manifest.json")
    queue_root = root / "coverage_review_v3" / "queue"
    unresolved = 0
    articles = 0
    for row in manifest["items"]:
        sample_id = str(row["sample_id"])
        item = read_json(root / "blinded_articles" / f"{sample_id}.json")
        annotation = read_json(root / "annotations_v2" / f"{sample_id}.json")
        package = build_review_package(
            item,
            annotation,
            candidate_prediction=_load_v7_prediction(root, sample_id),
        )
        unresolved += len(package["manual_review_tickers"])
        articles += int(bool(package["manual_review_tickers"]))
        write_json_atomic(queue_root / f"{sample_id}.json", package)
    summary = {
        "version": "news_semantic_coverage_review_v3",
        "sample_count": len(manifest["items"]),
        "articles_requiring_dispositions": articles,
        "manual_review_tickers": unresolved,
    }
    summary["summary_sha256"] = stable_json_hash(summary)
    write_json_atomic(root / "coverage_review_v3" / "queue_summary.json", summary)
    return summary


def finalize_completed_reviews(root: Path) -> dict[str, Any]:
    """Persist reviewed V3 records; missing decisions remain explicitly pending."""
    assert_runtime_root(root)
    manifest = read_json(root / "sample_manifest.json")
    decisions_root = root / "coverage_review_v3" / "decisions"
    target_root = annotation_directory(root, ANNOTATION_VERSION_V3)
    completed = 0
    pending: list[str] = []
    failures: list[dict[str, str]] = []
    for row in manifest["items"]:
        sample_id = str(row["sample_id"])
        decision_path = decisions_root / f"{sample_id}.json"
        if not decision_path.exists():
            pending.append(sample_id)
            continue
        try:
            item = read_json(root / "blinded_articles" / f"{sample_id}.json")
            annotation = read_json(root / "annotations_v2" / f"{sample_id}.json")
            decisions = read_json(decision_path)
            record = finalize_v3_annotation(item, annotation, decisions)
            write_json_atomic(target_root / f"{sample_id}.json", record)
            completed += 1
        except Exception as exc:  # preserve every failed identity for operator repair
            failures.append({"sample_id": sample_id, "error": str(exc)})
    state = refresh_annotation_state(root, annotation_version=ANNOTATION_VERSION_V3)
    result = {
        "version": "news_semantic_coverage_review_v3_finalize",
        "completed_this_pass": completed,
        "pending_decisions": pending,
        "failures": failures,
        "annotation_state": state,
    }
    result["result_sha256"] = stable_json_hash(result)
    write_json_atomic(root / "coverage_review_v3" / "finalize_state.json", result)
    return result


def record_review_decisions(
    root: Path,
    *,
    sample_id: str,
    reviewed_dispositions: Mapping[str, str],
    reviewer: str,
    review_notes: str,
    added_issuer_units: list[Mapping[str, Any]] | None = None,
    replaced_issuer_units: list[Mapping[str, Any]] | None = None,
    removed_issuer_units: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Record one explicit review; only non-judgment defaults are filled."""
    assert_runtime_root(root)
    item = read_json(root / "blinded_articles" / f"{sample_id}.json")
    annotation = read_json(root / "annotations_v2" / f"{sample_id}.json")
    package = build_review_package(item, annotation)
    supplied = {str(key).upper(): str(value) for key, value in reviewed_dispositions.items()}
    unknown = sorted(set(supplied) - set(package["candidate_tickers"]))
    if unknown:
        raise ValueError(f"review contains unknown candidates: {unknown}")
    dispositions: list[dict[str, Any]] = []
    unresolved: list[str] = []
    for ticker in package["candidate_tickers"]:
        suggestion = package["ticker_suggestions"][ticker]
        if ticker in supplied:
            disposition = supplied[ticker]
            basis = "manual_review"
        elif not suggestion["requires_manual_review"]:
            disposition = str(suggestion["disposition"])
            basis = "non_judgment_structural_default"
        else:
            unresolved.append(ticker)
            continue
        evidence_quotes = package["ticker_evidence"].get(ticker, [])
        if disposition in {"labeled_issuer_unit", "metadata_only"}:
            # Existing units already own exact evidence spans; metadata-only
            # candidates have no semantic evidence by definition.
            evidence_quotes = []
        dispositions.append(
            {
                "ticker": ticker,
                "disposition": disposition,
                "annotation_confidence": 4 if basis == "manual_review" else 3,
                "rationale": (
                    f"{basis}: reviewer assigned {disposition.replace('_', ' ')}."
                ),
                "evidence_quotes": evidence_quotes,
                "evidence_spans": [],
                "review_basis": basis,
            }
        )
    if unresolved:
        raise ValueError(f"manual dispositions remain unresolved: {unresolved}")
    decisions = {
        "review_version": "news_semantic_coverage_review_v3",
        "sample_id": sample_id,
        "source_id": item["source_id"],
        "source_text_sha256": item["source_text_sha256"],
        "reviewer": reviewer,
        "review_notes": review_notes,
        "ticker_dispositions": dispositions,
        "added_issuer_units": copy.deepcopy(added_issuer_units or []),
        "replaced_issuer_units": _normalize_unit_corrections(
            annotation,
            replaced_issuer_units or [],
            operation="replace",
        ),
        "removed_issuer_units": _normalize_unit_corrections(
            annotation,
            removed_issuer_units or [],
            operation="remove",
        ),
    }
    decisions["decision_sha256"] = stable_json_hash(decisions)
    write_json_atomic(
        root / "coverage_review_v3" / "decisions" / f"{sample_id}.json",
        decisions,
    )
    return decisions


def initialize_structurally_complete_decisions(root: Path) -> dict[str, Any]:
    """Persist only reviews whose candidates need no semantic judgment."""
    assert_runtime_root(root)
    manifest = read_json(root / "sample_manifest.json")
    created = 0
    existing = 0
    pending_manual = 0
    for row in manifest["items"]:
        sample_id = str(row["sample_id"])
        target = root / "coverage_review_v3" / "decisions" / f"{sample_id}.json"
        existed_before = target.exists()
        queue = read_json(root / "coverage_review_v3" / "queue" / f"{sample_id}.json")
        if queue["manual_review_tickers"]:
            pending_manual += 1
            if target.exists():
                existing += 1
            continue
        record_review_decisions(
            root,
            sample_id=sample_id,
            reviewed_dispositions={},
            reviewer="coverage_structural_authority_v3",
            review_notes=(
                "No unresolved semantic passage: every candidate was an existing "
                "reviewed unit, source metadata without textual evidence, or an "
                "unambiguous observed-price-only mention."
            ),
        )
        if existed_before:
            existing += 1
        else:
            created += 1
    return {
        "created": created,
        "already_present": existing,
        "pending_manual_articles": pending_manual,
    }


def amend_review_decision(
    root: Path,
    *,
    sample_id: str,
    disposition_updates: Mapping[str, str] | None = None,
    clear_disposition_evidence_tickers: list[str] | None = None,
    added_issuer_units: list[Mapping[str, Any]] | None = None,
    remove_added_unit_tickers: list[str] | None = None,
    remove_source_unit_indices: list[int] | None = None,
    notes_append: str,
) -> dict[str, Any]:
    """Amend one persisted decision while retaining an auditable V2 binding."""
    assert_runtime_root(root)
    decision_path = root / "coverage_review_v3" / "decisions" / f"{sample_id}.json"
    decision = read_json(decision_path)
    annotation = read_json(root / "annotations_v2" / f"{sample_id}.json")
    by_ticker = {
        str(value.get("ticker") or "").upper(): value
        for value in decision.get("ticker_dispositions") or []
    }
    for ticker, disposition in (disposition_updates or {}).items():
        key = str(ticker).upper()
        if key not in by_ticker:
            raise ValueError(f"unknown disposition ticker {key}")
        by_ticker[key]["disposition"] = str(disposition)
        by_ticker[key]["rationale"] = "manual_correction: " + notes_append
        by_ticker[key]["review_basis"] = "manual_correction"
        by_ticker[key]["annotation_confidence"] = 4
    for ticker in clear_disposition_evidence_tickers or []:
        key = str(ticker).upper()
        if key not in by_ticker:
            raise ValueError(f"unknown disposition ticker {key}")
        by_ticker[key]["evidence_quotes"] = []
        by_ticker[key]["evidence_spans"] = []
    decision["ticker_dispositions"] = list(by_ticker.values())
    remove_added = {str(value).upper() for value in remove_added_unit_tickers or []}
    retained_added = [
        value
        for value in decision.get("added_issuer_units") or []
        if str(value.get("ticker") or "").upper() not in remove_added
    ]
    retained_added.extend(copy.deepcopy(added_issuer_units or []))
    decision["added_issuer_units"] = retained_added
    removals = list(decision.get("removed_issuer_units") or [])
    existing_removed = {int(value["source_unit_index"]) for value in removals}
    for index in remove_source_unit_indices or []:
        if index in existing_removed:
            continue
        removals.extend(
            _normalize_unit_corrections(
                annotation,
                [{"source_unit_index": index, "rationale": notes_append}],
                operation="remove",
            )
        )
    decision["removed_issuer_units"] = removals
    decision.setdefault("replaced_issuer_units", [])
    previous_notes = str(decision.get("review_notes") or "").strip()
    decision["review_notes"] = " ".join(
        value for value in (previous_notes, notes_append.strip()) if value
    )
    decision.pop("decision_sha256", None)
    decision["decision_sha256"] = stable_json_hash(decision)
    write_json_atomic(decision_path, decision)
    return decision


def repair_review_evidence_from_queue(root: Path, *, sample_id: str) -> dict[str, Any]:
    """Repair only broken provenance quotes using exact immutable queue evidence."""
    assert_runtime_root(root)
    decision_path = root / "coverage_review_v3" / "decisions" / f"{sample_id}.json"
    decision = read_json(decision_path)
    item = read_json(root / "blinded_articles" / f"{sample_id}.json")
    package = read_json(root / "coverage_review_v3" / "queue" / f"{sample_id}.json")
    publication = item.get("publication") or {}
    rendered = item.get("rendered_product") or {}
    sources: list[tuple[str, int | None, str]] = [
        ("title", None, str(publication.get("title") or "")),
        ("teaser", None, str(publication.get("teaser") or "")),
        ("rendered_text", None, str(rendered.get("text") or "")),
    ]
    sources.extend(
        (
            "source_lane",
            int(lane.get("source_ordinal") or 0),
            str(lane.get("text") or ""),
        )
        for lane in item.get("source_lanes") or ()
    )
    repaired: list[dict[str, Any]] = []
    collections = (
        ("added_issuer_unit", decision.get("added_issuer_units") or []),
        ("ticker_disposition", decision.get("ticker_dispositions") or []),
    )
    queue_evidence = package.get("ticker_evidence") or {}
    for kind, values in collections:
        for value in values:
            quotes = [str(quote) for quote in value.get("evidence_quotes") or []]
            if all(unique_preferred_match(quote, sources) is not None for quote in quotes):
                continue
            ticker = str(value.get("ticker") or "").upper()
            candidates = [
                str(quote)
                for quote in queue_evidence.get(ticker, [])
                if unique_preferred_match(str(quote), sources) is not None
            ]
            if not candidates:
                raise ValueError(f"no exact queue evidence can repair {sample_id} {ticker}")
            value["evidence_quotes"] = candidates
            value["evidence_spans"] = []
            repaired.append({"kind": kind, "ticker": ticker, "quotes": candidates})
    decision.setdefault("replaced_issuer_units", [])
    decision.setdefault("removed_issuer_units", [])
    decision.pop("decision_sha256", None)
    decision["decision_sha256"] = stable_json_hash(decision)
    write_json_atomic(decision_path, decision)
    return {"sample_id": sample_id, "repaired": repaired}


def build_review_package(
    item: Mapping[str, Any],
    annotation: Mapping[str, Any],
    *,
    candidate_prediction: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    publication = item.get("publication") or {}
    text = str((item.get("rendered_product") or {}).get("text") or "")
    existing = {str(unit.get("ticker") or "").upper() for unit in annotation.get("issuer_units") or ()}
    supplied = {str(value).upper() for value in publication.get("provider_tickers") or () if value}
    identity_rows = item.get("point_in_time_issuer_candidates") or ()
    explicit = {match.group(1).upper() for match in EXCHANGE_TICKER_RE.finditer(text)}
    # Resolver candidates based only on issuer aliases can be false positives
    # (for example GAP from the ordinary word "gap"). They may enrich evidence
    # for a source-supported ticker, but may not create a gold-standard issuer.
    candidates = sorted((existing | supplied | explicit) - {""})
    aliases = _identity_aliases(identity_rows)
    evidence = {
        ticker: _ticker_passages(text, ticker, aliases.get(ticker, ()))
        for ticker in candidates
    }
    suggestions = {
        ticker: _suggest_disposition(evidence[ticker], ticker in existing)
        for ticker in candidates
    }
    v7_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for label in (candidate_prediction or {}).get("labels") or ():
        ticker = str(label.get("ticker") or "").upper()
        if not ticker:
            continue
        classification = label.get("classification") or {}
        v7_by_ticker.setdefault(ticker, []).append(
            {
                "unit_role": label.get("unit_role"),
                "issuer_role": label.get("issuer_role"),
                "evidence_scope": label.get("evidence_scope"),
                "semantic_evidence_text": label.get("semantic_evidence_text"),
                "event_concepts": classification.get("event_concepts") or [],
                "semantic_direction": classification.get("semantic_direction"),
                "forecast_trigger_eligible": label.get("forecast_trigger_eligible"),
                "reaction_evaluation_eligible": label.get("reaction_evaluation_eligible"),
                "issuer_history_context_eligible": label.get(
                    "issuer_history_context_eligible"
                ),
            }
        )
    return {
        "review_version": "news_semantic_coverage_review_v3",
        "sample_id": item["sample_id"],
        "source_id": item["source_id"],
        "source_text_sha256": item["source_text_sha256"],
        "title": publication.get("title") or "",
        "content_role": annotation.get("content_role"),
        "existing_labeled_tickers": sorted(existing),
        "candidate_tickers": candidates,
        "ticker_suggestions": suggestions,
        "manual_review_tickers": sorted(
            ticker for ticker, value in suggestions.items() if value["requires_manual_review"]
        ),
        "ticker_evidence": evidence,
        "v7_candidate_labels": v7_by_ticker,
    }


def finalize_v3_annotation(
    item: Mapping[str, Any],
    annotation_v2: Mapping[str, Any],
    decisions: Mapping[str, Any],
) -> dict[str, Any]:
    """Create V3 only after every candidate has an explicit reviewed disposition."""
    record = copy.deepcopy(dict(annotation_v2))
    record.pop("annotation_sha256", None)
    record["annotation_version"] = ANNOTATION_VERSION_V3
    record["review_round"] = 3
    record["issuer_unit_coverage"] = "exhaustive"
    record["coverage_reviewed_by"] = str(decisions.get("reviewer") or "")
    record["coverage_review_notes"] = str(decisions.get("review_notes") or "")
    packages = build_review_package(item, annotation_v2)
    expected = packages["candidate_tickers"]
    by_ticker = {
        str(value.get("ticker") or "").upper(): value
        for value in decisions.get("ticker_dispositions") or ()
    }
    if set(by_ticker) != set(expected):
        missing = sorted(set(expected) - set(by_ticker))
        extra = sorted(set(by_ticker) - set(expected))
        raise ValueError(f"coverage decisions mismatch missing={missing} extra={extra}")
    source_units = copy.deepcopy(annotation_v2.get("issuer_units") or [])
    replacements = _validated_correction_map(
        source_units,
        decisions.get("replaced_issuer_units") or [],
        operation="replace",
    )
    removals = _validated_correction_map(
        source_units,
        decisions.get("removed_issuer_units") or [],
        operation="remove",
    )
    overlap = sorted(set(replacements) & set(removals))
    if overlap:
        raise ValueError(f"issuer units cannot be both replaced and removed: {overlap}")
    corrected_units: list[dict[str, Any]] = []
    for index, unit in enumerate(source_units):
        if index in removals:
            continue
        if index in replacements:
            corrected_units.append(copy.deepcopy(replacements[index]["replacement_unit"]))
        else:
            corrected_units.append(unit)
    corrected_units.extend(copy.deepcopy(decisions.get("added_issuer_units") or []))
    record["issuer_units"] = corrected_units
    # V2 could legitimately abstain because its partial review omitted an
    # issuer passage.  Once the exhaustive V3 review adds a supported unit,
    # the article is no longer an abstention.  Derive this invariant from the
    # completed units instead of requiring record-specific repair flags.
    if record["issuer_units"]:
        record["extraction_decision"] = "labeled"
    else:
        record["extraction_decision"] = "no_supported_event"
    record["candidate_tickers"] = expected
    record["ticker_dispositions"] = [copy.deepcopy(by_ticker[ticker]) for ticker in expected]
    record = materialize_evidence_spans(record, item)
    validation = validate_annotation(record, expected_item=item)
    if not validation.valid:
        raise ValueError("invalid V3 annotation: " + ", ".join(validation.errors))
    record["annotation_sha256"] = stable_json_hash(record)
    return record


def _normalize_unit_corrections(
    annotation: Mapping[str, Any],
    corrections: list[Mapping[str, Any]],
    *,
    operation: str,
) -> list[dict[str, Any]]:
    """Bind every correction to the exact immutable V2 unit it edits."""
    source_units = annotation.get("issuer_units") or []
    normalized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in corrections:
        index = int(raw.get("source_unit_index", -1))
        if index < 0 or index >= len(source_units):
            raise ValueError(f"{operation} source_unit_index out of range: {index}")
        if index in seen:
            raise ValueError(f"duplicate {operation} correction for unit index {index}")
        seen.add(index)
        entry = {
            "source_unit_index": index,
            "source_unit_sha256": stable_json_hash(source_units[index]),
            "rationale": str(raw.get("rationale") or "").strip(),
        }
        if not entry["rationale"]:
            raise ValueError(f"{operation} correction {index} requires rationale")
        if operation == "replace":
            replacement = raw.get("replacement_unit")
            if not isinstance(replacement, Mapping):
                raise ValueError(f"replace correction {index} requires replacement_unit")
            entry["replacement_unit"] = copy.deepcopy(dict(replacement))
        normalized.append(entry)
    return normalized


def _validated_correction_map(
    source_units: list[Mapping[str, Any]],
    corrections: list[Mapping[str, Any]],
    *,
    operation: str,
) -> dict[int, Mapping[str, Any]]:
    output: dict[int, Mapping[str, Any]] = {}
    for correction in corrections:
        index = int(correction.get("source_unit_index", -1))
        if index < 0 or index >= len(source_units):
            raise ValueError(f"{operation} source_unit_index out of range: {index}")
        if index in output:
            raise ValueError(f"duplicate {operation} correction for unit index {index}")
        expected_hash = stable_json_hash(source_units[index])
        if str(correction.get("source_unit_sha256") or "") != expected_hash:
            raise ValueError(f"{operation} correction source drift at unit index {index}")
        if operation == "replace" and not isinstance(
            correction.get("replacement_unit"), Mapping
        ):
            raise ValueError(f"replace correction {index} requires replacement_unit")
        output[index] = correction
    return output


def _identity_aliases(rows: Any) -> dict[str, tuple[str, ...]]:
    output: dict[str, tuple[str, ...]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or row.get("canonical_instrument_id") or "").upper()
        values: list[str] = []
        for evidence in row.get("identity_evidence") or ():
            raw = str(evidence)
            if raw.startswith("issuer_alias:"):
                alias = raw.split(":", 1)[1].strip()
                if len(alias) >= 4:
                    values.append(alias)
        output[ticker] = tuple(dict.fromkeys(values))
    return output


def _ticker_passages(
    text: str,
    ticker: str,
    aliases: tuple[str, ...] = (),
) -> list[str]:
    patterns = [
        re.compile(
            rf"\b(?:NASDAQ|NYSE|NYSEMKT|AMEX|OTC(?:QB|QX|MKTS)?|TSX|CSE)\s*:\s*{re.escape(ticker)}\b",
            re.I,
        )
    ]
    patterns.extend(
        re.compile(rf"\b{re.escape(alias)}\b", re.I)
        for alias in aliases
    )
    passages: list[str] = []
    for pattern_index, pattern in enumerate(patterns):
        if pattern_index > 0 and passages:
            break
        for match in pattern.finditer(text):
            start = max(
                text.rfind("\n", 0, match.start()) + 1,
                text.rfind(". ", 0, match.start()) + 2,
            )
            newline = text.find("\n", match.end())
            period = text.find(". ", match.end())
            ends = [
                value
                for value in (newline, period + 1 if period >= 0 else -1)
                if value >= 0
            ]
            end = min(ends) if ends else min(len(text), match.end() + 500)
            quote = text[start:end].strip(" -\t\r\n")
            if quote and quote not in passages:
                passages.append(quote)
    return passages


def _suggest_disposition(passages: list[str], already_labeled: bool) -> dict[str, Any]:
    if already_labeled:
        return {"disposition": "labeled_issuer_unit", "requires_manual_review": False}
    combined = " ".join(passages)
    if not combined:
        return {"disposition": "metadata_only", "requires_manual_review": False}
    if ANALYST_RE.search(combined):
        return {"disposition": "analyst_context", "requires_manual_review": True}
    if PRICE_MOVE_RE.search(combined) and not CAUSAL_RE.search(combined):
        return {"disposition": "observed_price_only", "requires_manual_review": False}
    if CAUSAL_RE.search(combined):
        return {"disposition": "labeled_issuer_unit", "requires_manual_review": True}
    return {"disposition": "incidental_context", "requires_manual_review": True}


def _load_v7_prediction(root: Path, sample_id: str) -> Mapping[str, Any] | None:
    for directory in (
        root / "deterministic_v7" / "development_predictions",
        root / "deterministic_v7" / "frozen-acceptance_predictions",
    ):
        path = directory / f"{sample_id}.json"
        if path.exists():
            return read_json(path)
    return None
