from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_WANDB_PROJECT = "June2026-event-token-mae"
DEFAULT_RUN_NAME = "v16-v12mlp-v11tokenemb-f1-bs8192-10shards-after-v12"
DEFAULT_MATCH_TERMS = ("masked_event_model", "v12", "train_10shard_long")


@dataclass(frozen=True)
class ProcessMatch:
    process_id: int
    command_line: str


def repo_root() -> Path:
    return next(
        (parent for parent in Path(__file__).resolve().parents if (parent / "research").exists()),
        Path(__file__).resolve().parents[3],
    )


def default_target_script() -> Path:
    return Path(__file__).resolve().with_name("train_10shard_long.py")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Wait for an active v12 train_10shard_long process to finish, then "
            "replace this launcher with v16 train_10shard_long in the same terminal. "
            "Because this uses os.execv for the handoff, Rich output and Ctrl+C behave "
            "like a direct training run instead of a log-capturing wrapper."
        )
    )
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--wandb-project", default=DEFAULT_WANDB_PROJECT)
    parser.add_argument("--run-name", default=DEFAULT_RUN_NAME)
    parser.add_argument("--target-script", type=Path, default=default_target_script())
    parser.add_argument("--amp-dtype", choices=("auto", "bf16", "fp16"), default="bf16")
    parser.add_argument("--fresh-start", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--require-active-v12",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="If set, exit instead of starting immediately when no matching v12 process is found.",
    )
    parser.add_argument(
        "--match-term",
        action="append",
        dest="match_terms",
        default=None,
        help=(
            "Term that must appear in the watched process command line. Repeat this "
            "flag to override the default terms."
        ),
    )
    parser.add_argument(
        "--no-wait",
        action="store_true",
        help="Skip process waiting and immediately exec the target training script.",
    )
    parser.add_argument(
        "--print-command-only",
        action="store_true",
        help="Print the exact target command and exit without waiting or training.",
    )
    known, extra = parser.parse_known_args()
    known.extra_train_args = extra
    return known


def list_windows_processes() -> list[ProcessMatch]:
    """Return process command lines without requiring psutil.

    PowerShell is only used during the waiting phase. The actual training handoff
    does not use subprocess; it replaces this Python process so the training
    program directly owns the terminal.
    """

    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.CommandLine } | "
        "Select-Object ProcessId,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise RuntimeError(f"Could not list Windows processes via PowerShell: {stderr or result.returncode}")
    text = result.stdout.strip()
    if not text:
        return []
    payload = json.loads(text)
    if isinstance(payload, dict):
        payload = [payload]
    processes: list[ProcessMatch] = []
    for row in payload:
        try:
            process_id = int(row.get("ProcessId"))
            command_line = str(row.get("CommandLine") or "")
        except Exception:  # noqa: BLE001
            continue
        processes.append(ProcessMatch(process_id=process_id, command_line=command_line))
    return processes


def find_matching_processes(match_terms: tuple[str, ...]) -> list[ProcessMatch]:
    lowered_terms = tuple(term.lower() for term in match_terms if term)
    current_pid = os.getpid()
    matches: list[ProcessMatch] = []
    for process in list_windows_processes():
        if process.process_id == current_pid:
            continue
        command_line = process.command_line.lower()
        if all(term in command_line for term in lowered_terms):
            matches.append(process)
    return matches


def build_target_argv(args: argparse.Namespace) -> list[str]:
    target_script = Path(args.target_script).resolve()
    argv = [
        sys.executable,
        str(target_script),
        "--wandb-project",
        str(args.wandb_project),
        "--run-name",
        str(args.run_name),
        "--amp-dtype",
        str(args.amp_dtype),
    ]
    if bool(args.fresh_start):
        argv.append("--fresh-start")
    argv.extend(args.extra_train_args)
    return argv


def wait_for_v12(match_terms: tuple[str, ...], poll_seconds: float, *, require_active: bool) -> None:
    seen_active = False
    started_at = time.monotonic()
    while True:
        matches = find_matching_processes(match_terms)
        if not matches:
            if require_active and not seen_active:
                terms = ", ".join(match_terms)
                raise SystemExit(f"No active v12 training process matched terms: {terms}")
            elapsed_minutes = (time.monotonic() - started_at) / 60.0
            print(f"No matching v12 process remains. Wait elapsed {elapsed_minutes:.1f} minutes.", flush=True)
            return
        seen_active = True
        elapsed_minutes = (time.monotonic() - started_at) / 60.0
        print(
            f"Waiting for v12 training to finish: {len(matches)} match(es), "
            f"elapsed={elapsed_minutes:.1f} min, next_check={poll_seconds:.0f}s",
            flush=True,
        )
        for process in matches[:3]:
            print(f"  pid={process.process_id} command={short_command(process.command_line)}", flush=True)
        time.sleep(max(1.0, poll_seconds))


def short_command(command_line: str, max_chars: int = 220) -> str:
    one_line = " ".join(command_line.split())
    if len(one_line) <= max_chars:
        return one_line
    return one_line[: max_chars - 3] + "..."


def main() -> None:
    args = parse_args()
    target_script = Path(args.target_script).resolve()
    if not target_script.exists():
        raise SystemExit(f"Target training script does not exist: {target_script}")

    match_terms = tuple(args.match_terms or DEFAULT_MATCH_TERMS)
    target_argv = build_target_argv(args)
    print("=" * 100, flush=True)
    print("v16 after-v12 foreground handoff", flush=True)
    print(f"watch_terms={match_terms}", flush=True)
    print(f"poll_seconds={args.poll_seconds}", flush=True)
    print(f"target_script={target_script}", flush=True)
    print("target_command:", flush=True)
    print(" ".join(target_argv), flush=True)
    print("=" * 100, flush=True)
    if args.print_command_only:
        return

    if not args.no_wait:
        try:
            wait_for_v12(match_terms, float(args.poll_seconds), require_active=bool(args.require_active_v12))
        except KeyboardInterrupt:
            print("Interrupted while waiting. No training was started.", flush=True)
            raise SystemExit(130) from None

    os.chdir(repo_root())
    print("Starting target training by replacing this process; Rich should take over this terminal.", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, target_argv)


if __name__ == "__main__":
    main()
