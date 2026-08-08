from __future__ import annotations

import argparse
from pathlib import Path

from .sol_teacher_forecast_engine_audit import create_engine_audit


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit News Synthesis on reviewed Sol forecast audit gold"
    )
    parser.add_argument("--reviewed-gold-root", type=Path, required=True)
    parser.add_argument("--gold-review-root", type=Path, required=True)
    parser.add_argument("--evaluation-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    manifest = create_engine_audit(
        args.reviewed_gold_root,
        args.gold_review_root,
        args.evaluation_root,
        args.output_root,
    )
    metrics = manifest["metrics"]
    print(
        f"ENGINE_AUDIT engine={manifest['engine_version']} "
        f"units={metrics['units']:,} exact={metrics['exact']:,} "
        f"accuracy={metrics['accuracy']:.4f} "
        f"missing={metrics['missing_predictions']:,} "
        f"mismatches={manifest['population']['mismatches']:,}"
    )


if __name__ == "__main__":
    main()
