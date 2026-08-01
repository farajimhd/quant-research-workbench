from __future__ import annotations

import argparse
import json
from pathlib import Path

from .coverage_review_v3 import build_review_package, record_review_decisions
from .run_deterministic_news_v6 import DEFAULT_ROOT
from .storage import read_json


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Persist one manually adjudicated News V3 coverage decision."
    )
    parser.add_argument("sample_id")
    parser.add_argument(
        "--disposition",
        action="append",
        default=[],
        metavar="TICKER=DISPOSITION",
        help="Explicit decision for each manual-review candidate; repeat as needed.",
    )
    parser.add_argument("--reviewer", default="codex_primary")
    parser.add_argument("--notes", required=True)
    parser.add_argument("--added-units-json", type=Path)
    parser.add_argument(
        "--unit-corrections-json",
        type=Path,
        help=(
            "JSON object with replaced_issuer_units and removed_issuer_units. "
            "Every entry identifies source_unit_index and a rationale; replacement "
            "entries also contain replacement_unit. Exact source hashes are added "
            "when the review is persisted."
        ),
    )
    parser.add_argument(
        "--clone-unit",
        action="append",
        default=[],
        metavar="TARGET=SOURCE",
        help="Clone an existing event unit for another explicitly reviewed listing.",
    )
    parser.add_argument(
        "--new-unit",
        action="append",
        default=[],
        metavar="TICKER=DIRECTION:CONCEPT[,CONCEPT]",
        help="Add a reviewed non-trigger issuer-history unit using queued evidence.",
    )
    parser.add_argument(
        "--new-trigger-unit",
        action="append",
        default=[],
        metavar="TICKER=DIRECTION:CONCEPT[,CONCEPT]",
        help="Add a reviewed standalone issuer event eligible for reaction study.",
    )
    parser.add_argument(
        "--unit-evidence",
        action="append",
        default=[],
        metavar="TICKER=EXACT_QUOTE",
        help=(
            "Override queued evidence for a newly added issuer unit with an exact "
            "quote from the immutable title, teaser, rendered text, or source lane; "
            "repeat to attach multiple quotes."
        ),
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    dispositions: dict[str, str] = {}
    for raw in args.disposition:
        ticker, separator, disposition = raw.partition("=")
        if not separator or not ticker or not disposition:
            parser.error(f"invalid --disposition {raw!r}; expected TICKER=DISPOSITION")
        dispositions[ticker.upper()] = disposition
    added_units = []
    replaced_units = []
    removed_units = []
    if args.added_units_json:
        added_units = json.loads(args.added_units_json.read_text(encoding="utf-8"))
        if not isinstance(added_units, list):
            parser.error("--added-units-json must contain a JSON list")
    if args.unit_corrections_json:
        corrections = json.loads(args.unit_corrections_json.read_text(encoding="utf-8"))
        if not isinstance(corrections, dict):
            parser.error("--unit-corrections-json must contain a JSON object")
        replaced_units = corrections.get("replaced_issuer_units") or []
        removed_units = corrections.get("removed_issuer_units") or []
        if not isinstance(replaced_units, list) or not isinstance(removed_units, list):
            parser.error("unit correction collections must be JSON lists")
    item = read_json(args.root / "blinded_articles" / f"{args.sample_id}.json")
    annotation = read_json(args.root / "annotations_v2" / f"{args.sample_id}.json")
    package = build_review_package(item, annotation)
    explicit_unit_evidence: dict[str, list[str]] = {}
    for raw in args.unit_evidence:
        ticker, separator, quote = raw.partition("=")
        if not separator or not ticker or not quote:
            parser.error(f"invalid --unit-evidence {raw!r}; expected TICKER=EXACT_QUOTE")
        explicit_unit_evidence.setdefault(ticker.upper(), []).append(quote)
    existing_units = {
        str(unit.get("ticker") or "").upper(): unit
        for unit in annotation.get("issuer_units") or ()
    }
    for raw in args.clone_unit:
        target, separator, source = raw.partition("=")
        if not separator or source.upper() not in existing_units:
            parser.error(f"invalid --clone-unit {raw!r}")
        unit = json.loads(json.dumps(existing_units[source.upper()]))
        unit["ticker"] = target.upper()
        unit["evidence_quotes"] = package["ticker_evidence"].get(target.upper(), [])
        unit["evidence_spans"] = []
        unit["semantic_rationale"] = (
            f"Same reviewed event applies to alternate listed instrument {target.upper()}."
        )
        added_units.append(unit)
    for raw, trigger_eligible in (
        *((value, False) for value in args.new_unit),
        *((value, True) for value in args.new_trigger_unit),
    ):
        ticker, separator, payload = raw.partition("=")
        direction, colon, concepts_text = payload.partition(":")
        concepts = [value.strip() for value in concepts_text.split(",") if value.strip()]
        if not separator or not colon or direction not in {"positive", "negative", "neutral", "mixed"} or not concepts:
            parser.error(f"invalid --new-unit {raw!r}")
        evidence = explicit_unit_evidence.get(
            ticker.upper(), package["ticker_evidence"].get(ticker.upper(), [])
        )
        if not evidence:
            parser.error(f"new unit {ticker.upper()} has no queued evidence")
        added_units.append(
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
                "forecast_trigger_eligible": trigger_eligible,
                "reaction_evaluation_eligible": trigger_eligible,
                "issuer_history_context_eligible": True,
                "analyst_context_eligible": False,
                "analyst_evaluation_eligible": False,
                "analyst_opinions": [],
                "eligibility_reason": (
                    "Direct standalone issuer event."
                    if trigger_eligible
                    else "Context inside an aggregation article, not a new standalone trigger."
                ),
                "annotation_confidence": 4,
                "ambiguity_notes": "",
                "semantic_rationale": "Manually reviewed issuer event in an aggregation article.",
            }
        )
    result = record_review_decisions(
        args.root,
        sample_id=args.sample_id,
        reviewed_dispositions=dispositions,
        reviewer=args.reviewer,
        review_notes=args.notes,
        added_issuer_units=added_units,
        replaced_issuer_units=replaced_units,
        removed_issuer_units=removed_units,
    )
    print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
