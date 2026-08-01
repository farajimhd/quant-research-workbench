from __future__ import annotations

import argparse
import json
from pathlib import Path

from .coverage_review_v3 import (
    amend_review_decision,
    repair_review_evidence_from_queue,
)
from .run_deterministic_news_v6 import DEFAULT_ROOT
from .storage import read_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Amend one reviewed V3 gold decision.")
    parser.add_argument("sample_id")
    parser.add_argument("--set-disposition", action="append", default=[])
    parser.add_argument("--clear-disposition-evidence", action="append", default=[])
    parser.add_argument("--remove-source-unit-index", action="append", type=int, default=[])
    parser.add_argument("--new-unit", action="append", default=[])
    parser.add_argument("--remove-added-unit", action="append", default=[])
    parser.add_argument("--repair-evidence-from-queue", action="store_true")
    parser.add_argument("--notes", required=True)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    updates: dict[str, str] = {}
    for raw in args.set_disposition:
        ticker, separator, disposition = raw.partition("=")
        if not separator:
            parser.error(f"invalid --set-disposition {raw!r}")
        updates[ticker.upper()] = disposition
    package = read_json(args.root / "coverage_review_v3" / "queue" / f"{args.sample_id}.json")
    added = []
    for raw in args.new_unit:
        ticker, separator, payload = raw.partition("=")
        direction, colon, concepts_text = payload.partition(":")
        concepts = [value.strip() for value in concepts_text.split(",") if value.strip()]
        if not separator or not colon or direction not in {"positive", "negative", "neutral", "mixed"} or not concepts:
            parser.error(f"invalid --new-unit {raw!r}")
        evidence = (package.get("ticker_evidence") or {}).get(ticker.upper(), [])
        if not evidence:
            parser.error(f"new unit {ticker.upper()} has no source-backed evidence")
        added.append(
            {
                "ticker": ticker.upper(),
                "issuer_role": "primary_subject",
                "evidence_scope": "ticker_specific",
                "event_concepts": concepts,
                "evidence_quotes": evidence,
                "evidence_spans": [],
                "modality": "confirmed",
                "time_orientation": "current",
                "positive_evidence_level": 2 if direction in {"positive", "mixed"} else 0,
                "negative_evidence_level": 2 if direction in {"negative", "mixed"} else 0,
                "semantic_direction": direction,
                "forecast_trigger_eligible": False,
                "reaction_evaluation_eligible": False,
                "issuer_history_context_eligible": True,
                "analyst_context_eligible": False,
                "analyst_evaluation_eligible": False,
                "analyst_opinions": [],
                "eligibility_reason": "Issuer context inside a non-standalone aggregation article.",
                "annotation_confidence": 4,
                "ambiguity_notes": "",
                "semantic_rationale": args.notes,
            }
        )
    result = amend_review_decision(
        args.root,
        sample_id=args.sample_id,
        disposition_updates=updates,
        clear_disposition_evidence_tickers=args.clear_disposition_evidence,
        added_issuer_units=added,
        remove_added_unit_tickers=args.remove_added_unit,
        remove_source_unit_indices=args.remove_source_unit_index,
        notes_append=args.notes,
    )
    evidence_result = None
    if args.repair_evidence_from_queue:
        evidence_result = repair_review_evidence_from_queue(
            args.root,
            sample_id=args.sample_id,
        )
    print(
        json.dumps(
            {"decision_sha256": result["decision_sha256"], "evidence": evidence_result},
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
