from __future__ import annotations

import argparse
import csv
import gzip
import json
import time
from pathlib import Path
from typing import Any, Iterable

import torch

from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
)
from research.mlops.env import discover_env_files, load_env_files
from research.news_reaction_model.v1.config import LoaderConfig, ModelConfig
from research.news_reaction_model.v1.data import ClickHouseNewsReactionDataset
from research.news_reaction_model.v1.model import NewsReactionModelV1

REPO_ROOT = Path(__file__).resolve().parents[3]
HORIZONS = ("1m", "5m", "10m", "30m", "1h", "2h", "3h", "premarket_close", "regular_close", "extended_close")
SIDE = {"negative": -1, "neutral": 0, "positive": 1}


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare v2, v1, and deterministic one-share P&L on identical labels.")
    parser.add_argument("--v1-checkpoint", required=True)
    parser.add_argument("--v2-predictions", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start", default="2026-01-01")
    parser.add_argument("--end-exclusive", default="2027-01-01")
    parser.add_argument("--v2-flat-z", type=float, default=0.25)
    parser.add_argument("--deterministic-database", default="q_live")
    parser.add_argument("--deterministic-table", default="news_reaction_predictions_v2")
    parser.add_argument("--deterministic-version", default="news_reaction_probability_v2_1")
    parser.add_argument("--amp", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args(list(argv) if argv is not None else None)


def normalize_timestamp(value: Any) -> str:
    text = str(value).strip().replace("T", " ").removesuffix("Z")
    if "." not in text:
        return text
    whole, fractional = text.split(".", 1)
    fractional = fractional.rstrip("0")
    return f"{whole}.{fractional}" if fractional else whole


def key(news_id: Any, ticker: Any, published_at_utc: Any, horizon: Any) -> tuple[str, str, str, str]:
    return str(news_id), str(ticker), normalize_timestamp(published_at_utc), str(horizon)


def load_v2_ledger(path: Path) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    rows: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            row_key = key(row["canonical_news_id"], row["ticker"], row["published_at_utc"], row["horizon"])
            if row_key in rows:
                raise RuntimeError(f"Duplicate v2 prediction identity: {row_key}")
            rows[row_key] = row
    if not rows:
        raise RuntimeError(f"No v2 predictions found in {path}")
    return rows


def deterministic_sql(args: argparse.Namespace) -> str:
    database = args.deterministic_database.replace("`", "``")
    table = args.deterministic_table.replace("`", "``")
    version = args.deterministic_version.replace("'", "''")
    return f"""
SELECT canonical_news_id, ticker, toString(published_at_utc) AS published_at_utc_text, horizon_code, predicted_class
FROM `{database}`.`{table}` FINAL
WHERE prediction_version = '{version}'
 AND published_at_utc >= toDateTime64('{args.start}', 9, 'UTC')
 AND published_at_utc < toDateTime64('{args.end_exclusive}', 9, 'UTC')
FORMAT JSONEachRow
"""


def load_deterministic(args: argparse.Namespace) -> dict[tuple[str, str, str, str], int]:
    client = ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password())
    result: dict[tuple[str, str, str, str], int] = {}
    for line in client.execute(deterministic_sql(args)).splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        row_key = key(
            row["canonical_news_id"], row["ticker"], row["published_at_utc_text"], row["horizon_code"]
        )
        side = SIDE.get(str(row["predicted_class"]))
        if side is None:
            raise RuntimeError(f"Unknown deterministic class {row['predicted_class']!r}")
        if row_key in result and result[row_key] != side:
            raise RuntimeError(f"Conflicting deterministic predictions for {row_key}")
        result[row_key] = side
    return result


def empty_metrics() -> dict[str, float | int]:
    return {
        "long": 0,
        "short": 0,
        "flat": 0,
        "long_one_share_pnl": 0.0,
        "short_one_share_pnl": 0.0,
        "one_share_pnl": 0.0,
    }


def add(metrics: dict[str, float | int], side: int, anchor: float, target: float) -> None:
    metrics["long" if side > 0 else "short" if side < 0 else "flat"] += 1
    pnl = side * (target - anchor)
    if side > 0:
        metrics["long_one_share_pnl"] += pnl
    elif side < 0:
        metrics["short_one_share_pnl"] += pnl
    metrics["one_share_pnl"] += pnl


def total_metrics(horizons: dict[str, dict[str, float | int]]) -> dict[str, float | int]:
    total = empty_metrics()
    for metrics in horizons.values():
        for field in total:
            total[field] += metrics[field]
    return total


def write_comparison_csv(
    path: Path,
    metrics: dict[str, dict[str, dict[str, float | int]]],
    totals: dict[str, dict[str, float | int]],
) -> None:
    fields = (
        "model", "horizon", "long", "long_one_share_pnl", "short",
        "short_one_share_pnl", "flat", "one_share_pnl",
    )
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for horizon in HORIZONS:
            for model in ("deterministic", "v1", "v2"):
                writer.writerow({"model": model, "horizon": horizon, **metrics[model][horizon]})
        for model in ("deterministic", "v1", "v2"):
            writer.writerow({"model": model, "horizon": "ALL_INDEPENDENT_HORIZONS", **totals[model]})


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_env_files(REPO_ROOT), verbose=True)
    args = parse_args(argv)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    with torch.serialization.safe_globals([type(Path())]):
        state = torch.load(Path(args.v1_checkpoint), map_location=device, weights_only=True)
    loader = LoaderConfig(**state["config"]["loader"])
    model = NewsReactionModelV1(ModelConfig(**state["config"]["model"])).to(device)
    model.load_state_dict(state["model"])
    model.eval()
    v2 = load_v2_ledger(Path(args.v2_predictions))
    deterministic = load_deterministic(args)
    metrics = {name: {horizon: empty_metrics() for horizon in HORIZONS} for name in ("v2", "v1", "deterministic")}
    seen: set[tuple[str, str, str, str]] = set()
    missing_deterministic = 0
    dataset = ClickHouseNewsReactionDataset(loader, start=args.start, end_exclusive=args.end_exclusive)
    started = time.perf_counter()
    try:
        for batch in dataset.iter_batches():
            device_batch = batch.to(device)
            with torch.inference_mode(), torch.autocast(
                device_type=device.type,
                dtype=torch.bfloat16,
                enabled=device.type == "cuda" and args.amp,
            ):
                output = model(device_batch.x)
            v1_sides = output.class_logits.argmax(dim=-1).to("cpu").numpy() - 1
            mask = batch.label_mask.numpy().astype(bool)
            for row_index in range(batch.sample_count):
                news_id = batch.identity["canonical_news_id"][row_index]
                ticker = batch.identity["ticker"][row_index]
                published_at_utc = batch.identity["published_at_utc"][row_index]
                for horizon_index, horizon in enumerate(HORIZONS):
                    if not mask[row_index, horizon_index]:
                        continue
                    row_key = key(news_id, ticker, published_at_utc, horizon)
                    ledger = v2.get(row_key)
                    if ledger is None:
                        raise RuntimeError(f"Prepared v1 row missing from exact v2 ledger: {row_key}")
                    anchor, target = float(ledger["anchor_price"]), float(ledger["target_price"])
                    threshold = args.v2_flat_z * float(ledger["robust_scale"])
                    prediction = float(ledger["predicted_abnormal_target_return"])
                    v2_side = 1 if prediction > threshold else -1 if prediction < -threshold else 0
                    add(metrics["v2"][horizon], v2_side, anchor, target)
                    add(metrics["v1"][horizon], int(v1_sides[row_index, horizon_index]), anchor, target)
                    deterministic_side = deterministic.get(row_key)
                    if deterministic_side is None:
                        deterministic_side = 0
                        missing_deterministic += 1
                    add(metrics["deterministic"][horizon], deterministic_side, anchor, target)
                    seen.add(row_key)
    finally:
        dataset.stop()
    if seen != set(v2):
        raise RuntimeError(f"Comparison coverage mismatch: compared={len(seen):,} v2_ledger={len(v2):,}")
    totals = {name: total_metrics(horizons) for name, horizons in metrics.items()}
    output = {
        "validation_range": [args.start, args.end_exclusive],
        "identical_article_horizon_rows": len(seen),
        "v2_flat_z": args.v2_flat_z,
        "v1_decision": "argmax(class_logits): negative, neutral, positive",
        "deterministic_decision": "persisted predicted_class; missing prediction is flat",
        "deterministic_available": len(set(v2) & set(deterministic)),
        "deterministic_missing_as_flat": missing_deterministic,
        "elapsed_seconds": time.perf_counter() - started,
        "models": metrics,
        "model_totals_across_independent_horizons": totals,
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, indent=2, allow_nan=False), encoding="utf-8")
    csv_path = path.with_suffix(".csv")
    write_comparison_csv(csv_path, metrics, totals)
    print(
        f"COMPLETED rows={len(seen):,} deterministic_missing={missing_deterministic:,} "
        f"output={path} table={csv_path}", flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
