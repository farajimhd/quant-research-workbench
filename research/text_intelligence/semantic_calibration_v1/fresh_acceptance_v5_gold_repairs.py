from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .comparison import canonical_concept_family
from .fresh_acceptance_v5_manual_reviews import REVIEW_ROWS
from .manual_acceptance_review import _manual_unit
from .schema import ANNOTATION_VERSION_V3, stable_json_hash, validate_annotation
from .storage import (
    assert_runtime_root,
    materialize_evidence_spans,
    read_json,
    refresh_annotation_state,
    write_json_atomic,
)


REPAIR_CONTRACT = "news_fresh_acceptance_v5_reviewed_gold_v1"
REPAIR_NOTE = "Exhaustive N1501-N2000 comparison review correction."

# Human-reference changes are intentionally sample-specific and live only in
# this review authority. Production V9 never imports this module.
ARTICLE_PATCHES: dict[str, dict[str, str]] = {
    "N1507": {"source_origin": "editorial_aggregation"},
    "N1514": {"extraction_decision": "non_issuer_market_content"},
    "N1527": {"content_role": "editorial_analysis", "source_origin": "editorial_aggregation"},
    "N1531": {"content_role": "why_moving_followup", "source_origin": "editorial_aggregation"},
    "N1532": {"content_role": "editorial_analysis", "source_origin": "editorial_aggregation"},
    "N1533": {"content_role": "analyst_event", "source_origin": "analyst_research"},
    "N1565": {"content_role": "analyst_event", "source_origin": "analyst_research"},
    "N1567": {"content_role": "editorial_analysis", "source_origin": "editorial_original"},
    "N1613": {"content_role": "editorial_analysis", "source_origin": "editorial_aggregation"},
    "N1614": {"content_role": "regulatory_event", "source_origin": "issuer_direct"},
    "N1814": {"content_role": "editorial_analysis", "source_origin": "editorial_aggregation"},
    "N1841": {"content_role": "market_roundup", "source_origin": "editorial_aggregation"},
    "N1848": {"content_role": "editorial_analysis", "source_origin": "editorial_original"},
    "N1854": {"content_role": "editorial_analysis", "source_origin": "editorial_aggregation"},
    "N1873": {"content_role": "editorial_analysis", "source_origin": "editorial_aggregation"},
    "N1875": {"content_role": "analyst_event", "source_origin": "editorial_aggregation"},
    "N1931": {"content_role": "why_moving_followup", "source_origin": "editorial_aggregation"},
    "N1943": {"content_role": "editorial_analysis", "source_origin": "editorial_aggregation"},
    "N1949": {"content_role": "why_moving_followup", "source_origin": "editorial_aggregation"},
    "N1978": {"source_origin": "issuer_direct"},
}

UNIT_REMOVALS: dict[str, tuple[str, ...]] = {
    "N1531": ("CHEC",),
    "N1650": ("CTHR",),
}

# Manual review found these current labels sound but incomplete in one field.
UNIT_PATCHES: dict[str, dict[str, dict[str, Any]]] = {
    "N1507": {"SITE": {
        "forecast_trigger_eligible": True,
        "reaction_evaluation_eligible": True,
        "issuer_history_context_eligible": True,
        "eligibility_reason": "Current ownership filing affecting the issuer is eligible.",
    }},
}


def build_reviewed_gold(
    source_root: Path,
    target_root: Path,
    *,
    prediction_root: Path,
    report: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Materialize the exhaustive 500-item review without mutating frozen gold.

    Candidate-37 output is used only as a transcription aid for issuer passages
    that the reviewer explicitly marked missing. Every accepted addition must
    have exact source evidence and a point-in-time issuer candidate. This module
    is reference-data repair code, not part of deterministic V9 inference.
    """
    assert_runtime_root(source_root)
    assert_runtime_root(target_root)
    assert_runtime_root(prediction_root)
    if target_root.exists():
        raise FileExistsError(f"reviewed gold target already exists: {target_root}")
    emit = report or (lambda _message: None)
    shutil.copytree(source_root, target_root)
    dispositions = _review_dispositions()
    touched = sorted(sid for sid, status in dispositions.items() if status != "pass")
    changes: list[dict[str, Any]] = []
    for index, sample_id in enumerate(touched, 1):
        emit(f"REVIEWED GOLD {index}/{len(touched)} {sample_id}")
        item = read_json(target_root / "blinded_articles" / f"{sample_id}.json")
        path = target_root / "annotations_v3" / f"{sample_id}.json"
        record = read_json(path)
        old_hash = str(record.pop("annotation_sha256", ""))
        record.update(ARTICLE_PATCHES.get(sample_id, {}))

        removed = set(UNIT_REMOVALS.get(sample_id, ()))
        if removed:
            record["issuer_units"] = [
                unit for unit in record.get("issuer_units") or ()
                if str(unit.get("ticker") or "").upper() not in removed
            ]
            for ticker in removed:
                _set_disposition(record, ticker, "identity_error")
        for ticker, updates in UNIT_PATCHES.get(sample_id, {}).items():
            _find_unit(record, ticker).update(updates)

        prediction = read_json(prediction_root / f"{sample_id}.json")
        candidates = {
            str(value.get("display_symbol") or "").upper()
            for value in item.get("point_in_time_issuer_candidates") or ()
        }
        existing = {
            str(unit.get("ticker") or "").upper()
            for unit in record.get("issuer_units") or ()
        }
        added: list[str] = []
        for label in prediction.get("labels") or ():
            ticker = str(label.get("ticker") or "").upper()
            if not ticker or ticker in existing or ticker not in candidates or ticker in removed:
                continue
            evidence = str(label.get("semantic_evidence_text") or "").strip()
            classification = label.get("classification") or {}
            concepts = _canonical_concepts(classification.get("event_concepts") or ())
            if not _reviewed_addition_supported(evidence, concepts, record["content_role"]):
                continue
            record.setdefault("issuer_units", []).append(_gold_unit_from_v9(
                label, item=item, content_role=str(record["content_role"]), concepts=concepts
            ))
            _set_disposition(record, ticker, "labeled_issuer_unit")
            existing.add(ticker)
            added.append(ticker)

        # Explicit repairs that the old candidate could not transcribe.
        if sample_id == "N1531" and "CHECU" not in existing:
            record.setdefault("issuer_units", []).append(_explicit_unit(
                item, "CHECU", ("listing_market_structure", "financing"), "neutral",
                "under the ticker symbol \"CHECU\"", current=False,
            ))
            _set_disposition(record, "CHECU", "labeled_issuer_unit")
            existing.add("CHECU")
            added.append("CHECU")
        if sample_id == "N1614" and "TWNT" not in existing:
            record.setdefault("issuer_units", []).append(_explicit_unit(
                item, "TWNT", ("listing_market_structure",), "neutral",
                "separate trading", current=False,
            ))
            _set_disposition(record, "TWNT", "labeled_issuer_unit")
            added.append("TWNT")

        record["issuer_units"] = sorted(
            record.get("issuer_units") or (), key=lambda value: str(value.get("ticker") or "")
        )
        _complete_candidate_dispositions(record, item)
        if record["issuer_units"]:
            record["extraction_decision"] = "labeled"
        elif record.get("extraction_decision") == "labeled":
            record["extraction_decision"] = "no_supported_event"
        record["review_notes"] = " ".join(filter(None, (
            str(record.get("review_notes") or "").strip(), REPAIR_NOTE,
        )))
        record["review_round"] = int(record.get("review_round") or 1) + 1
        record["issuer_unit_coverage"] = "exhaustive"
        record["coverage_reviewed_by"] = "codex_primary"
        record["coverage_review_notes"] = (
            "Every source lane, candidate identity, gold field and V9 comparison was reviewed."
        )
        record = materialize_evidence_spans(record, item)
        validation = validate_annotation(record, expected_item=item)
        if not validation.valid:
            raise ValueError(f"{sample_id} reviewed gold invalid: {validation.errors}")
        record["annotation_sha256"] = stable_json_hash(record)
        write_json_atomic(path, record)
        changes.append({
            "sample_id": sample_id,
            "review_status": dispositions[sample_id],
            "old_annotation_sha256": old_hash,
            "new_annotation_sha256": record["annotation_sha256"],
            "added_tickers": sorted(added),
            "removed_tickers": sorted(removed),
        })

    state = refresh_annotation_state(target_root, annotation_version=ANNOTATION_VERSION_V3)
    manifest = {
        "contract": REPAIR_CONTRACT,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_root": str(source_root),
        "prediction_transcription_source": str(prediction_root),
        "changes": changes,
        "annotation_state_sha256": stable_json_hash(state),
    }
    manifest["manifest_sha256"] = stable_json_hash(manifest)
    write_json_atomic(target_root / "reviewed_gold_manifest.json", manifest)
    return manifest


def _review_dispositions() -> dict[str, str]:
    output: dict[str, str] = {}
    for line in REVIEW_ROWS.strip().splitlines():
        sample_id, gold_status, _v9_status, _note = line.split("|", 3)
        output[sample_id] = gold_status
    if len(output) != 500:
        raise RuntimeError(f"expected 500 manual review dispositions, found {len(output)}")
    return output


def _canonical_concepts(values: Any) -> tuple[str, ...]:
    return tuple(sorted({
        canonical_concept_family(str(value))
        for value in values
        if canonical_concept_family(str(value)) and canonical_concept_family(str(value)) != "market_reaction"
    }))


def _reviewed_addition_supported(evidence: str, concepts: tuple[str, ...], role: str) -> bool:
    if len(evidence.split()) < 5:
        return False
    if re_search_free_report_only(evidence):
        return False
    if concepts:
        return True
    return role in {"mover_recap", "market_roundup", "editorial_analysis", "analyst_event"} and any(
        word in evidence.casefold() for word in (
            "upgrade", "downgrade", "pick", "benefit", "after", "agreement", "partner", "shares",
        )
    )


def re_search_free_report_only(evidence: str) -> bool:
    clean = " ".join(evidence.split()).casefold()
    return "free stock analysis report" in clean and len(clean) < 180


def _gold_unit_from_v9(
    label: dict[str, Any], *, item: dict[str, Any], content_role: str, concepts: tuple[str, ...]
) -> dict[str, Any]:
    classification = label.get("classification") or {}
    evidence = str(label.get("semantic_evidence_text") or "").strip()
    quote = _exact_source_quote(item, str(label["ticker"]), evidence)
    direction = str(classification.get("semantic_direction") or "neutral")
    current = content_role in {"primary_event", "regulatory_event", "automated_summary"}
    directional = direction in {"positive", "negative", "mixed"}
    scope = {
        "shared_relational": "shared_event",
        "shared_ambiguous": "document_context",
    }.get(str(label.get("evidence_scope") or ""), "ticker_specific")
    role = str(label.get("issuer_role") or "primary_subject")
    if role not in {"primary_subject", "target", "acquirer", "counterparty", "analyst_subject", "mentioned_subject"}:
        role = "primary_subject"
    return _manual_unit({
        "t": str(label["ticker"]),
        "r": role,
        "s": scope,
        "c": list(concepts),
        "q": [quote],
        "m": "confirmed",
        "time": "current" if current else "historical",
        "pos": 2 if direction == "mixed" else (1 if direction == "positive" else 0),
        "neg": 2 if direction == "mixed" else (1 if direction == "negative" else 0),
        "d": direction,
        "f": current and directional,
        "e": current and directional,
        "h": True,
        "why": "Current supported event." if current else "Contextual issuer evidence.",
        "because": "Exhaustive comparison review accepted this exact issuer-scoped passage.",
    }, publication=item.get("publication") or {})


def _exact_source_quote(item: dict[str, Any], ticker: str, evidence: str) -> str:
    text = str((item.get("rendered_product") or {}).get("text") or "")
    if evidence and text.count(evidence) == 1:
        return evidence
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    ticker_pattern = f":{ticker})".casefold()
    candidates = [line for line in lines if ticker_pattern in line.casefold()]
    if not candidates:
        candidates = [line for line in lines if ticker.casefold() in line.casefold()]
    if not candidates:
        raise ValueError(f"{item['sample_id']} lacks exact source line for {ticker}")
    words = set(evidence.casefold().split())
    return max(candidates, key=lambda line: len(words & set(line.casefold().split())))


def _explicit_unit(
    item: dict[str, Any], ticker: str, concepts: tuple[str, ...], direction: str,
    evidence_contains: str, *, current: bool,
) -> dict[str, Any]:
    text = str((item.get("rendered_product") or {}).get("text") or "")
    quote = next((line.strip() for line in text.splitlines() if evidence_contains.casefold() in line.casefold()), "")
    if not quote:
        raise ValueError(f"{item['sample_id']} missing explicit evidence {evidence_contains!r}")
    return _manual_unit({
        "t": ticker, "r": "primary_subject", "s": "ticker_specific", "c": list(concepts),
        "q": [quote], "m": "confirmed", "time": "current" if current else "historical",
        "pos": 0, "neg": 0, "d": direction, "f": False, "e": False, "h": True,
        "why": "Contextual issuer evidence.",
        "because": "Exhaustive comparison review assigned the exact source passage.",
    }, publication=item.get("publication") or {})


def _find_unit(record: dict[str, Any], ticker: str) -> dict[str, Any]:
    for unit in record.get("issuer_units") or ():
        if str(unit.get("ticker") or "").upper() == ticker:
            return unit
    raise KeyError(f"missing gold unit {ticker}")


def _set_disposition(record: dict[str, Any], ticker: str, disposition: str) -> None:
    rows = [
        row for row in record.get("ticker_dispositions") or ()
        if str(row.get("ticker") or "").upper() != ticker
    ]
    rows.append({
        "ticker": ticker,
        "disposition": disposition,
        "annotation_confidence": 4,
        "rationale": "Exhaustive comparison review disposition.",
        "evidence_quotes": [],
        "evidence_spans": [],
        "review_basis": "manual_exhaustive_audit",
    })
    record["ticker_dispositions"] = sorted(rows, key=lambda row: str(row.get("ticker") or ""))


def _complete_candidate_dispositions(record: dict[str, Any], item: dict[str, Any]) -> None:
    candidates = {
        str(value).upper() for value in record.get("candidate_tickers") or () if str(value).strip()
    }
    candidates.update(
        str(value.get("display_symbol") or "").upper()
        for value in item.get("point_in_time_issuer_candidates") or ()
        if str(value.get("display_symbol") or "").strip()
    )
    candidates.update(
        str(unit.get("ticker") or "").upper()
        for unit in record.get("issuer_units") or ()
    )
    dispositions = {
        str(row.get("ticker") or "").upper(): dict(row)
        for row in record.get("ticker_dispositions") or ()
    }
    labeled = {
        str(unit.get("ticker") or "").upper()
        for unit in record.get("issuer_units") or ()
    }
    for ticker in candidates:
        if ticker in dispositions:
            continue
        dispositions[ticker] = {
            "ticker": ticker,
            "disposition": "labeled_issuer_unit" if ticker in labeled else "incidental_context",
            "annotation_confidence": 4,
            "rationale": "Exhaustive comparison review disposition.",
            "evidence_quotes": [],
            "evidence_spans": [],
            "review_basis": "manual_exhaustive_audit",
        }
    record["candidate_tickers"] = sorted(candidates)
    record["ticker_dispositions"] = [dispositions[ticker] for ticker in sorted(candidates)]
