"""SSH dispatch into the logged-in workstation desktop, with a visible console.

Use only a hash-verified committed v3 deployment. A uniquely named scheduled
task uses InteractiveToken; SSH alone would put a console in a hidden session.
Source/configuration is immutable and task/process ownership is recorded.
"""

import os
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

import argparse
import base64
import subprocess
from pathlib import Path
from uuid import uuid4


def ps_literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--checkout", required=True, help="Verified D:/TradingML/codes v3 deployment"
    )
    p.add_argument(
        "--job", required=True, help="Task-owned D:/TradingML/runtimes/... job"
    )
    p.add_argument("--python", default="C:/Users/Mehdi/miniconda3/envs/ml4t/python.exe")
    p.add_argument("--host", default="mehdi@DESKTOP-SAAI85T")
    p.add_argument(
        "--key", type=Path, default=Path.home() / ".ssh/id_ed25519_codex_workstation"
    )
    p.add_argument(
        "--command", choices=("dates", "plan", "profile", "run"), required=True
    )
    p.add_argument("arguments", nargs=argparse.REMAINDER)
    args = p.parse_args(argv)
    extra = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    checkout = args.checkout.replace("\\", "/").rstrip("/")
    job = args.job.replace("\\", "/").rstrip("/")
    if not checkout.startswith(
        "D:/TradingML/codes/quant-research-workbench-squeeze-v3-"
    ) or not job.startswith(
        "D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v3/"
    ):
        raise ValueError("Remote launch must use task-owned v3 code/runtime roots")
    if ".." in Path(checkout).parts or ".." in Path(job).parts:
        raise ValueError("Parent path components are forbidden")
    token = uuid4().hex
    task = "Codex-v3-optimization-" + token
    script = (
        checkout
        + "/research/vectorized_backtest/v3/torch_backtest/run_optimization_workstation.py"
    )
    command = [args.command, "--resume", job, *extra]
    body = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$env:PYTHONDONTWRITEBYTECODE = '1'",
            "$env:PYTHONUNBUFFERED = '1'",
            "$env:PYTHONIOENCODING = 'utf-8'",
            "$Host.UI.RawUI.WindowTitle = 'GPU STRATEGY SEARCH - v3'",
            "Set-Location -LiteralPath " + ps_literal(checkout),
            "& "
            + ps_literal(args.python)
            + " -B "
            + ps_literal(script)
            + " @("
            + ",".join(ps_literal(v) for v in command)
            + ")",
            "$workerExit = $LASTEXITCODE",
            "@{ exit_code=$workerExit; finished_utc=[DateTime]::UtcNow.ToString('o') } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath "
            + ps_literal(job + "/exit.json"),
            "exit $workerExit",
        ]
    )
    body64 = base64.b64encode(body.encode("utf-8")).decode()
    # Task Scheduler may give an interactive task a hidden inherited console.
    # Start-Process on Windows opens a NEW normal console, while the dispatcher
    # waits and retains ownership/exit status. No hidden-window flag is used.
    dispatch = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$child = Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoLogo','-NoProfile','-ExecutionPolicy','Bypass','-File', "
            + ps_literal('"' + job + '/visible-launch.ps1"')
            + ") -WindowStyle Normal -Wait -PassThru",
            "exit $child.ExitCode",
        ]
    )
    dispatch64 = base64.b64encode(dispatch.encode("utf-8")).decode()
    remote = "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            "$ProgressPreference = 'SilentlyContinue'",
            "$job = " + ps_literal(job),
            "$checkout = " + ps_literal(checkout),
            "if (!(Test-Path -LiteralPath $checkout)) { throw 'Verified v3 deployment missing' }",
            "if (!(Test-Path -LiteralPath 'D:/TradingML/runtimes')) { throw 'Required runtime root missing' }",
            "if (!(Get-CimInstance Win32_LogonSession | Where-Object { $_.LogonType -in 2,10 })) { throw 'No interactive desktop login' }",
            "New-Item -ItemType Directory -Force -Path $job | Out-Null",
            "$script = Join-Path $job 'visible-launch.ps1'",
            "if (Test-Path -LiteralPath $script) { throw 'Job already has a launch owner; inspect before restarting' }",
            "[IO.File]::WriteAllBytes($script, [Convert]::FromBase64String("
            + ps_literal(body64)
            + "))",
            "$dispatcher = Join-Path $job 'visible-dispatch.ps1'",
            "[IO.File]::WriteAllBytes($dispatcher, [Convert]::FromBase64String("
            + ps_literal(dispatch64)
            + "))",
            "$taskName = " + ps_literal(task),
            "$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited",
            "$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoLogo -NoProfile -ExecutionPolicy Bypass -File ' + [char]34 + $dispatcher + [char]34)",
            "$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Days 7) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries",
            "Register-ScheduledTask -TaskName $taskName -Action $action -Principal $principal -Settings $settings | Out-Null",
            "@{ task=$taskName; checkout=$checkout; job=$job; created_utc=[DateTime]::UtcNow.ToString('o'); transport='ssh+InteractiveToken' } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $job 'launch.json') -Encoding UTF8",
            "Start-ScheduledTask -TaskName $taskName",
            "Write-Output ('Launched visible workstation console: ' + $taskName)",
            "Write-Output ('Run: ' + $job)",
        ]
    )
    # Windows OpenSSH's cmd.exe has an 8191-character command limit. Send the
    # whole script on stdin; only this short UTF-8 bootstrap is on the command
    # line. Execute one script block so a failed preflight cannot continue into
    # later registration/start statements.
    bootstrap = "[Console]::InputEncoding=[Text.Encoding]::UTF8; & ([scriptblock]::Create([Console]::In.ReadToEnd()))"
    encoded = base64.b64encode(bootstrap.encode("utf-16le")).decode()
    ssh = "C:/Windows/System32/OpenSSH/ssh.exe"
    subprocess.run(
        [
            ssh,
            "-i",
            str(args.key),
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            args.host,
            "powershell",
            "-NoProfile",
            "-EncodedCommand",
            encoded,
        ],
        input=remote,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
