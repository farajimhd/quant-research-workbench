from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from pipelines.sec.edgar.sec_pipeline.historical_fill import build_historical_fill_plan, write_plan_script


DEFAULT_CODE_ROOT_WIN = Path("D:/TradingML/codes/quant_research_workbench_pipelines")
DEFAULT_OUTPUT_ROOT_WIN = Path("D:/market-data/prepared/sec_historical_fill")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Package-backed SEC historical fill launcher.")
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--stages", default="default")
    parser.add_argument("--code-root-win", default=str(DEFAULT_CODE_ROOT_WIN))
    parser.add_argument("--output-root-win", default=str(DEFAULT_OUTPUT_ROOT_WIN))
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)
    run_root = Path(args.output_root_win) / f"sec_historical_fill_{args.start_date}_{args.end_date}".replace(":", "")
    plan = build_historical_fill_plan(
        start_date=start,
        end_date=end,
        code_root_win=Path(args.code_root_win),
        python_executable=args.python_executable,
        execute=True,
        stages=args.stages,
    )
    script_path = write_plan_script(plan, run_root / "run_sec_historical_fill.ps1")
    print(f"script={script_path}", flush=True)
    print(plan.command_text, flush=True)
    if not args.execute:
        print("execute=false; plan written only", flush=True)
        return
    completed = subprocess.run(plan.command, check=False)
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
