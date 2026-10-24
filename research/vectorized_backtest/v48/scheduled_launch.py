"""Run a pinned runtime entry through Task Scheduler, outside the app process tree.

Interactive-token tasks survive app closure/updates while the user stays logged in.
This deliberately does not implement reboot recovery or automatic optimizer retries.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import argparse
import base64
import json
from pathlib import Path
import runpy
import subprocess
import sys


def install(task, python, entry, working_directory, arguments, launcher):
    for path in (python, entry, launcher):
        if not Path(path).is_file():
            raise ValueError(f'Missing pinned file: {path}')
    if not Path(working_directory).is_dir():
        raise ValueError('Missing working directory')
    command = subprocess.list2cmdline([
        '-B', str(launcher), '--execute-runtime', str(entry), '--', *arguments,
    ])
    payload = base64.b64encode(json.dumps(dict(
        task=task, python=str(python), arguments=command,
        working_directory=str(working_directory),
    )).encode()).decode()
    script = """$ErrorActionPreference='Stop'
$x=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PAYLOAD')) | ConvertFrom-Json
if (Get-ScheduledTask -TaskName $x.task -ErrorAction SilentlyContinue) { throw 'Task already exists; inspect it before replacing' }
$a=New-ScheduledTaskAction -Execute $x.python -Argument $x.arguments -WorkingDirectory $x.working_directory
$p=New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$s=New-ScheduledTaskSettingsSet -Hidden -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $x.task -Action $a -Principal $p -Settings $s | Out-Null
Start-ScheduledTask -TaskName $x.task
Get-ScheduledTask -TaskName $x.task | Select-Object TaskName,State | ConvertTo-Json -Compress
""".replace('PAYLOAD', payload)
    encoded = base64.b64encode(script.encode('utf-16le')).decode()
    return subprocess.run(
        ['powershell', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        check=True, capture_output=True, text=True,
    ).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-runtime', type=Path)
    parser.add_argument('--task')
    parser.add_argument('--python', type=Path)
    parser.add_argument('--entry', type=Path)
    parser.add_argument('--working-directory', type=Path)
    parser.add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ['--'] else args.arguments
    if args.execute_runtime:
        # A file entry puts this package directory on sys.path. Its profile.py
        # must not shadow the stdlib profile imported lazily by PyTorch.
        package_directory = Path(__file__).resolve().parent
        sys.path[:] = [p for p in sys.path if Path(p or os.curdir).resolve() != package_directory]
        sys.argv = [str(args.execute_runtime), *arguments]
        runpy.run_path(str(args.execute_runtime), run_name='__main__')
        return
    if not all((args.task, args.python, args.entry, args.working_directory)):
        parser.error('--task, --python, --entry and --working-directory are required')
    print(install(args.task, args.python, args.entry, args.working_directory,
                  arguments, Path(__file__).resolve()), flush=True)


if __name__ == '__main__':
    main()
