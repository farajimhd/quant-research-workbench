from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from research.bar_gpt.v1.cohort import (
    BAR_GPT_IDENTITY_HOLDOUT_TICKERS,
    BAR_GPT_TRAINING_TICKERS,
)
from research.bar_gpt.v1.offline_shards import (
    DEFAULT_OUTPUT_ROOT,
    OFFLINE_SHARD_CONTRACT_VERSION,
    _atomic_json,
    _sha256,
    condition_positive_counts,
    load_shard,
)


@dataclass(frozen=True, slots=True)
class RepairCandidate:
    unit_key: str
    sidecar_path: Path
    shard_path: Path


@dataclass(frozen=True, slots=True)
class RepairResult:
    unit_key: str
    status: str
    condition_positive_counts: tuple[int, int, int, int] | None = None


class RepairRunLog:
    def __init__(self, root: Path, arguments: dict[str, Any]) -> None:
        now = dt.datetime.now().astimezone()
        run_id = f"{now:%Y%m%d-%H%M%S}-p{os.getpid()}-{time.time_ns() % 1_000_000:06d}"
        directory = root / "manifest" / "metadata_repairs"
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{run_id}.jsonl"
        self._handle = self.path.open("a", encoding="utf-8", buffering=1)
        self.record("run_started", arguments=arguments)

    def record(self, event: str, **fields: Any) -> None:
        value = {
            "timestamp": dt.datetime.now().astimezone().isoformat(timespec="microseconds"),
            "event": event,
            **fields,
        }
        self._handle.write(json.dumps(value, sort_keys=True, default=str) + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self) -> None:
        self._handle.close()


def _csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip().upper() for item in value.split(",") if item.strip()))


def _month_in_range(month: str, start_date: str, end_date: str) -> bool:
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    value = dt.date.fromisoformat(f"{month}-01")
    return start <= value < end


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    training_tickers = tuple(
        ticker for ticker in BAR_GPT_TRAINING_TICKERS
        if ticker not in BAR_GPT_IDENTITY_HOLDOUT_TICKERS
    )
    parser = argparse.ArgumentParser(
        description=(
            "Repair missing condition_positive_counts in certified BarGPT v2 sidecars "
            "from the immutable shard tensors."
        )
    )
    parser.add_argument("--execute", action="store_true", help="Required to update sidecars; omit for a read-only plan.")
    parser.add_argument("--root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--tickers", default=",".join(training_tickers))
    parser.add_argument("--start-date", default="2019-01-01")
    parser.add_argument("--end-date", default="2021-01-01")
    parser.add_argument(
        "--verify-sha256",
        action="store_true",
        help="Re-hash each tensor file before repair. This reads every byte and is intentionally opt-in.",
    )
    parser.add_argument("--max-shards", type=int, default=0, help="Bounded smoke limit; zero repairs every candidate.")
    args = parser.parse_args(list(argv) if argv is not None else None)
    start = dt.date.fromisoformat(str(args.start_date))
    end = dt.date.fromisoformat(str(args.end_date))
    if start.day != 1 or end.day != 1 or start >= end:
        parser.error("date bounds must be non-empty month boundaries")
    if args.max_shards < 0:
        parser.error("--max-shards cannot be negative")
    if not _csv(str(args.tickers)):
        parser.error("--tickers must contain at least one ticker")
    return args


def discover_candidates(
    root: Path,
    *,
    tickers: Sequence[str],
    start_date: str,
    end_date: str,
) -> tuple[tuple[RepairCandidate, ...], dict[str, int]]:
    ticker_root = root / "tickers"
    if not ticker_root.is_dir():
        raise RuntimeError(f"offline shard ticker root is absent: {ticker_root}")
    allowed = {ticker.upper() for ticker in tickers}
    candidates: list[RepairCandidate] = []
    summary = {"complete": 0, "already_repaired": 0, "covered_empty": 0, "other_status": 0}
    for sidecar_path in sorted(root.glob("tickers/*/*/*.json")):
        try:
            value = json.loads(sidecar_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"cannot read shard sidecar {sidecar_path}: {exc}") from exc
        unit_key = str(value.get("unit_key", ""))
        try:
            ticker, month = unit_key.split(":", 1)
        except ValueError as exc:
            raise RuntimeError(f"invalid unit_key in {sidecar_path}: {unit_key!r}") from exc
        if (
            sidecar_path.stem != month
            or sidecar_path.parent.name != month[:4]
            or sidecar_path.parent.parent.name.upper() != ticker.upper()
        ):
            raise RuntimeError(f"sidecar path disagrees with unit_key {unit_key}: {sidecar_path}")
        if ticker.upper() not in allowed or not _month_in_range(month, start_date, end_date):
            continue
        status = str(value.get("status", ""))
        if status == "covered_empty":
            summary["covered_empty"] += 1
            continue
        if status != "complete":
            summary["other_status"] += 1
            continue
        summary["complete"] += 1
        counts = value.get("condition_positive_counts")
        if counts is not None:
            if not isinstance(counts, list) or len(counts) != 4 or any(int(item) < 0 for item in counts):
                raise RuntimeError(f"invalid condition_positive_counts in {sidecar_path}: {counts!r}")
            summary["already_repaired"] += 1
            continue
        candidates.append(RepairCandidate(unit_key, sidecar_path, sidecar_path.with_suffix(".pt")))
    if not any(summary.values()):
        raise RuntimeError(
            f"no shard sidecars matched tickers={sorted(allowed)} range=[{start_date},{end_date})"
        )
    return tuple(candidates), summary


def repair_candidate(
    candidate: RepairCandidate,
    *,
    execute: bool,
    verify_sha256: bool,
) -> RepairResult:
    original_text = candidate.sidecar_path.read_text(encoding="utf-8")
    sidecar = json.loads(original_text)
    if int(sidecar.get("contract_version", -1)) != OFFLINE_SHARD_CONTRACT_VERSION:
        raise RuntimeError(f"unsupported sidecar contract for {candidate.unit_key}")
    if sidecar.get("status") != "complete" or sidecar.get("unit_key") != candidate.unit_key:
        raise RuntimeError(f"sidecar identity/status changed for {candidate.unit_key}")
    existing = sidecar.get("condition_positive_counts")
    if existing is not None:
        if not isinstance(existing, list) or len(existing) != 4:
            raise RuntimeError(f"invalid existing condition counts for {candidate.unit_key}")
        return RepairResult(candidate.unit_key, "already_repaired", tuple(int(item) for item in existing))
    if not candidate.shard_path.is_file():
        raise RuntimeError(f"missing tensor file for {candidate.unit_key}: {candidate.shard_path}")
    actual_bytes = candidate.shard_path.stat().st_size
    if int(sidecar.get("bytes", -1)) != actual_bytes:
        raise RuntimeError(
            f"tensor byte-size mismatch for {candidate.unit_key}: "
            f"sidecar={sidecar.get('bytes')} actual={actual_bytes}"
        )
    certified_sha256 = str(sidecar.get("sha256", ""))
    if len(certified_sha256) != 64 or any(character not in "0123456789abcdef" for character in certified_sha256.lower()):
        raise RuntimeError(f"missing certified SHA-256 for {candidate.unit_key}")
    if verify_sha256 and _sha256(candidate.shard_path) != certified_sha256:
        raise RuntimeError(f"tensor SHA-256 mismatch for {candidate.unit_key}")
    shard = load_shard(candidate.shard_path)
    if shard.get("unit_key") != candidate.unit_key:
        raise RuntimeError(f"tensor unit identity mismatch for {candidate.unit_key}")
    if shard.get("config_hash") != sidecar.get("config_hash"):
        raise RuntimeError(f"tensor/sidecar config hash mismatch for {candidate.unit_key}")
    sessions = shard["sessions"]
    structural_counts = {
        "sessions": len(sessions),
        "blocks": sum(len(session["blocks"]) for session in sessions),
        "origins": sum(
            int(block["origin_indices"].numel())
            for session in sessions for block in session["blocks"]
        ),
    }
    for name, actual in structural_counts.items():
        if int(sidecar.get(name, -1)) != actual:
            raise RuntimeError(
                f"tensor/sidecar {name} mismatch for {candidate.unit_key}: "
                f"sidecar={sidecar.get(name)} actual={actual}"
            )
    counts = condition_positive_counts(sessions)
    embedded = shard.get("counts", {}).get("condition_positive_counts")
    if embedded is not None and tuple(int(item) for item in embedded) != counts:
        raise RuntimeError(f"embedded condition counts disagree with tensors for {candidate.unit_key}")
    if not execute:
        return RepairResult(candidate.unit_key, "would_repair", counts)
    if candidate.sidecar_path.read_text(encoding="utf-8") != original_text:
        raise RuntimeError(f"sidecar changed during repair for {candidate.unit_key}; retry safely")
    updated = dict(sidecar)
    updated["condition_positive_counts"] = list(counts)
    updated["condition_positive_counts_repaired_at"] = dt.datetime.now().astimezone().isoformat(
        timespec="seconds"
    )
    updated["condition_positive_counts_source"] = "horizon_targets_and_mask_v1"
    _atomic_json(candidate.sidecar_path, updated)
    return RepairResult(candidate.unit_key, "repaired", counts)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    root = Path(args.root)
    candidates, catalog = discover_candidates(
        root,
        tickers=_csv(str(args.tickers)),
        start_date=str(args.start_date),
        end_date=str(args.end_date),
    )
    candidate_total = len(candidates)
    if args.max_shards:
        candidates = candidates[: int(args.max_shards)]
    print(
        "BarGPT condition-count repair: "
        f"root={root} complete={catalog['complete']} already={catalog['already_repaired']} "
        f"covered_empty={catalog['covered_empty']} other_status={catalog['other_status']} "
        f"candidates={candidate_total} selected={len(candidates)} execute={bool(args.execute)} "
        f"verify_sha256={bool(args.verify_sha256)}",
        flush=True,
    )
    if not args.execute:
        print("Read-only plan; pass --execute to derive counts and atomically update sidecars.", flush=True)
        return 0
    arguments = {
        "root": str(root),
        "tickers": list(_csv(str(args.tickers))),
        "start_date": str(args.start_date),
        "end_date": str(args.end_date),
        "verify_sha256": bool(args.verify_sha256),
        "max_shards": int(args.max_shards),
        "candidate_count": candidate_total,
        "selected_count": len(candidates),
    }
    log = RepairRunLog(root, arguments)
    repaired = 0
    started = time.perf_counter()
    try:
        for index, candidate in enumerate(candidates, start=1):
            try:
                result = repair_candidate(
                    candidate,
                    execute=True,
                    verify_sha256=bool(args.verify_sha256),
                )
            except Exception as exc:
                log.record("unit_failed", unit_key=candidate.unit_key, error=repr(exc))
                raise
            if result.status == "repaired":
                repaired += 1
            elapsed = time.perf_counter() - started
            rate = index / elapsed if elapsed > 0 else 0.0
            print(
                f"[{index}/{len(candidates)}] {result.status} {candidate.unit_key} "
                f"counts={result.condition_positive_counts} rate={rate:.2f}_shards/s",
                flush=True,
            )
            log.record(
                "unit_repaired",
                unit_key=candidate.unit_key,
                status=result.status,
                condition_positive_counts=result.condition_positive_counts,
            )
        log.record("run_finished", status="complete", repaired=repaired, candidates=len(candidates))
    except KeyboardInterrupt:
        log.record("run_finished", status="interrupted", repaired=repaired, candidates=len(candidates))
        print(f"Interrupted after {repaired} atomic repairs; rerun resumes safely. Log: {log.path}", file=sys.stderr)
        return 130
    except Exception as exc:
        log.record("run_finished", status="failed", repaired=repaired, candidates=len(candidates), error=repr(exc))
        print(f"Repair failed after {repaired} updates: {exc}. Log: {log.path}", file=sys.stderr)
        return 1
    finally:
        log.close()
    elapsed = time.perf_counter() - started
    print(f"Repair complete: repaired={repaired} elapsed={elapsed:.1f}s log={log.path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
