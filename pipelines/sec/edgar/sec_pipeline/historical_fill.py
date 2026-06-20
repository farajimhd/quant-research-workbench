from __future__ import annotations

import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[4]


@dataclass(frozen=True, slots=True)
class HistoricalFillPlan:
    start_date: date
    end_date: date
    command: list[str]
    run_script_path: Path | None = None

    @property
    def command_text(self) -> str:
        return format_command(self.command)


def build_historical_fill_plan(
    *,
    start_date: date,
    end_date: date,
    code_root_win: Path,
    python_executable: str = "python",
    execute: bool = True,
    stages: str = "default",
) -> HistoricalFillPlan:
    script = code_root_win / "pipelines" / "sec" / "edgar" / "sec_historical_backfill_orchestrator.py"
    command = [
        python_executable,
        str(script),
        "--start-date",
        start_date.isoformat(),
        "--end-date",
        end_date.isoformat(),
        "--stages",
        stages,
    ]
    if execute:
        command.append("--execute")
    return HistoricalFillPlan(start_date=start_date, end_date=end_date, command=command)


def write_plan_script(plan: HistoricalFillPlan, script_path: Path) -> Path:
    script_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "$ErrorActionPreference = 'Stop'",
        format_command(plan.command),
        "",
    ]
    script_path.write_text("\n".join(lines), encoding="utf-8")
    manifest = script_path.with_suffix(".json")
    manifest.write_text(
        json.dumps(
            {
                "created_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "start_date": plan.start_date.isoformat(),
                "end_date": plan.end_date.isoformat(),
                "command": plan.command,
                "script_path": str(script_path),
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return script_path


def run_historical_fill(plan: HistoricalFillPlan, *, cwd: Path | None = None) -> subprocess.Popen[bytes]:
    return subprocess.Popen(plan.command, cwd=str(cwd or REPO_ROOT))


def format_command(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)
