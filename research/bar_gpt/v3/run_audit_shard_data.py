from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
from typing import Sequence

from research.bar_gpt.v3.audit_offline_shards import audit_shard
from research.bar_gpt.v3.shard_data_audit import (
    autoregressive_target_diagnostics,
    compare_loaded_to_clickhouse,
    data_config_for_sample,
    diagnostic_findings,
    load_audit_sample,
    reconstruct_clickhouse_example,
    select_random_audit_blocks,
    target_diagnostics,
)
from research.bar_gpt.v3.offline_shards import shard_path
from research.bar_gpt.v3.train import _stream_config
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files


DEFAULT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v12")
DEFAULT_OUTPUT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v3\shard_data_audits")


def _csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip().upper() for item in value.split(",") if item.strip()))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit random certified BarGPT shards and reconstruct bounded samples from ClickHouse."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--tickers", default="", help="Optional comma-separated ticker filter.")
    parser.add_argument("--max-shards", type=int, default=2)
    parser.add_argument("--samples-per-shard", type=int, default=1)
    parser.add_argument("--clickhouse-samples", type=int, default=2)
    parser.add_argument("--clickhouse-prefetch-pages", type=int, default=16)
    parser.add_argument("--clickhouse-max-threads-per-query", type=int, default=2)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--verify-sha256", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if (
        args.max_shards <= 0
        or args.samples_per_shard <= 0
        or args.clickhouse_samples < 0
        or args.clickhouse_prefetch_pages <= 0
        or args.clickhouse_max_threads_per_query <= 0
    ):
        parser.error("shard/sample counts must be positive and ClickHouse samples cannot be negative")
    return args


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _catalog_start_date(root: Path) -> tuple[str, str]:
    """Return the frozen one-pass start and config identity for reconstruction."""
    path = root / "manifest" / "build_plan.json"
    if not path.is_file():
        raise RuntimeError(f"BarGPT shard reconstruction requires the frozen build plan: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    selection = value.get("selection")
    if not isinstance(selection, dict) or not selection.get("start_date"):
        raise RuntimeError(f"BarGPT build plan has no frozen selection start: {path}")
    config_hash = str(value.get("config_hash", ""))
    if not config_hash:
        raise RuntimeError(f"BarGPT build plan has no config hash: {path}")
    try:
        start = dt.date.fromisoformat(str(selection["start_date"])).isoformat()
    except ValueError as exc:
        raise RuntimeError(f"BarGPT build plan has an invalid selection start: {path}") from exc
    return start, config_hash


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    refs = select_random_audit_blocks(
        args.root,
        max_shards=int(args.max_shards),
        samples_per_shard=int(args.samples_per_shard),
        seed=int(args.seed),
        tickers=_csv(str(args.tickers)),
        require_prior_session=int(args.clickhouse_samples) > 0,
    )
    clickhouse_limit = min(int(args.clickhouse_samples), len(refs))
    if clickhouse_limit:
        load_env_files(discover_clickhouse_env_files(), verbose=True)
        catalog_start_date, catalog_config_hash = _catalog_start_date(args.root)
    else:
        catalog_start_date, catalog_config_hash = "", ""
    samples = []
    audited_sidecars: set[Path] = set()
    failures: list[str] = []
    for index, ref in enumerate(refs):
        sample = load_audit_sample(args.root, ref)
        data_config = data_config_for_sample(sample)
        sidecar = shard_path(args.root, ref.unit_key).with_suffix(".json")
        structural = None
        if sidecar not in audited_sidecars:
            structural = audit_shard(
                sidecar,
                verify_sha256=bool(args.verify_sha256),
            )
            audited_sidecars.add(sidecar)
        row = {
            "reference": {
                "unit_key": ref.unit_key,
                "ticker": ref.ticker,
                "local_date": ref.local_date,
                "session_index": ref.session_index,
                "block_index": ref.block_index,
                "block_offset": ref.block_offset,
            },
            "structural_audit": structural,
            "target_diagnostics": target_diagnostics(sample, data_config),
            "autoregressive_target_diagnostics": autoregressive_target_diagnostics(sample),
            "clickhouse_reconstruction": None,
        }
        if index < clickhouse_limit:
            if str(sample.shard.get("config_hash", "")) != catalog_config_hash:
                raise RuntimeError(
                    f"sampled shard {ref.unit_key} does not match frozen build plan config "
                    f"{catalog_config_hash}"
                )
            rebuilt = reconstruct_clickhouse_example(
                sample,
                data_config=data_config,
                stream_config=_stream_config(data_config),
                catalog_start_date=catalog_start_date,
                clickhouse_prefetch_pages=int(args.clickhouse_prefetch_pages),
                clickhouse_max_threads_per_worker=int(args.clickhouse_max_threads_per_query),
            )
            comparison = compare_loaded_to_clickhouse(sample, rebuilt, data_config=data_config)
            row["clickhouse_reconstruction"] = comparison
            if not comparison["match"]:
                failures.append(
                    f"{ref.unit_key} session={ref.session_index} block={ref.block_index}: "
                    f"{comparison['failed']}"
                )
        row["findings"] = [
            *diagnostic_findings(row["target_diagnostics"], row["clickhouse_reconstruction"]),
            *diagnostic_findings(row["autoregressive_target_diagnostics"], None),
        ]
        error_findings = [item for item in row["findings"] if item.get("severity") == "error"]
        if error_findings:
            failures.append(
                f"{ref.unit_key} session={ref.session_index} block={ref.block_index}: "
                + "; ".join(str(item.get("message", "audit error")) for item in error_findings)
            )
        samples.append(row)
        status = (
            "ClickHouse match within certified float tolerance"
            if row["clickhouse_reconstruction"] and row["clickhouse_reconstruction"]["match"]
            else ("ClickHouse mismatch" if row["clickhouse_reconstruction"] else "stored audit")
        )
        print(f"[{index + 1}/{len(refs)}] {ref.unit_key} {ref.local_date} block={ref.block_index}: {status}", flush=True)
    report = {
        "contract_version": 4,
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": str(args.root),
        "seed": int(args.seed),
        "audited_shards": len(audited_sidecars),
        "audited_blocks": len(samples),
        "clickhouse_reconstructions": clickhouse_limit,
        "status": "failed" if failures else "passed",
        "failures": failures,
        "samples": samples,
    }
    output = args.output_root / f"audit-{dt.datetime.now():%Y%m%d-%H%M%S}-p{os.getpid()}.json"
    _atomic_json(output, report)
    if failures:
        raise RuntimeError(f"BarGPT shard data audit failed; inspect {output}: {failures}")
    print(f"BarGPT shard data audit passed: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
