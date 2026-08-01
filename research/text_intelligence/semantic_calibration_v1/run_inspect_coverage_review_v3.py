from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .run_deterministic_news_v6 import DEFAULT_ROOT
from .storage import read_json


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Inspect compact evidence for pending News V3 coverage reviews."
    )
    parser.add_argument("--sample-id", action="append", default=[])
    parser.add_argument("--max-manual", type=int)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()

    decision_root = args.root / "coverage_review_v3" / "decisions"
    decided = {path.stem for path in decision_root.glob("*.json")}
    if args.sample_id:
        sample_ids = args.sample_id
    else:
        rows: list[tuple[int, str]] = []
        for path in (args.root / "coverage_review_v3" / "queue").glob("N*.json"):
            if path.stem in decided:
                continue
            package = read_json(path)
            count = len(package.get("manual_review_tickers") or ())
            if args.max_manual is None or count <= args.max_manual:
                rows.append((count, path.stem))
        sample_ids = [sample_id for _, sample_id in sorted(rows)[: args.limit]]

    output = [_compact(args.root, sample_id) for sample_id in sample_ids]
    print(json.dumps(output, ensure_ascii=False, indent=2), flush=True)
    return 0


def _compact(root: Path, sample_id: str) -> dict[str, Any]:
    package = read_json(root / "coverage_review_v3" / "queue" / f"{sample_id}.json")
    annotation = read_json(root / "annotations_v2" / f"{sample_id}.json")
    labels = package.get("v7_candidate_labels") or {}
    manual = package.get("manual_review_tickers") or []
    return {
        "sample_id": sample_id,
        "title": package.get("title"),
        "content_role": package.get("content_role"),
        "existing_units": [
            {
                "ticker": unit.get("ticker"),
                "direction": unit.get("semantic_direction"),
                "concepts": unit.get("event_concepts") or [],
                "trigger": unit.get("forecast_trigger_eligible"),
                "evidence": unit.get("evidence_quotes") or [],
            }
            for unit in annotation.get("issuer_units") or ()
        ],
        "manual": {
            ticker: {
                "evidence": (package.get("ticker_evidence") or {}).get(ticker, []),
                "v7": [
                    {
                        "role": value.get("unit_role"),
                        "issuer_role": value.get("issuer_role"),
                        "scope": value.get("evidence_scope"),
                        "concepts": value.get("event_concepts") or [],
                        "direction": value.get("semantic_direction"),
                        "trigger": value.get("forecast_trigger_eligible"),
                    }
                    for value in labels.get(ticker, [])
                ],
            }
            for ticker in manual
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
