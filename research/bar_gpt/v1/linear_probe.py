from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Iterator

import torch

REPO_ROOT = next(parent for parent in Path(__file__).resolve().parents if (parent / "research").exists())
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research.bar_gpt.v1 import MODEL_FAMILY, MODEL_VERSION
from research.bar_gpt.v1.config import BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig, to_dict
from research.bar_gpt.v1.data import PATHWAY_ID_BY_NAME, TIMEFRAME_US_BY_NAME, BarGPTBatch
from research.bar_gpt.v1.loader import BarGPTIterableDataset, ClickHouseBarStreamConfig, make_dataloader
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.train import preflight
from research.mlops.clickhouse import (
    ClickHouseHttpClient,
    default_clickhouse_password,
    default_clickhouse_url,
    default_clickhouse_user,
    discover_clickhouse_env_files,
)
from research.mlops.env import load_env_files
from research.mlops.manifest import write_run_manifest
from research.mlops.paths import RunPaths


JOB_TYPE = "linear_probe"


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fit frozen BarGPT endpoint-return probes on held-out tickers.")
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output-root", default=r"D:\TradingML\runtimes\bar_gpt\v1\linear_probe")
    parser.add_argument("--run-name", default="")
    parser.add_argument("--calibration-samples", type=int, default=32_768)
    parser.add_argument("--validation-samples", type=int, default=32_768)
    parser.add_argument("--ridge", type=float, default=1.0)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--loader-workers", type=int, default=4)
    parser.add_argument("--progress-interval", type=int, default=8_192)
    return parser.parse_args(list(argv) if argv is not None else None)


def _load_experiment(path: Path) -> tuple[dict[str, Any], ExperimentConfig]:
    payload = torch.load(path, map_location="cpu", weights_only=False)
    saved = payload.get("config")
    if not isinstance(saved, dict) or "model" not in saved or "data" not in saved:
        raise RuntimeError("checkpoint does not contain a BarGPT model/data contract")
    config = ExperimentConfig(
        model=BarGPTConfig(**saved["model"]),
        data=DataConfig(**saved["data"]),
        train=TrainConfig(**saved["train"]),
    )
    return payload, config


def _stream(data: DataConfig) -> ClickHouseBarStreamConfig:
    return ClickHouseBarStreamConfig(
        url=default_clickhouse_url(),
        user=default_clickhouse_user(),
        password=default_clickhouse_password(),
        database=data.database,
        table=data.one_second_table,
        max_threads=data.clickhouse_max_threads_per_worker,
        max_block_size=data.clickhouse_max_block_size,
        max_memory_usage=data.clickhouse_max_memory_usage,
        query_days=data.clickhouse_query_days,
        max_bytes_before_external_sort=data.clickhouse_max_bytes_before_external_sort,
    )


@torch.no_grad()
def collect_embeddings(
    model: BarGPTV1,
    iterator: Iterator[BarGPTBatch],
    *,
    data_config: DataConfig,
    device: torch.device,
    limit: int,
    progress_interval: int,
    label: str,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    embeddings: list[torch.Tensor] = []
    targets: list[torch.Tensor] = []
    masks: list[torch.Tensor] = []
    collected = 0
    next_progress = max(1, progress_interval)
    started = time.perf_counter()
    while collected < limit:
        try:
            raw_batch = next(iterator)
        except StopIteration:
            break
        batch = raw_batch.to(device)
        with torch.autocast(device_type=device.type, dtype=torch.bfloat16, enabled=device.type == "cuda"):
            fused, _ = model.embed(
                batch.views,
                timeframe_us=TIMEFRAME_US_BY_NAME,
                pathway_ids=PATHWAY_ID_BY_NAME,
                base_view="1s",
                origin_indices=batch.origin_indices,
                asof_indices=batch.asof_indices,
                attention_windows=data_config.attention_window_by_name,
            )
        if batch.horizon_targets is None or batch.horizon_mask is None:
            raise RuntimeError("probe batch did not materialize physical targets")
        valid_origins = batch.origin_mask
        x = fused[valid_origins]
        y = batch.horizon_targets[..., 0][valid_origins]
        m = batch.horizon_mask[..., 0][valid_origins]
        take = min(x.shape[0], limit - collected)
        embeddings.append(x[:take].float().cpu())
        targets.append(y[:take].float().cpu())
        masks.append(m[:take].cpu())
        collected += take
        if collected >= next_progress:
            elapsed = max(1e-9, time.perf_counter() - started)
            print(f"{label} embeddings {collected:,}/{limit:,} ({collected / elapsed:,.1f} origins/s)", flush=True)
            next_progress += max(1, progress_interval)
    if not embeddings:
        raise RuntimeError(f"no {label} embeddings were available")
    return torch.cat(embeddings), torch.cat(targets), torch.cat(masks)


def fit_ridge_probes(
    calibration_x: torch.Tensor,
    calibration_y: torch.Tensor,
    calibration_mask: torch.Tensor,
    validation_x: torch.Tensor,
    validation_y: torch.Tensor,
    validation_mask: torch.Tensor,
    *,
    ridge: float,
) -> tuple[dict[str, torch.Tensor], list[dict[str, float]]]:
    mean = calibration_x.mean(dim=0)
    scale = calibration_x.std(dim=0).clamp_min(1e-5)
    train_x = (calibration_x - mean) / scale
    test_x = (validation_x - mean) / scale
    train_x = torch.cat((train_x, torch.ones((train_x.shape[0], 1))), dim=1).double()
    test_x = torch.cat((test_x, torch.ones((test_x.shape[0], 1))), dim=1).double()
    weights: list[torch.Tensor] = []
    metrics: list[dict[str, float]] = []
    identity = torch.eye(train_x.shape[1], dtype=torch.float64)
    identity[-1, -1] = 0.0
    for horizon in range(calibration_y.shape[1]):
        train_valid = calibration_mask[:, horizon]
        test_valid = validation_mask[:, horizon]
        x_fit = train_x[train_valid]
        y_fit = calibration_y[train_valid, horizon].double()
        if x_fit.shape[0] <= x_fit.shape[1] or int(test_valid.sum()) == 0:
            raise RuntimeError(f"insufficient valid rows for horizon index {horizon}")
        weight = torch.linalg.solve(x_fit.T @ x_fit + float(ridge) * identity, x_fit.T @ y_fit)
        prediction = test_x[test_valid] @ weight
        truth = validation_y[test_valid, horizon].double()
        residual = prediction - truth
        variance = ((truth - truth.mean()) ** 2).sum().clamp_min(1e-12)
        metrics.append(
            {
                "horizon_index": float(horizon),
                "samples": float(truth.numel()),
                "r2": float(1.0 - residual.square().sum() / variance),
                "mae": float(residual.abs().mean()),
                "directional_accuracy": float((torch.sign(prediction) == torch.sign(truth)).double().mean()),
            }
        )
        weights.append(weight.float())
    return {"mean": mean, "scale": scale, "weights": torch.stack(weights)}, metrics


def main(argv: Iterable[str] | None = None) -> int:
    load_env_files(discover_clickhouse_env_files(), verbose=True)
    args = parse_args(argv)
    checkpoint = Path(args.checkpoint)
    payload, config = _load_experiment(checkpoint)
    config.data.batch_size = int(args.batch_size)
    config.data.loader_workers = int(args.loader_workers)
    config.data.persistent_workers = False
    evidence = preflight(
        ClickHouseHttpClient(default_clickhouse_url(), default_clickhouse_user(), default_clickhouse_password()),
        config.data,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = BarGPTV1(config.model).to(device)
    model.load_state_dict(payload["model"])
    model.eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    stream = _stream(config.data)
    calibration = make_dataloader(
        BarGPTIterableDataset(data_config=config.data, stream_config=stream, split="train", seed=config.train.seed),
        config.data,
        drop_last=False,
    )
    validation = make_dataloader(
        BarGPTIterableDataset(data_config=config.data, stream_config=stream, split="validation", seed=config.train.seed),
        config.data,
        drop_last=False,
    )
    run_name = args.run_name or f"bar-gpt-v1-probe-{time.strftime('%Y%m%d-%H%M%S')}"
    paths = RunPaths.create(Path(args.output_root) / run_name)
    write_run_manifest(
        paths.manifest_path,
        repo_root=REPO_ROOT,
        model_family=MODEL_FAMILY,
        version=MODEL_VERSION,
        job_type=JOB_TYPE,
        run_name=run_name,
        args=vars(args),
        config={**to_dict(config), "data_evidence": evidence},
        data_roots={"clickhouse": default_clickhouse_url(), "database": config.data.database},
        output_root=paths.run_root,
        source_checkpoint=checkpoint,
    )
    calibration_data = collect_embeddings(
        model, iter(calibration), data_config=config.data, device=device, limit=int(args.calibration_samples),
        progress_interval=int(args.progress_interval), label="calibration",
    )
    validation_data = collect_embeddings(
        model, iter(validation), data_config=config.data, device=device, limit=int(args.validation_samples),
        progress_interval=int(args.progress_interval), label="validation",
    )
    probe, metrics = fit_ridge_probes(*calibration_data, *validation_data, ridge=float(args.ridge))
    probe["horizons_us"] = torch.as_tensor(config.data.horizons_us, dtype=torch.long)
    torch.save(probe, paths.artifacts_dir / "frozen_endpoint_return_probe.pt")
    metrics_rows = [
        {**row, "horizon_us": int(config.data.horizons_us[int(row["horizon_index"])])}
        for row in metrics
    ]
    (paths.run_root / "probe_metrics.json").write_text(json.dumps(metrics_rows, indent=2), encoding="utf-8")
    for row in metrics_rows:
        print(
            f"horizon={row['horizon_us']:,}us samples={int(row['samples']):,} "
            f"r2={row['r2']:.4f} mae={row['mae']:.4f} direction={row['directional_accuracy']:.3%}",
            flush=True,
        )
    print(f"Probe artifacts: {paths.run_root}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
