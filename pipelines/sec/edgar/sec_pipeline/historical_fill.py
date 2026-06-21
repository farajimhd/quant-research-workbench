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
    read_database: str = "",
    write_database: str = "",
    extra_args: list[str] | None = None,
) -> HistoricalFillPlan:
    script = code_root_win / "pipelines" / "sec" / "edgar" / "sec_historical_gap_fill.py"
    command = [
        python_executable,
        str(script),
        "--start-date",
        start_date.isoformat(),
        "--end-date",
        end_date.isoformat(),
    ]
    if read_database:
        command.extend(["--read-database", read_database])
    if write_database:
        command.extend(["--write-database", write_database])
    if extra_args:
        command.extend(extra_args)
    if execute:
        command.append("--execute")
    return HistoricalFillPlan(start_date=start_date, end_date=end_date, command=command)


def build_xbrl_companyfacts_catchup_plan(
    *,
    start_date: date,
    end_date: date,
    code_root_win: Path,
    read_database: str,
    write_database: str,
    python_executable: str = "python",
    execute: bool = True,
    workers: int = 4,
    batch_size: int = 10000,
) -> HistoricalFillPlan:
    script = code_root_win / "pipelines" / "sec" / "edgar" / "sec_xbrl_companyfacts_catchup.py"
    command = [
        python_executable,
        str(script),
        "--read-database",
        read_database,
        "--write-database",
        write_database,
        "--start-date",
        start_date.isoformat(),
        "--end-date",
        end_date.isoformat(),
        "--workers",
        str(max(1, workers)),
        "--batch-size",
        str(max(1, batch_size)),
    ]
    if execute:
        command.append("--execute")
    return HistoricalFillPlan(start_date=start_date, end_date=end_date, command=command)


def build_xbrl_integrity_repair_plan(
    *,
    code_root_win: Path,
    database: str,
    python_executable: str = "python",
    execute: bool = True,
    scope_start_date: date = date(2019, 1, 1),
) -> HistoricalFillPlan:
    script = code_root_win / "pipelines" / "sec" / "edgar" / "sec_xbrl_integrity_repair.py"
    command = [
        python_executable,
        str(script),
        "--database",
        database,
        "--scope-start-date",
        scope_start_date.isoformat(),
        "--stages",
        "drop-legacy,filing-parents,frame-parents",
    ]
    if execute:
        command.append("--execute")
    return HistoricalFillPlan(start_date=scope_start_date, end_date=date.today(), command=command)


def build_integrity_audit_plan(
    *,
    code_root_win: Path,
    database: str,
    python_executable: str = "python",
    scope_start_date: date = date(2019, 1, 1),
) -> HistoricalFillPlan:
    script = code_root_win / "pipelines" / "sec" / "edgar" / "sec_integrity_audit.py"
    command = [
        python_executable,
        str(script),
        "--database",
        database,
        "--scope-start-date",
        scope_start_date.isoformat(),
        "--require-v2-tables",
    ]
    return HistoricalFillPlan(start_date=scope_start_date, end_date=date.today(), command=command)


def write_plan_script(plan: HistoricalFillPlan, script_path: Path) -> Path:
    return write_multi_plan_script([plan], script_path)


def write_multi_plan_script(plans: list[HistoricalFillPlan], script_path: Path) -> Path:
    if not plans:
        raise ValueError("at least one historical fill plan is required")
    script_path.parent.mkdir(parents=True, exist_ok=True)
    log_name = script_path.with_suffix(".log").name
    lines = [
        "$ErrorActionPreference = 'Stop'",
        "Set-StrictMode -Version Latest",
        f"$secGapFillLog = Join-Path $PSScriptRoot {powershell_single_quote(log_name)}",
        "Write-Host \"SEC gap-fill wrapper started $(Get-Date -Format o)\"",
        "Write-Host \"Script: $PSCommandPath\"",
        "Write-Host \"Log: $secGapFillLog\"",
        "Start-Transcript -Path $secGapFillLog -Append | Out-Null",
        "try {",
        "",
    ]
    for index, plan in enumerate(plans, start=1):
        lines.append(f"    Write-Host {powershell_single_quote(f'SEC historical task {index}/{len(plans)} started')}")
        lines.append(f"    & {format_command(plan.command)}")
        lines.append("    if ($LASTEXITCODE -ne 0) {")
        lines.append(f"        throw {powershell_single_quote(f'SEC historical task {index}/{len(plans)} failed with exit code ')} + $LASTEXITCODE")
        lines.append("    }")
        lines.append(f"    Write-Host {powershell_single_quote(f'SEC historical task {index}/{len(plans)} completed')}")
        lines.append("")
    lines.extend(
        [
            "    Write-Host \"SEC gap-fill wrapper completed $(Get-Date -Format o)\"",
            "}",
            "finally {",
            "    Stop-Transcript | Out-Null",
            "}",
        ]
    )
    script_path.write_text("\n".join(lines), encoding="utf-8")
    manifest = script_path.with_suffix(".json")
    manifest.write_text(
        json.dumps(
            {
                "created_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                "start_date": min(plan.start_date for plan in plans).isoformat(),
                "end_date": max(plan.end_date for plan in plans).isoformat(),
                "commands": [plan.command for plan in plans],
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


def run_plan_script(script_path: Path, *, cwd: Path | None = None) -> subprocess.Popen[bytes]:
    return subprocess.Popen(["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script_path)], cwd=str(cwd or REPO_ROOT))


def format_command(command: Iterable[str]) -> str:
    return " ".join(shlex.quote(str(part)) for part in command)


def powershell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"
