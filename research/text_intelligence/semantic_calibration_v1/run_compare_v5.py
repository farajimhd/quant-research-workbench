from __future__ import annotations

import argparse
from pathlib import Path

from .comparison import evaluate_predictions, run_v5_predictions
from .storage import write_json_atomic


DEFAULT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\news_1000"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Rerun the exact current News V5 authority on locked human reviews."
    )
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    output = args.runtime_root / "v5_v6_calibration"
    items = run_v5_predictions(args.runtime_root, output_dir=output)
    for name, splits in (
        ("fit", {"fit"}),
        ("calibration", {"calibration"}),
        ("holdout", {"holdout"}),
        ("all", None),
    ):
        report = evaluate_predictions(
            items,
            prediction_dir=output / "v5_predictions",
            splits=splits,
        )
        write_json_atomic(output / f"v5_{name}_metrics.json", report)
        family_report = evaluate_predictions(
            items,
            prediction_dir=output / "v5_predictions",
            splits=splits,
            canonical_concepts=True,
        )
        write_json_atomic(output / f"v5_{name}_family_metrics.json", family_report)
        print(
            f"{name.upper()} samples={report['sample_count']} "
            f"role_macro_f1={report['content_role']['macro_f1']:.3f} "
            f"direction_macro_f1={report['semantic_direction']['macro_f1']:.3f} "
            f"concept_f1={report['event_concepts']['f1']:.3f} "
            f"family_f1={family_report['event_concepts']['f1']:.3f}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
