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
        "$secCondaEnv = if ($env:SEC_GATEWAY_WORKSTATION_CONDA_ENV) { $env:SEC_GATEWAY_WORKSTATION_CONDA_ENV } else { 'ml4t' }",
        "",
        "function Resolve-SecPython {",
        "    param([string]$EnvName)",
        "    if ($env:SEC_GATEWAY_WORKSTATION_PYTHON_EXE -and (Test-Path -LiteralPath $env:SEC_GATEWAY_WORKSTATION_PYTHON_EXE)) {",
        "        return $env:SEC_GATEWAY_WORKSTATION_PYTHON_EXE",
        "    }",
        "    $candidates = New-Object System.Collections.Generic.List[string]",
        "    if ($env:CONDA_PREFIX -and $env:CONDA_DEFAULT_ENV -eq $EnvName) {",
        "        $candidates.Add((Join-Path $env:CONDA_PREFIX 'python.exe'))",
        "    }",
        "    $profileRoots = @($env:USERPROFILE, 'C:\\Users\\Mehdi', 'C:\\Users\\g835l') | Where-Object { $_ } | Select-Object -Unique",
        "    foreach ($root in $profileRoots) {",
        "        $candidates.Add((Join-Path $root \"miniconda3\\envs\\$EnvName\\python.exe\"))",
        "        $candidates.Add((Join-Path $root \"anaconda3\\envs\\$EnvName\\python.exe\"))",
        "    }",
        "    foreach ($candidate in $candidates) {",
        "        if ($candidate -and (Test-Path -LiteralPath $candidate)) {",
        "            return $candidate",
        "        }",
        "    }",
        "    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue",
        "    if ($pythonCommand -and $pythonCommand.Source) {",
        "        return $pythonCommand.Source",
        "    }",
        "    throw \"Unable to resolve Python. Set SEC_GATEWAY_WORKSTATION_PYTHON_EXE or SEC_GATEWAY_WORKSTATION_CONDA_ENV.\"",
        "}",
        "",
        "function Format-SecCommand {",
        "    param([string[]]$Command)",
        "    $parts = foreach ($part in $Command) {",
        "        if ($part -match '\\s') { '\"' + ($part -replace '\"', '\\\"') + '\"' } else { $part }",
        "    }",
        "    return ($parts -join ' ')",
        "}",
        "",
        "function Invoke-SecHistoricalCommand {",
        "    param([string]$TaskName, [string[]]$Command, [string]$PythonPath)",
        "    if (-not $Command -or $Command.Count -lt 1) {",
        "        throw \"$TaskName has an empty command.\"",
        "    }",
        "    if ($Command[0] -eq 'python' -or $Command[0] -match '(?i)(^|\\\\)python(\\.exe)?$') {",
        "        $Command[0] = $PythonPath",
        "    }",
        "    Write-Host (\"$TaskName command: \" + (Format-SecCommand -Command $Command))",
        "    if ($Command.Count -eq 1) {",
        "        & $Command[0]",
        "    } else {",
        "        & $Command[0] @($Command[1..($Command.Count - 1)])",
        "    }",
        "    $exitCode = if ($null -eq $global:LASTEXITCODE) { 0 } else { $global:LASTEXITCODE }",
        "    if ($exitCode -ne 0) {",
        "        throw \"$TaskName failed with exit code $exitCode\"",
        "    }",
        "}",
        "",
        "Write-Host \"SEC gap-fill wrapper started $(Get-Date -Format o)\"",
        "Write-Host \"Script: $PSCommandPath\"",
        "Write-Host \"Log: $secGapFillLog\"",
        "$secPython = Resolve-SecPython -EnvName $secCondaEnv",
        "Write-Host \"Python: $secPython\"",
        "Start-Transcript -Path $secGapFillLog -Append | Out-Null",
        "try {",
        "",
    ]
    for index, plan in enumerate(plans, start=1):
        task_name = f"SEC historical task {index}/{len(plans)}"
        lines.append(f"    Write-Host {powershell_single_quote(f'{task_name} started')}")
        lines.append(f"    $secTaskCommand = {powershell_array(plan.command)}")
        lines.append(f"    Invoke-SecHistoricalCommand -TaskName {powershell_single_quote(task_name)} -Command $secTaskCommand -PythonPath $secPython")
        lines.append(f"    Write-Host {powershell_single_quote(f'{task_name} completed')}")
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


def powershell_array(values: Iterable[str]) -> str:
    return "@(" + ", ".join(powershell_single_quote(str(value)) for value in values) + ")"
