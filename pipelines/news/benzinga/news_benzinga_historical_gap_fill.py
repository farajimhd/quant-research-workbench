from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.mlops.env import discover_env_files, load_env_files, secret_status  # noqa: E402


DEFAULT_RAW_ROOT_WIN = Path("D:/market-data/news-benzinga")
DEFAULT_PREPARED_ROOT_WIN = Path("D:/market-data/prepared")
DEFAULT_URL_DOWNLOAD_ARTIFACT_ROOT_WIN = Path("D:/market-data/news_benzinga_url_download_artifacts")
DEFAULT_PARTS_ROOT_WIN = Path("D:/market-data")
DEFAULT_PARTS_ROOT_CH = "/mnt/d/market-data"
DEFAULT_RUNTIME_ROOT = REPO_ROOT


@dataclass(frozen=True, slots=True)
class StageResult:
    stage: str
    command: list[str]
    status: str
    started_at_utc: str
    finished_at_utc: str
    wall_seconds: float
    returncode: int
    exception: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the historical Benzinga news gap-fill pipeline in the same order "
            "as the successful manual backfill: raw download, URL inventory, fetch "
            "plan, URL download, normalized row build, ClickHouse ingest, and "
            "ticker-link rebuild."
        )
    )
    parser.add_argument("--start-utc", required=True, help="Inclusive UTC start, e.g. 2026-06-01 or 2026-06-01T00:00:00Z.")
    parser.add_argument("--end-utc", required=True, help="Exclusive UTC end, e.g. 2026-06-02 or 2026-06-02T00:00:00Z.")
    parser.add_argument("--runtime-root", default=os.environ.get("QWB_PIPELINE_RUNTIME_ROOT") or str(DEFAULT_RUNTIME_ROOT))
    parser.add_argument("--raw-root-win", default=os.environ.get("NEWS_BENZINGA_RAW_ROOT_WIN") or str(DEFAULT_RAW_ROOT_WIN))
    parser.add_argument("--prepared-root-win", default=os.environ.get("NEWS_BENZINGA_PREPARED_ROOT_WIN") or str(DEFAULT_PREPARED_ROOT_WIN))
    parser.add_argument("--url-download-artifact-root-win", default=os.environ.get("NEWS_BENZINGA_URL_DOWNLOAD_ARTIFACT_ROOT_WIN") or str(DEFAULT_URL_DOWNLOAD_ARTIFACT_ROOT_WIN))
    parser.add_argument("--parts-root-win", default=os.environ.get("NEWS_BENZINGA_PARTS_ROOT_WIN") or str(DEFAULT_PARTS_ROOT_WIN))
    parser.add_argument("--parts-root-ch", default=os.environ.get("NEWS_BENZINGA_PARTS_ROOT_CH") or DEFAULT_PARTS_ROOT_CH)
    parser.add_argument("--download-processes", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_DOWNLOAD_PROCESSES", "32")))
    parser.add_argument("--inventory-processes", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_INVENTORY_PROCESSES", "32")))
    parser.add_argument("--inventory-chunk-size", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_INVENTORY_CHUNK_SIZE", "1000")))
    parser.add_argument("--fetch-plan-shards", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_FETCH_PLAN_SHARDS", "256")))
    parser.add_argument("--url-network-concurrency", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_URL_NETWORK_CONCURRENCY", "128")))
    parser.add_argument("--url-max-pending-futures", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_URL_MAX_PENDING", "512")))
    parser.add_argument("--normalizer-processes", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_NORMALIZER_PROCESSES", "32")))
    parser.add_argument("--inline-extraction-processes", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_INLINE_EXTRACTION_PROCESSES", "32")))
    parser.add_argument("--max-pending-futures", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_NORMALIZER_MAX_PENDING", "96")))
    parser.add_argument("--text-limit-chars", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_TEXT_LIMIT_CHARS", "50000")))
    parser.add_argument("--max-enriched-text-chars-per-url", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_MAX_ENRICHED_TEXT_CHARS_PER_URL", "24000")))
    parser.add_argument("--max-enriched-urls-per-article", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_MAX_ENRICHED_URLS_PER_ARTICLE", "5")))
    parser.add_argument("--rows-per-file", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_ROWS_PER_FILE", "100000")))
    parser.add_argument("--max-output-file-bytes", type=int, default=int(os.environ.get("NEWS_BENZINGA_GAP_FILL_MAX_OUTPUT_FILE_BYTES", "268435456")))
    parser.add_argument("--from-stage", default=os.environ.get("NEWS_BENZINGA_GAP_FILL_FROM_STAGE") or "raw_download", choices=stage_names())
    parser.add_argument("--to-stage", default=os.environ.get("NEWS_BENZINGA_GAP_FILL_TO_STAGE") or "ticker_links", choices=stage_names())
    parser.add_argument("--execute-db", action="store_true", help="Run ClickHouse insert and ticker-link rebuild. Without this, DB stages stop after preflight/dry-run.")
    parser.add_argument("--yes", action="store_true", help="Required to run commands. Without this, only prints the plan.")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--skip-url-download", action="store_true", help="Skip external URL download; normalization will use existing artifacts/body text only.")
    return parser.parse_args()


def main() -> None:
    loaded_env_files = load_env_files(discover_env_files(REPO_ROOT))
    args = parse_args()
    run_id = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    prepared_root = Path(args.prepared_root_win)
    run_root = prepared_root / "benzinga_news_historical_gap_fill" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_root / "benzinga_news_historical_gap_fill_manifest.json"
    stage_log_path = run_root / "benzinga_news_historical_gap_fill_stages.jsonl"

    scoped_raw_root = run_root / "raw_scope"
    plan = build_stage_plan(args, scoped_raw_root=scoped_raw_root)
    selected_plan = select_stage_range(plan, args.from_stage, args.to_stage)
    if args.skip_url_download:
        selected_plan = [item for item in selected_plan if item[0] != "url_download"]

    print("=" * 96, flush=True)
    print("Benzinga historical gap-fill orchestrator", flush=True)
    print(f"run_id={run_id}", flush=True)
    print(f"start_utc={args.start_utc} end_utc={args.end_utc}", flush=True)
    print(f"runtime_root={Path(args.runtime_root)}", flush=True)
    print(f"raw_root_win={args.raw_root_win}", flush=True)
    print(f"prepared_root_win={args.prepared_root_win}", flush=True)
    print(f"execute_db={args.execute_db} yes={args.yes}", flush=True)
    print(f"run_root={run_root}", flush=True)
    print(f"loaded_env_files={[str(path) for path in loaded_env_files]}", flush=True)
    print("secret_status=" + json.dumps(secret_status(["MASSIVE_API_KEY", "REAL_LIVE_CLICKHOUSE_WRITE_URL", "CLICKHOUSE_LIVE_STORAGE_POLICY"]), sort_keys=True), flush=True)
    print("=" * 96, flush=True)

    write_manifest(
        manifest_path,
        {
            "run_id": run_id,
            "created_at_utc": datetime.now(UTC).isoformat(),
            "start_utc": args.start_utc,
            "end_utc": args.end_utc,
            "run_root": str(run_root),
            "loaded_env_files": [str(path) for path in loaded_env_files],
            "args": vars(args),
            "planned_stages": [{"stage": stage, "command": command} for stage, command in selected_plan],
        },
    )

    print_plan(selected_plan)
    if not args.yes:
        print("plan_only=1; pass --yes to run stages", flush=True)
        return

    env = os.environ.copy()
    env["NEWS_BENZINGA_ARTIFACT_ROOT_WIN"] = str(Path(args.raw_root_win))
    env["NEWS_BENZINGA_RAW_ROOT_WIN"] = str(Path(args.raw_root_win))

    results: list[StageResult] = []
    for stage, command in selected_plan:
        if stage == "scope_raw_files":
            result = scope_raw_files_stage(
                stage,
                command,
                source_raw_root=Path(args.raw_root_win) / "raw",
                scoped_raw_root=scoped_raw_root,
                start_utc=args.start_utc,
                end_utc=args.end_utc,
            )
        else:
            result = run_stage(stage, command, env=env)
        results.append(result)
        append_jsonl(stage_log_path, asdict(result))
        if result.status != "ok" and not args.continue_on_error:
            write_manifest(manifest_path, {"completed_at_utc": datetime.now(UTC).isoformat(), "status": "failed", "results": [asdict(item) for item in results]}, append=True)
            raise SystemExit(result.returncode or 1)

    status = "ok" if all(item.status == "ok" for item in results) else "failed"
    write_manifest(manifest_path, {"completed_at_utc": datetime.now(UTC).isoformat(), "status": status, "results": [asdict(item) for item in results]}, append=True)
    print("=" * 96, flush=True)
    print(f"DONE status={status} manifest={manifest_path}", flush=True)
    print("=" * 96, flush=True)


def stage_names() -> list[str]:
    return [
        "raw_download",
        "scope_raw_files",
        "url_inventory",
        "url_fetch_plan",
        "url_download",
        "build_normalized_rows",
        "clickhouse_preflight",
        "clickhouse_ingest",
        "ticker_links",
    ]


def build_stage_plan(args: argparse.Namespace, *, scoped_raw_root: Path) -> list[tuple[str, list[str]]]:
    runtime_root = Path(args.runtime_root)
    prepared_root = Path(args.prepared_root_win)
    inventory_root = prepared_root / "benzinga_news_url_inventory"
    fetch_plan_root = prepared_root / "benzinga_news_url_fetch_plan"
    url_download_root = prepared_root / "benzinga_news_url_download"
    url_extraction_root = prepared_root / "benzinga_news_url_extraction"
    normalized_root = prepared_root / "benzinga_news_normalized_rows"

    python = sys.executable
    script = lambda name: str(runtime_root / "pipelines" / "news" / "benzinga" / name)  # noqa: E731

    plan: list[tuple[str, list[str]]] = [
        (
            "raw_download",
            [
                python,
                script("news_benzinga_raw_download.py"),
                "--start-utc",
                args.start_utc,
                "--end-utc",
                args.end_utc,
                "--download-processes",
                str(args.download_processes),
            ],
        ),
        (
            "scope_raw_files",
            [
                "internal:scope_raw_files",
                "--source-raw-root",
                str(Path(args.raw_root_win) / "raw"),
                "--scoped-raw-root",
                str(scoped_raw_root),
                "--start-utc",
                args.start_utc,
                "--end-utc",
                args.end_utc,
            ],
        ),
        (
            "url_inventory",
            [
                python,
                script("news_benzinga_url_inventory.py"),
                "--raw-root-win",
                str(scoped_raw_root),
                "--output-root-win",
                str(inventory_root),
                "--processes",
                str(args.inventory_processes),
                "--chunk-size",
                str(args.inventory_chunk_size),
            ],
        ),
        (
            "url_fetch_plan",
            [
                python,
                script("news_benzinga_url_fetch_plan.py"),
                "--inventory-root-win",
                str(inventory_root),
                "--output-root-win",
                str(fetch_plan_root),
                "--shards",
                str(args.fetch_plan_shards),
                "--progress-interval",
                "1000000",
            ],
        ),
        (
            "url_download",
            [
                python,
                script("news_benzinga_url_download.py"),
                "--fetch-plan-root-win",
                str(fetch_plan_root),
                "--output-root-win",
                str(url_download_root),
                "--artifact-root-win",
                args.url_download_artifact_root_win,
                "--network-concurrency",
                str(args.url_network_concurrency),
                "--max-pending-futures",
                str(args.url_max_pending_futures),
                "--per-domain-min-interval-seconds",
                "0.02",
                "--timeout-seconds",
                "5",
                "--max-retries",
                "0",
                "--progress-interval",
                "5000",
                "--heartbeat-seconds",
                "15",
                "--flush-interval",
                "500",
                "--resume",
            ],
        ),
        (
            "build_normalized_rows",
            [
                python,
                script("news_benzinga_build_normalized_rows.py"),
                "--raw-root-win",
                str(scoped_raw_root / "raw"),
                "--fetch-plan-root-win",
                str(fetch_plan_root),
                "--download-root-win",
                str(url_download_root),
                "--extraction-root-win",
                str(url_extraction_root),
                "--output-root-win",
                str(normalized_root),
                "--processes",
                str(args.normalizer_processes),
                "--max-pending-futures",
                str(args.max_pending_futures),
                "--inline-extraction-processes",
                str(args.inline_extraction_processes),
                "--text-limit-chars",
                str(args.text_limit_chars),
                "--max-enriched-text-chars-per-url",
                str(args.max_enriched_text_chars_per_url),
                "--max-enriched-urls-per-article",
                str(args.max_enriched_urls_per_article),
                "--rows-per-file",
                str(args.rows_per_file),
                "--max-output-file-bytes",
                str(args.max_output_file_bytes),
                "--progress-interval",
                "25000",
                "--inline-extraction-progress-interval",
                "5000",
                "--flush-interval",
                "1000",
            ],
        ),
        (
            "clickhouse_preflight",
            [
                python,
                script("news_benzinga_clickhouse_file_ingest.py"),
                "--manifest-root-win",
                str(normalized_root),
                "--parts-root-win",
                args.parts_root_win,
                "--parts-root-ch",
                args.parts_root_ch,
                "--preflight-only",
            ],
        ),
    ]
    ingest_command = [
        python,
        script("news_benzinga_clickhouse_file_ingest.py"),
        "--manifest-root-win",
        str(normalized_root),
        "--parts-root-win",
        args.parts_root_win,
        "--parts-root-ch",
        args.parts_root_ch,
    ]
    if args.execute_db:
        ingest_command.append("--execute")
    plan.append(("clickhouse_ingest", ingest_command))

    ticker_command = [
        python,
        script("news_benzinga_ticker_links.py"),
    ]
    if args.execute_db:
        ticker_command.extend(["--execute", "--rebuild"])
    plan.append(("ticker_links", ticker_command))
    return plan


def select_stage_range(plan: list[tuple[str, list[str]]], from_stage: str, to_stage: str) -> list[tuple[str, list[str]]]:
    names = [stage for stage, _ in plan]
    start = names.index(from_stage)
    end = names.index(to_stage)
    if end < start:
        raise SystemExit("--to-stage must be the same as or after --from-stage")
    return plan[start : end + 1]


def print_plan(plan: list[tuple[str, list[str]]]) -> None:
    print("planned_commands:", flush=True)
    for index, (stage, command) in enumerate(plan, start=1):
        print(f"{index}. {stage}: {format_command(command)}", flush=True)


def run_stage(stage: str, command: list[str], *, env: dict[str, str]) -> StageResult:
    started = time.perf_counter()
    started_at = datetime.now(UTC)
    print("=" * 96, flush=True)
    print(f"STAGE START {stage}", flush=True)
    print(format_command(command), flush=True)
    print("=" * 96, flush=True)
    status = "ok"
    exception = ""
    returncode = 0
    try:
        completed = subprocess.run(command, cwd=str(REPO_ROOT), env=env, check=False)
        returncode = int(completed.returncode)
        if returncode != 0:
            status = "failed"
            exception = f"returncode={returncode}"
    except KeyboardInterrupt:
        status = "interrupted"
        returncode = 130
        exception = "KeyboardInterrupt"
        raise
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        returncode = 1
        exception = repr(exc)
    finished_at = datetime.now(UTC)
    result = StageResult(
        stage=stage,
        command=command,
        status=status,
        started_at_utc=started_at.isoformat(),
        finished_at_utc=finished_at.isoformat(),
        wall_seconds=round(time.perf_counter() - started, 3),
        returncode=returncode,
        exception=exception,
    )
    print("=" * 96, flush=True)
    print(f"STAGE END {stage} status={status} returncode={returncode} elapsed_seconds={result.wall_seconds}", flush=True)
    print("=" * 96, flush=True)
    return result


def scope_raw_files_stage(stage: str, command: list[str], *, source_raw_root: Path, scoped_raw_root: Path, start_utc: str, end_utc: str) -> StageResult:
    started = time.perf_counter()
    started_at = datetime.now(UTC)
    print("=" * 96, flush=True)
    print(f"STAGE START {stage}", flush=True)
    print(format_command(command), flush=True)
    print("=" * 96, flush=True)
    status = "ok"
    exception = ""
    returncode = 0
    files_seen = 0
    files_linked = 0
    files_copied = 0
    files_skipped = 0
    try:
        if not source_raw_root.exists():
            raise FileNotFoundError(f"source raw root does not exist: {source_raw_root}")
        start_dt = parse_utc(start_utc)
        end_dt = parse_utc(end_utc)
        if end_dt <= start_dt:
            raise ValueError("--end-utc must be after --start-utc")
        for day in iter_days(start_dt, end_dt):
            source_day = source_raw_root / day.strftime("%Y") / day.strftime("%m") / day.strftime("%d")
            if not source_day.exists():
                continue
            for source_file in sorted(source_day.glob("*.json")):
                files_seen += 1
                if not raw_file_in_range(source_file, start_dt, end_dt):
                    files_skipped += 1
                    continue
                relative = source_file.relative_to(source_raw_root.parent)
                target_file = scoped_raw_root / relative
                target_file.parent.mkdir(parents=True, exist_ok=True)
                if target_file.exists():
                    files_skipped += 1
                    continue
                try:
                    os.link(source_file, target_file)
                    files_linked += 1
                except OSError:
                    shutil.copy2(source_file, target_file)
                    files_copied += 1
                done = files_linked + files_copied
                if done and done % 100000 == 0:
                    print(
                        f"scope_progress seen={files_seen:,} linked={files_linked:,} copied={files_copied:,} skipped={files_skipped:,}",
                        flush=True,
                    )
    except Exception as exc:  # noqa: BLE001
        status = "failed"
        returncode = 1
        exception = repr(exc)
    finished_at = datetime.now(UTC)
    result = StageResult(
        stage=stage,
        command=command,
        status=status,
        started_at_utc=started_at.isoformat(),
        finished_at_utc=finished_at.isoformat(),
        wall_seconds=round(time.perf_counter() - started, 3),
        returncode=returncode,
        exception=exception,
    )
    print(
        f"scope_raw_files seen={files_seen:,} linked={files_linked:,} copied={files_copied:,} skipped={files_skipped:,} "
        f"scoped_raw_root={scoped_raw_root}",
        flush=True,
    )
    print("=" * 96, flush=True)
    print(f"STAGE END {stage} status={status} returncode={returncode} elapsed_seconds={result.wall_seconds}", flush=True)
    print("=" * 96, flush=True)
    return result


def parse_utc(value: str) -> datetime:
    text = value.strip()
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        text += "T00:00:00Z"
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iter_days(start_dt: datetime, end_dt: datetime) -> list[datetime]:
    current = datetime(start_dt.year, start_dt.month, start_dt.day, tzinfo=UTC)
    final = datetime(end_dt.year, end_dt.month, end_dt.day, tzinfo=UTC)
    days: list[datetime] = []
    while current <= final:
        days.append(current)
        current += timedelta(days=1)
    return days


def raw_file_in_range(path: Path, start_dt: datetime, end_dt: datetime) -> bool:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        published_raw = str(payload.get("published") or "")
        if not published_raw:
            return True
        published_at = parse_utc(published_raw)
        return start_dt <= published_at < end_dt
    except Exception:
        return True


def format_command(command: list[str]) -> str:
    return " ".join(quote_arg(part) for part in command)


def quote_arg(value: str) -> str:
    text = str(value)
    if not text or any(char.isspace() for char in text):
        return '"' + text.replace('"', '\\"') + '"'
    return text


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def write_manifest(path: Path, payload: dict[str, Any], *, append: bool = False) -> None:
    if append and path.exists():
        current = json.loads(path.read_text(encoding="utf-8"))
        current.update(payload)
        payload = current
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
