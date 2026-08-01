from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path

from .comparison import evaluate_predictions, load_collection
from .news_v6 import fit_v6, generate_predictions
from .storage import write_json_atomic


DEFAULT_ROOT = Path(
    r"D:\TradingML\runtimes\text_intelligence\semantic_calibration_v1\news_1000"
)


def _metric_rows(v5: dict, v6: dict) -> list[tuple[str, float, float]]:
    return [
        ("Extraction F1", v5["extraction"]["f1"], v6["extraction"]["f1"]),
        ("Ticker scope precision", v5["ticker_scope"]["precision"], v6["ticker_scope"]["precision"]),
        ("Ticker scope recall", v5["ticker_scope"]["recall"], v6["ticker_scope"]["recall"]),
        ("Ticker scope F1", v5["ticker_scope"]["f1"], v6["ticker_scope"]["f1"]),
        ("Content role macro F1", v5["content_role"]["macro_f1"], v6["content_role"]["macro_f1"]),
        ("Source origin macro F1", v5["source_origin"]["macro_f1"], v6["source_origin"]["macro_f1"]),
        ("Direction accuracy", v5["semantic_direction"]["accuracy"], v6["semantic_direction"]["accuracy"]),
        ("Direction macro F1", v5["semantic_direction"]["macro_f1"], v6["semantic_direction"]["macro_f1"]),
        ("Canonical concept-family F1", v5["event_concepts"]["f1"], v6["event_concepts"]["f1"]),
        ("Forecast eligibility precision", v5["eligibility"]["forecast_trigger_eligible"]["precision"], v6["eligibility"]["forecast_trigger_eligible"]["precision"]),
        ("Forecast eligibility recall", v5["eligibility"]["forecast_trigger_eligible"]["recall"], v6["eligibility"]["forecast_trigger_eligible"]["recall"]),
        ("Forecast eligibility F1", v5["eligibility"]["forecast_trigger_eligible"]["f1"], v6["eligibility"]["forecast_trigger_eligible"]["f1"]),
    ]


def _write_holdout_summary(output: Path, v5: dict, v6: dict) -> None:
    rows = _metric_rows(v5, v6)
    lines = [
        "# News V5 vs V6 locked-holdout report",
        "",
        "The comparison uses the sealed 588 fit / 194 calibration / 218 holdout split. ",
        "V6 was fit only on the fit partition and its thresholds were selected only on calibration. ",
        "The immutable human annotations were not rewritten, price reactions were not used, and SEC is out of scope.",
        "",
        "| Metric | V5 | V6 candidate | Change |",
        "|---|---:|---:|---:|",
    ]
    for label, old, new in rows:
        lines.append(f"| {label} | {old:.4f} | {new:.4f} | {new - old:+.4f} |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "V6 is a research candidate, not a production cutover. It improves the aggregate F1, classification, and "
            "eligibility metrics, but ticker-scope recall regresses materially and the fit-to-holdout generalization gap "
            "still requires repair and a new external "
            "validation collection before live or historical backfill use.",
            "",
            "The concept metric uses an explicit broad canonical-family projection because the human annotations preserve "
            "precise, evidence-grounded concepts rather than one closed exact-string ontology. The projection is used only "
            "for comparison and does not alter ground truth.",
            "",
        ]
    )
    (output / "v5_v6_holdout_report.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fit and evaluate the News-only V6 calibration candidate."
    )
    parser.add_argument("--runtime-root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    output = args.runtime_root / "v5_v6_calibration"
    items = load_collection(args.runtime_root)
    artifact = output / "news_v6_candidate_1.joblib"
    model = fit_v6(
        items,
        v5_dir=output / "v5_predictions",
        artifact_path=artifact,
    )
    generate_predictions(
        model,
        items,
        v5_dir=output / "v5_predictions",
        output_dir=output / "v6_predictions",
    )
    write_json_atomic(output / "v6_thresholds.json", asdict(model.thresholds))
    reports: dict[str, dict] = {}
    for name, splits in (
        ("fit", {"fit"}),
        ("calibration", {"calibration"}),
        ("holdout", {"holdout"}),
        ("all", None),
    ):
        report = evaluate_predictions(
            items,
            prediction_dir=output / "v6_predictions",
            splits=splits,
            canonical_concepts=True,
        )
        reports[name] = report
        write_json_atomic(output / f"v6_{name}_metrics.json", report)
        print(
            f"{name.upper()} samples={report['sample_count']} "
            f"role_macro_f1={report['content_role']['macro_f1']:.3f} "
            f"direction_macro_f1={report['semantic_direction']['macro_f1']:.3f} "
            f"family_f1={report['event_concepts']['f1']:.3f}",
            flush=True,
        )
    v5_holdout = evaluate_predictions(
        items,
        prediction_dir=output / "v5_predictions",
        splits={"holdout"},
        canonical_concepts=True,
    )
    _write_holdout_summary(output, v5_holdout, reports["holdout"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
