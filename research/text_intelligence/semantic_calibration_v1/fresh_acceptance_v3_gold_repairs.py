from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .schema import ANNOTATION_VERSION_V3, stable_json_hash, validate_annotation
from .storage import (
    materialize_evidence_spans,
    read_json,
    refresh_annotation_state,
    write_json_atomic,
)


REPAIR_CONTRACT = "news_fresh_acceptance_v3_gold_repairs_v2"
REPAIR_NOTE = "Third fresh-100 exhaustive post-prediction audit correction v2."


ARTICLE_REPAIRS: dict[str, dict[str, str]] = {
    # A Benzinga title reporting a venue action is not evidence that the
    # provider is publishing the regulator's primary source document.
    "N1210": {"source_origin": "editorial_aggregation"},
    # The title explicitly reports an event announced on the prior Friday.
    "N1246": {
        "content_role": "why_moving_followup",
        "source_origin": "editorial_aggregation",
    },
    # Suspension of an airline's flights is an issuer operating event, not a
    # regulatory document. No supported US issuer unit was present.
    "N1255": {"content_role": "primary_event"},
    "N1263": {
        "content_role": "why_moving_followup",
        "source_origin": "editorial_aggregation",
    },
    "N1268": {
        "content_role": "why_moving_followup",
        "source_origin": "editorial_aggregation",
    },
    "N1300": {"content_role": "primary_event"},
}

UNIT_REPAIRS: dict[str, dict[str, dict[str, Any]]] = {
    "N1268": {"WTRG": {"semantic_direction": "neutral", "positive_evidence_level": 0}},
    "N1271": {"CPB": {"remove_concepts": ("guidance",)}},
    "N1284": {"WFT": {"remove_concepts": ("guidance",)}},
    "N1288": {"GCI": {"issuer_role": "acquirer"}},
    "N1290": {
        "CNQR": {"issuer_role": "acquirer"},
        "PPL": {"semantic_direction": "neutral", "positive_evidence_level": 0},
    },
    "N1293": {"HOOD": {"remove_concepts": ("earnings",)}},
}

DISPOSITION_REPAIRS: dict[str, dict[str, str]] = {
    "N1266": {"AAPL": "incidental_context"},
    "N1282": {
        "CQB": "incidental_context", "FDP": "incidental_context",
        "IWM": "incidental_context", "PBJ": "incidental_context",
        "SDD": "incidental_context", "SKK": "incidental_context",
        "TZA": "incidental_context", "VB": "incidental_context",
    },
    "N1295": {"FXI": "incidental_context"},
}


def repair_fresh_acceptance_v3_gold(
    root: Path,
    *,
    report: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    emit = report or (lambda _message: None)
    annotations = root / "annotations_v3"
    changes: list[dict[str, Any]] = []
    targets = sorted({*ARTICLE_REPAIRS, *UNIT_REPAIRS, *DISPOSITION_REPAIRS, "N1258"})
    for index, sample_id in enumerate(targets, 1):
        emit(f"GOLD REPAIR {index}/{len(targets)} {sample_id}")
        path = annotations / f"{sample_id}.json"
        item = read_json(root / "blinded_articles" / f"{sample_id}.json")
        record = read_json(path)
        if REPAIR_NOTE in str(record.get("review_notes") or ""):
            changes.append({"sample_id": sample_id, "status": "already_repaired"})
            continue
        old_hash = str(record.pop("annotation_sha256", ""))
        for key, value in ARTICLE_REPAIRS.get(sample_id, {}).items():
            record[key] = value
        for unit in record.get("issuer_units") or ():
            unit_patch = UNIT_REPAIRS.get(sample_id, {}).get(str(unit.get("ticker") or "").upper())
            if not unit_patch:
                continue
            remove_concepts = set(unit_patch.get("remove_concepts") or ())
            if remove_concepts:
                unit["event_concepts"] = [
                    value for value in unit.get("event_concepts") or ()
                    if value not in remove_concepts
                ]
            for key, value in unit_patch.items():
                if key != "remove_concepts":
                    unit[key] = value
        for disposition in record.get("ticker_dispositions") or ():
            ticker = str(disposition.get("ticker") or "").upper()
            replacement = DISPOSITION_REPAIRS.get(sample_id, {}).get(ticker)
            if replacement:
                disposition.update({
                    "disposition": replacement,
                    "rationale": "Manual exhaustive review found explicit contextual instrument evidence.",
                    "review_basis": "manual_exhaustive_source_audit",
                })
        if sample_id == "N1246":
            for unit in record.get("issuer_units") or ():
                unit.update({
                    "time_orientation": "historical",
                    "forecast_trigger_eligible": False,
                    "reaction_evaluation_eligible": False,
                    "issuer_history_context_eligible": True,
                    "eligibility_reason": (
                        "Reported-Friday republication is historical issuer context, "
                        "not a new independent trigger at this timestamp."
                    ),
                })
        if sample_id == "N1258":
            _rename_point_in_time_ticker(record, old="EA", new="ERTS")
        record["review_notes"] = " ".join(filter(None, (
            str(record.get("review_notes") or "").strip(), REPAIR_NOTE,
        )))
        record["review_round"] = max(2, int(record.get("review_round") or 1) + 1)
        record["coverage_reviewed_by"] = "codex_primary"
        record["coverage_review_notes"] = (
            "All original metadata, source lanes and issuer units were re-audited."
        )
        record = materialize_evidence_spans(record, item)
        validation = validate_annotation(record, expected_item=item)
        if not validation.valid:
            raise ValueError(f"{sample_id} repaired gold invalid: {validation.errors}")
        record["annotation_sha256"] = stable_json_hash(record)
        write_json_atomic(path, record)
        changes.append({
            "sample_id": sample_id,
            "status": "repaired",
            "old_annotation_sha256": old_hash,
            "new_annotation_sha256": record["annotation_sha256"],
        })
    state = refresh_annotation_state(root, annotation_version=ANNOTATION_VERSION_V3)
    manifest = {
        "contract": REPAIR_CONTRACT,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "changes": changes,
        "annotation_state_sha256": stable_json_hash(state),
    }
    write_json_atomic(root / "fresh_acceptance_v3_gold_repair_manifest.json", manifest)
    return manifest


def _rename_point_in_time_ticker(record: dict[str, Any], *, old: str, new: str) -> None:
    matched = 0
    for unit in record.get("issuer_units") or ():
        if str(unit.get("ticker") or "").upper() == old:
            unit["ticker"] = new
            matched += 1
    if matched == 0 and any(
        str(unit.get("ticker") or "").upper() == new
        for unit in record.get("issuer_units") or ()
    ):
        return
    if matched != 1:
        raise ValueError(f"expected one {old} issuer unit, found {matched}")
    record["candidate_tickers"] = sorted({
        new if str(value).upper() == old else str(value).upper()
        for value in record.get("candidate_tickers") or ()
    })
    dispositions = [
        value for value in record.get("ticker_dispositions") or ()
        if str(value.get("ticker") or "").upper() != old
    ]
    replacement = next(
        (
            value for value in dispositions
            if str(value.get("ticker") or "").upper() == new
        ),
        None,
    )
    if replacement is None:
        replacement = {"ticker": new}
        dispositions.append(replacement)
    replacement.update({
        "disposition": "labeled_issuer_unit",
        "annotation_confidence": 4,
        "rationale": (
            "Manual point-in-time identity review uses the symbol stated in "
            "the 2010 publication rather than its later successor symbol."
        ),
        "evidence_quotes": [],
        "evidence_spans": [],
        "review_basis": "manual_point_in_time_identity_audit",
    })
    record["ticker_dispositions"] = sorted(
        dispositions, key=lambda value: str(value.get("ticker") or "")
    )
