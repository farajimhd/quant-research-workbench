from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep GPT-OSS LLM parameters for the recommended news pipeline.")
    parser.add_argument("--supervision-run", required=True)
    parser.add_argument("--article-limit", type=int, default=100)
    parser.add_argument("--model", default="prosusai-finbert")
    parser.add_argument("--output-root", default=PACKAGE_ROOT / "pipeline_evaluation_runs" / "llm_parameter_sweeps")
    parser.add_argument("--python", default=sys.executable)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sweep_dir = Path(args.output_root) / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sweep_dir.mkdir(parents=True, exist_ok=True)
    candidates = [
        {"name": "sum_low_256_mat040", "merge_mode": "summary_only", "max_tokens": 256, "reasoning_effort": "low", "response_format": "json_object", "min_materiality": 0.40, "min_text_chars": 40},
        {"name": "sum_low_384_mat040", "merge_mode": "summary_only", "max_tokens": 384, "reasoning_effort": "low", "response_format": "json_object", "min_materiality": 0.40, "min_text_chars": 40},
        {"name": "sum_low_512_mat040", "merge_mode": "summary_only", "max_tokens": 512, "reasoning_effort": "low", "response_format": "json_object", "min_materiality": 0.40, "min_text_chars": 40},
        {"name": "sum_low_256_mat060", "merge_mode": "summary_only", "max_tokens": 256, "reasoning_effort": "low", "response_format": "json_object", "min_materiality": 0.60, "min_text_chars": 80},
        {"name": "sum_low_384_mat060", "merge_mode": "summary_only", "max_tokens": 384, "reasoning_effort": "low", "response_format": "json_object", "min_materiality": 0.60, "min_text_chars": 80},
        {"name": "sum_low_512_mat060", "merge_mode": "summary_only", "max_tokens": 512, "reasoning_effort": "low", "response_format": "json_object", "min_materiality": 0.60, "min_text_chars": 80},
        {"name": "sum_low_256_mat070", "merge_mode": "summary_only", "max_tokens": 256, "reasoning_effort": "low", "response_format": "json_object", "min_materiality": 0.70, "min_text_chars": 120},
        {"name": "sum_low_384_mat070", "merge_mode": "summary_only", "max_tokens": 384, "reasoning_effort": "low", "response_format": "json_object", "min_materiality": 0.70, "min_text_chars": 120},
    ]
    rows = []
    for candidate in candidates:
        result = run_candidate(args, sweep_dir, candidate)
        rows.append(result)
        (sweep_dir / "sweep_results.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        (sweep_dir / "analysis.md").write_text(render_analysis(rows), encoding="utf-8")
    print(json.dumps({"sweep_dir": str(sweep_dir), "results": rows}, indent=2, ensure_ascii=False))
    return 0


def run_candidate(args: argparse.Namespace, sweep_dir: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    output_root = sweep_dir / candidate["name"]
    command = [
        args.python,
        str(PACKAGE_ROOT / "scripts" / "evaluate_recommended_pipeline.py"),
        "--supervision-run",
        args.supervision_run,
        "--models",
        args.model,
        "--article-limit",
        str(args.article_limit),
        "--enable-llm",
        "--llm-max-tokens",
        str(candidate["max_tokens"]),
        "--llm-reasoning-effort",
        candidate["reasoning_effort"],
        "--llm-response-format",
        candidate["response_format"],
        "--llm-merge-mode",
        candidate["merge_mode"],
        "--llm-min-materiality",
        str(candidate["min_materiality"]),
        "--llm-min-text-chars",
        str(candidate["min_text_chars"]),
        "--output-root",
        str(output_root),
    ]
    completed = subprocess.run(command, cwd=REPO_ROOT, text=True, capture_output=True, check=False)
    summary_path = latest_summary(output_root)
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path else {}
    model_summary = summary.get("models", [{}])[0]
    llm_timing = model_summary.get("stage_timings", {}).get("llm", {})
    return {
        **candidate,
        "returncode": completed.returncode,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
        "summary_path": str(summary_path) if summary_path else "",
        "article_count": summary.get("article_count", 0),
        "sentiment_accuracy": model_summary.get("categorical_accuracy", {}).get("sentiment_label", {}).get("accuracy"),
        "event_accuracy": model_summary.get("categorical_accuracy", {}).get("event_type", {}).get("accuracy"),
        "elapsed_seconds": model_summary.get("elapsed_seconds"),
        "throughput_articles_per_second": model_summary.get("throughput_articles_per_second"),
        "llm_status_distribution": llm_timing.get("status_distribution", {}),
        "llm_median_seconds": llm_timing.get("median_seconds"),
        "llm_p95_seconds": llm_timing.get("p95_seconds"),
        "llm_mean_seconds": llm_timing.get("mean_seconds"),
    }


def latest_summary(output_root: Path) -> Path | None:
    candidates = sorted(output_root.glob("*/summary.json"))
    return candidates[-1] if candidates else None


def render_analysis(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# LLM Parameter Sweep",
        "",
        "| Candidate | Merge | Sent Acc | Event Acc | LLM Status | LLM Median s | LLM p95 s | Elapsed s | Throughput / s |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {name} | {merge} | {sent} | {event} | {status} | {median} | {p95} | {elapsed} | {throughput} |".format(
                name=row["name"],
                merge=row.get("merge_mode", ""),
                sent=fmt(row.get("sentiment_accuracy")),
                event=fmt(row.get("event_accuracy")),
                status=json.dumps(row.get("llm_status_distribution", {}), ensure_ascii=False),
                median=fmt(row.get("llm_median_seconds")),
                p95=fmt(row.get("llm_p95_seconds")),
                elapsed=fmt(row.get("elapsed_seconds")),
                throughput=fmt(row.get("throughput_articles_per_second")),
            )
        )
    return "\n".join(lines) + "\n"


def fmt(value: Any) -> str:
    if value is None:
        return ""
    return f"{float(value):.3f}"


if __name__ == "__main__":
    raise SystemExit(main())
