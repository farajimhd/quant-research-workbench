from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import torch

from research.bar_gpt.v1.config import BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig
from research.bar_gpt.v1.data import AUTOREGRESSIVE_VIEW_NAMES, collate_examples
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES
from research.bar_gpt.v1.loader import (
    SESSION_START_SECOND,
    SESSION_TIMEZONE,
    BarGPTSequentialDataset,
    ClickHouseBarStreamConfig,
    SequentialBlockPlan,
    SequentialSessionPlan,
)
from research.bar_gpt.v1.model import BarGPTV1
from research.bar_gpt.v1.offline_shards import (
    CompiledBlock,
    collate_compiled_blocks,
    load_shard,
    materialize_block,
    shard_compatibility_hash,
    shard_path,
)
from research.bar_gpt.v1.schema import FEATURE_INDEX
from research.bar_gpt.v1.targets import TARGET_NAMES
from research.bar_gpt.v1.train import _forward


@dataclass(frozen=True, slots=True)
class AuditBlockRef:
    unit_key: str
    session_index: int
    block_index: int
    ticker: str
    local_date: str
    block_offset: int


@dataclass(slots=True)
class LoadedAuditSample:
    ref: AuditBlockRef
    shard: dict[str, Any]
    session: dict[str, Any]
    stored_block: dict[str, Any]
    block: CompiledBlock


def _rank(seed: int, *values: object) -> bytes:
    text = "|".join((str(seed), *(str(value) for value in values)))
    return hashlib.sha256(text.encode("utf-8")).digest()


def _next_month(month: str) -> str:
    value = dt.date.fromisoformat(f"{month[:7]}-01")
    return ((value.replace(day=28) + dt.timedelta(days=4)).replace(day=1)).isoformat()


def _complete_sidecars(root: Path, tickers: Sequence[str] = ()) -> tuple[Path, ...]:
    allowed = {value.upper() for value in tickers}
    selected: list[Path] = []
    for path in root.glob("tickers/*/*/*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("status") != "complete":
            continue
        unit_key = str(value.get("unit_key", ""))
        ticker = unit_key.partition(":")[0].upper()
        if allowed and ticker not in allowed:
            continue
        selected.append(path)
    return tuple(selected)


def select_random_audit_blocks(
    root: Path,
    *,
    max_shards: int = 2,
    samples_per_shard: int = 1,
    seed: int = 17,
    tickers: Sequence[str] = (),
    require_prior_session: bool = False,
) -> tuple[AuditBlockRef, ...]:
    """Select deterministic pseudo-random blocks without process-random state."""
    if max_shards <= 0 or samples_per_shard <= 0:
        raise ValueError("audit shard and sample counts must be positive")
    sidecars = sorted(
        _complete_sidecars(root, tickers),
        key=lambda path: (_rank(seed, "shard", path.stem, path.parent.parent.name), str(path)),
    )[: int(max_shards)]
    if not sidecars:
        raise RuntimeError(f"no complete BarGPT shards matched beneath {root}")
    result: list[AuditBlockRef] = []
    for sidecar_path in sidecars:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
        unit_key = str(sidecar["unit_key"])
        shard = load_shard(sidecar_path.with_suffix(".pt"))
        candidates: list[AuditBlockRef] = []
        for session_index, session in enumerate(shard["sessions"]):
            if require_prior_session and session_index == 0:
                continue
            for block_index, block in enumerate(session["blocks"]):
                candidates.append(AuditBlockRef(
                    unit_key=unit_key,
                    session_index=session_index,
                    block_index=block_index,
                    ticker=str(session["ticker"]),
                    local_date=str(session["local_date"]),
                    block_offset=int(block["block_offset"]),
                ))
        candidates.sort(
            key=lambda ref: (
                _rank(seed, "block", ref.unit_key, ref.session_index, ref.block_index),
                ref.session_index,
                ref.block_index,
            )
        )
        result.extend(candidates[: int(samples_per_shard)])
    if not result:
        qualifier = " with a prior session" if require_prior_session else ""
        raise RuntimeError(f"selected shards contain no auditable blocks{qualifier}")
    return tuple(result)


def load_audit_sample(root: Path, ref: AuditBlockRef) -> LoadedAuditSample:
    payload = load_shard(shard_path(root, ref.unit_key))
    session = payload["sessions"][int(ref.session_index)]
    stored = session["blocks"][int(ref.block_index)]
    if str(session["ticker"]) != ref.ticker or str(session["local_date"]) != ref.local_date:
        raise RuntimeError(f"audit block identity changed for {ref}")
    if int(stored["block_offset"]) != int(ref.block_offset):
        raise RuntimeError(f"audit block offset changed for {ref}")
    return LoadedAuditSample(
        ref=ref,
        shard=payload,
        session=session,
        stored_block=stored,
        block=materialize_block(payload, ref.session_index, ref.block_index),
    )


def data_config_for_sample(sample: LoadedAuditSample) -> DataConfig:
    """Derive tensor-shaping fields from the selected immutable shard contract."""
    contract = dict(sample.shard.get("context_contract", {}))
    intraday = tuple(
        (name, int(value))
        for name, value in dict(contract.get("intraday_context_bars", {})).items()
    )
    calendar = tuple(
        (name, int(value))
        for name, value in dict(contract.get("calendar_context_bars", {})).items()
    )
    maximum_origins = max(
        int(block["origin_indices"].numel())
        for session in sample.shard["sessions"]
        for block in session["blocks"]
    )
    if not intraday or not calendar or maximum_origins <= 0:
        raise RuntimeError(f"incomplete stored context contract for {sample.ref.unit_key}")
    config = replace(
        DataConfig(),
        base_timeframe_us=int(sample.shard["base_timeframe_us"]),
        horizons_us=tuple(int(value) for value in sample.shard["horizons_us"]),
        context_bars_1s=int(dict(intraday)["1s"]),
        origin_bars_1s=maximum_origins,
        intraday_context_bars=intraday,
        calendar_context_bars=calendar,
        daily_context_bars=int(dict(calendar)["1D"]),
    )
    observed = shard_compatibility_hash(config)
    expected = str(sample.shard.get("config_hash", ""))
    if observed != expected:
        raise RuntimeError(
            f"stored shard contract cannot be reconstructed for {sample.ref.unit_key}: "
            f"expected {expected}, observed {observed}"
        )
    return config


def _origin_clock_second(sample: LoadedAuditSample) -> int:
    start, _length = sample.stored_block["view_slices"]["1s"]
    origin = int(sample.stored_block["origin_indices"][0])
    start_us = int(sample.session["views"]["1s"]["start_us"][int(start) + origin])
    local = dt.datetime.fromtimestamp(start_us / 1_000_000, tz=dt.timezone.utc).astimezone(
        ZoneInfo(SESSION_TIMEZONE)
    )
    if local.date().isoformat() != sample.ref.local_date:
        raise RuntimeError(
            f"stored origin date mismatch: expected {sample.ref.local_date}, observed {local.date()}"
        )
    return local.hour * 3600 + local.minute * 60 + local.second


def reconstruct_clickhouse_example(
    sample: LoadedAuditSample,
    *,
    data_config: DataConfig,
    stream_config: ClickHouseBarStreamConfig,
):
    """Rebuild one exact shard block through the production bounded CH loader."""
    if int(sample.ref.session_index) <= 0:
        raise ValueError("ClickHouse reconstruction requires a shard session with a known prior session")
    expected_hash = shard_compatibility_hash(data_config)
    if str(sample.shard.get("config_hash", "")) != expected_hash:
        raise RuntimeError(
            "audit DataConfig is incompatible with the stored shard: "
            f"expected {sample.shard.get('config_hash')}, observed {expected_hash}"
        )
    clock_second = _origin_clock_second(sample)
    first_origin = clock_second - int(SESSION_START_SECOND)
    if first_origin < 0:
        raise RuntimeError(f"origin precedes the configured session: {clock_second}")
    month = sample.ref.unit_key.split(":", 1)[1]
    month_start = f"{month}-01"
    month_end = _next_month(month)
    prior_date = str(sample.shard["sessions"][sample.ref.session_index - 1]["local_date"])
    session = SequentialSessionPlan(
        unit_index=0,
        ticker=sample.ref.ticker,
        unit_start_date=month_start,
        unit_end_date=month_end,
        local_date=sample.ref.local_date,
        prior_date=prior_date,
        first_origin=first_origin,
        block_count=1,
        unit_block_start=int(sample.ref.block_offset),
        global_block_start=0,
    )
    plan = SequentialBlockPlan(
        sessions=(session,),
        session_block_starts=(0,),
        unit_global_starts=(0,),
        unit_block_counts=(int(sample.ref.block_offset) + 1,),
        total_blocks=1,
        total_origins=int(sample.block.origin_indices.numel()),
    )
    resolved = replace(
        data_config,
        tickers=(sample.ref.ticker,),
        start_date=month_start,
        end_date=month_end,
        validation_start_date=month_start,
        validation_slices=((sample.ref.ticker, month_start, month_end),),
        origin_fetch_candidate_blocks=1,
        origin_emit_blocks_per_chunk=1,
        loader_workers=0,
        persistent_workers=False,
    )
    dataset = BarGPTSequentialDataset(data_config=resolved, stream_config=stream_config, plan=plan)
    return dataset[0]


def _tensor_comparison(left: torch.Tensor, right: torch.Tensor) -> dict[str, Any]:
    shape_match = tuple(left.shape) == tuple(right.shape)
    dtype_match = left.dtype == right.dtype
    if not shape_match:
        return {
            "match": False,
            "shape_match": False,
            "dtype_match": dtype_match,
            "stored_shape": list(left.shape),
            "clickhouse_shape": list(right.shape),
            "mismatched": None,
            "max_abs_difference": None,
        }
    if left.dtype == torch.bool or not left.dtype.is_floating_point:
        exact_difference = left != right
        outside_tolerance = exact_difference
        maximum = None
    else:
        finite = torch.isfinite(left) & torch.isfinite(right)
        exact_difference = (left != right) | (torch.isfinite(left) != torch.isfinite(right))
        outside_tolerance = ~torch.isclose(left, right, rtol=1e-6, atol=1e-6, equal_nan=True)
        maximum = float((left[finite] - right[finite]).abs().max()) if torch.any(finite) else 0.0
    exact_mismatched = int(exact_difference.sum())
    mismatched = int(outside_tolerance.sum())
    return {
        "match": bool(shape_match and dtype_match and mismatched == 0),
        "shape_match": shape_match,
        "dtype_match": dtype_match,
        "stored_shape": list(left.shape),
        "clickhouse_shape": list(right.shape),
        "mismatched": mismatched,
        "exact_mismatched": exact_mismatched,
        "float_atol": 1e-6 if left.dtype.is_floating_point else None,
        "float_rtol": 1e-6 if left.dtype.is_floating_point else None,
        "max_abs_difference": maximum,
    }


def _labeled_tensor_comparison(
    left: torch.Tensor,
    right: torch.Tensor,
    labels: Sequence[str],
) -> dict[str, Any]:
    result = _tensor_comparison(left, right)
    if tuple(left.shape) != tuple(right.shape) or not left.ndim or left.shape[-1] != len(labels):
        return result
    if left.dtype == torch.bool or not left.dtype.is_floating_point:
        difference = left != right
    else:
        difference = ~torch.isclose(left, right, rtol=1e-6, atol=1e-6, equal_nan=True)
    reduce_dims = tuple(range(difference.ndim - 1))
    counts = difference.sum(dim=reduce_dims) if reduce_dims else difference.to(torch.long)
    result["outside_tolerance_by_field"] = {
        label: int(count)
        for label, count in zip(labels, counts.tolist(), strict=True)
        if int(count)
    }
    return result


def compare_loaded_to_clickhouse(
    sample: LoadedAuditSample,
    reconstructed_example,
    *,
    data_config: DataConfig,
) -> dict[str, Any]:
    stored = collate_compiled_blocks(
        [sample.block],
        horizons_us=tuple(data_config.horizons_us),
        base_timeframe_us=int(data_config.base_timeframe_us),
    )
    rebuilt = collate_examples([reconstructed_example], balance_activity_regimes=True).to(
        "cpu", non_blocking=False
    )
    comparisons: dict[str, dict[str, Any]] = {}

    def add(
        name: str,
        left: torch.Tensor,
        right: torch.Tensor,
        labels: Sequence[str] | None = None,
    ) -> None:
        left_cpu = left.cpu()
        right_cpu = right.cpu()
        comparisons[name] = (
            _labeled_tensor_comparison(left_cpu, right_cpu, labels)
            if labels is not None
            else _tensor_comparison(left_cpu, right_cpu)
        )

    for name in stored.views:
        add(f"input/{name}", stored.views[name], rebuilt.views[name], MODEL_FEATURE_NAMES)
    add("origin_indices", stored.origin_indices, rebuilt.origin_indices)
    add("origin_timestamps_us", stored.origin_timestamps_us, rebuilt.origin_timestamps_us)
    add("origin_mask", stored.origin_mask, rebuilt.origin_mask)
    for name in stored.asof_indices:
        add(f"asof/{name}", stored.asof_indices[name], rebuilt.asof_indices[name])
    for name in stored.autoregressive_targets:
        add(
            f"autoregressive_target/{name}",
            stored.autoregressive_targets[name],
            rebuilt.autoregressive_targets[name],
            TARGET_NAMES,
        )
        add(
            f"autoregressive_mask/{name}",
            stored.autoregressive_mask[name],
            rebuilt.autoregressive_mask[name],
        )
    assert stored.horizon_targets is not None and rebuilt.horizon_targets is not None
    assert stored.horizon_mask is not None and rebuilt.horizon_mask is not None
    add("horizon_targets", stored.horizon_targets, rebuilt.horizon_targets, TARGET_NAMES)
    add("horizon_mask", stored.horizon_mask, rebuilt.horizon_mask)
    failed = sorted(name for name, value in comparisons.items() if not value["match"])
    return {
        "match": not failed,
        "failed": failed,
        "comparisons": comparisons,
        "source_switch_diagnostics": source_switch_diagnostics(rebuilt, data_config),
    }


def source_switch_diagnostics(batch, data_config: DataConfig) -> dict[str, Any]:
    """Measure midpoint/trade reference transitions in reconstructed raw support."""
    support = batch.target_support[0, : int(batch.target_support_lengths[0])]
    origins = batch.support_origin_indices[0, : int(batch.origin_mask[0].sum())].long()
    quote = support[:, FEATURE_INDEX["quote_pair_present"]] > 0
    trade = support[:, FEATURE_INDEX["trade_present"]] > 0
    direct_source = torch.where(quote, torch.ones_like(quote, dtype=torch.long), torch.where(
        trade, torch.full_like(quote, 2, dtype=torch.long), torch.zeros_like(quote, dtype=torch.long)
    ))
    rows = torch.arange(direct_source.numel(), dtype=torch.long)
    last = torch.where(direct_source > 0, rows, torch.full_like(rows, -1)).cummax(0).values
    source = torch.where(last >= 0, direct_source[last.clamp_min(0)], torch.zeros_like(last))
    values: dict[str, Any] = {}
    for index, horizon_us in enumerate(data_config.horizons_us):
        steps = int(horizon_us) // int(data_config.base_timeframe_us)
        endpoint = (origins + steps).clamp(max=max(source.numel() - 1, 0))
        valid = batch.horizon_mask[0, : origins.numel(), index, 0]
        changed = valid & (source[origins] != source[endpoint])
        count = int(valid.sum())
        values[f"{int(horizon_us) // 1_000_000}s"] = {
            "valid": count,
            "reference_source_switches": int(changed.sum()),
            "reference_source_switch_rate": float(changed.sum() / count) if count else None,
        }
    return values


def target_diagnostics(sample: LoadedAuditSample, data_config: DataConfig) -> dict[str, Any]:
    target = sample.block.horizon_targets[..., 0]
    mask = sample.block.horizon_mask[..., 0]
    threshold = math.asinh(1.0 / 100.0)
    result: dict[str, Any] = {}
    for index, horizon_us in enumerate(data_config.horizons_us):
        valid = mask[:, index]
        selected = target[:, index][valid]
        bps = torch.sinh(selected.double()) * 100.0
        directional = selected.abs() > threshold
        directional_count = int(directional.sum())
        positive = int((selected > threshold).sum())
        result[f"{int(horizon_us) // 1_000_000}s"] = {
            "valid": int(valid.sum()),
            "masked": int((~valid).sum()),
            "neutral": int((~directional).sum()),
            "neutral_fraction": float((~directional).float().mean()) if selected.numel() else None,
            "up": positive,
            "down": int((selected < -threshold).sum()),
            "up_fraction_directional": positive / directional_count if directional_count else None,
            "mean_bps": float(bps.mean()) if bps.numel() else None,
            "mean_abs_bps": float(bps.abs().mean()) if bps.numel() else None,
            "maximum_abs_bps": float(bps.abs().max()) if bps.numel() else None,
            "over_1000_bps": int((bps.abs() > 1_000).sum()),
        }
    return result


def diagnostic_findings(
    target_values: dict[str, Any],
    clickhouse_comparison: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Return explicit heuristic warnings without weakening exact contract failures."""
    findings: list[dict[str, str]] = []
    for horizon, values in target_values.items():
        neutral = values.get("neutral_fraction")
        up = values.get("up_fraction_directional")
        if neutral is not None and float(neutral) > 0.95:
            findings.append({
                "severity": "warning",
                "code": "near_all_neutral_direction",
                "message": f"{horizon}: {float(neutral):.2%} of valid endpoint returns are inside the neutral band",
            })
        if up is not None and not 0.35 <= float(up) <= 0.65:
            findings.append({
                "severity": "warning",
                "code": "direction_class_imbalance",
                "message": f"{horizon}: directional up share is {float(up):.2%}",
            })
        extreme = int(values.get("over_1000_bps", 0))
        if extreme:
            findings.append({
                "severity": "warning",
                "code": "extreme_endpoint_return",
                "message": f"{horizon}: {extreme} valid endpoint returns exceed 1,000 bps in absolute value",
            })
    if clickhouse_comparison is not None:
        for horizon, values in clickhouse_comparison["source_switch_diagnostics"].items():
            rate = values.get("reference_source_switch_rate")
            if rate is not None and float(rate) > 0.01:
                findings.append({
                    "severity": "warning",
                    "code": "reference_source_switching",
                    "message": f"{horizon}: midpoint/trade reference source changes for {float(rate):.2%} of valid targets",
                })
    if not findings:
        findings.append({
            "severity": "info",
            "code": "no_heuristic_warning",
            "message": "No configured target-noise heuristic was triggered",
        })
    return findings


def context_rows(sample: LoadedAuditSample, origin_offset: int) -> list[dict[str, Any]]:
    origin_count = int(sample.block.origin_indices.numel())
    if not 0 <= int(origin_offset) < origin_count:
        raise IndexError(f"origin offset {origin_offset} is outside [0,{origin_count})")
    rows: list[dict[str, Any]] = []
    for name, value in sample.block.views.items():
        index = (
            int(sample.block.origin_indices[origin_offset])
            if name == "1s"
            else int(sample.block.asof_indices[name][origin_offset])
        )
        rows.append({
            "view": name,
            "rows_loaded": int(value.shape[0]),
            "features": int(value.shape[1]),
            "asof_index": index,
            "available": index >= 0,
            "visible_rows": index + 1 if index >= 0 else 0,
        })
    return rows


def selected_input_rows(
    sample: LoadedAuditSample,
    *,
    origin_offset: int,
    tail_rows: int = 8,
) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    for item in context_rows(sample, origin_offset):
        name = str(item["view"])
        asof = int(item["asof_index"])
        if asof < 0:
            output[name] = []
            continue
        view = sample.block.views[name]
        slice_start, _slice_length = sample.stored_block["view_slices"][name]
        shared = sample.session["views"][name]
        start = max(0, asof - int(tail_rows) + 1)
        rows = []
        for index in range(start, asof + 1):
            shared_index = int(slice_start) + index
            row = {
                "row_index": index,
                "bar_start_us": int(shared["start_us"][shared_index]),
                "available_at_us": int(shared["available_at_us"][shared_index]),
                "is_asof": index == asof,
            }
            row.update({feature: float(view[index, column]) for column, feature in enumerate(MODEL_FEATURE_NAMES)})
            rows.append(row)
        output[name] = rows
    return output


def selected_targets(sample: LoadedAuditSample, origin_offset: int, data_config: DataConfig) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for horizon_index, horizon_us in enumerate(data_config.horizons_us):
        values = sample.block.horizon_targets[origin_offset, horizon_index]
        mask = sample.block.horizon_mask[origin_offset, horizon_index]
        row: dict[str, Any] = {
            "horizon": f"{int(horizon_us) // 1_000_000}s",
            "endpoint_return_bps": float(torch.sinh(values[0].double()) * 100.0),
            "direction": "up" if values[0] > 0 else ("down" if values[0] < 0 else "flat"),
        }
        for target_index, name in enumerate(TARGET_NAMES):
            row[f"target_{name}"] = float(values[target_index])
            row[f"mask_{name}"] = bool(mask[target_index])
        rows.append(row)
    return rows


def selected_autoregressive_targets(sample: LoadedAuditSample, origin_offset: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in AUTOREGRESSIVE_VIEW_NAMES:
        target_index = (
            int(sample.block.origin_indices[origin_offset])
            if name == "1s"
            else int(sample.block.asof_indices[name][origin_offset])
        )
        targets = sample.block.autoregressive_targets[name]
        masks = sample.block.autoregressive_mask[name]
        if target_index < 0 or target_index >= targets.shape[0]:
            rows.append({"view": name, "target_index": target_index, "available": False})
            continue
        values = targets[target_index]
        mask = masks[target_index]
        row: dict[str, Any] = {
            "view": name,
            "target_index": target_index,
            "available": bool(mask.any()),
            "endpoint_return_bps": float(torch.sinh(values[0].double()) * 100.0),
            "direction": "up" if values[0] > 0 else ("down" if values[0] < 0 else "flat"),
        }
        for index, target_name in enumerate(TARGET_NAMES):
            row[f"target_{target_name}"] = float(values[index])
            row[f"mask_{target_name}"] = bool(mask[index])
        rows.append(row)
    return rows


@torch.no_grad()
def selected_predictions(
    sample: LoadedAuditSample,
    *,
    origin_offset: int,
    checkpoint_path: Path,
    device: torch.device | str | None = None,
) -> list[dict[str, Any]]:
    payload = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    model_config = BarGPTConfig(**payload["config"]["model"])
    data_values = dict(payload["config"]["data"])
    data_values.update(batch_size=1, loader_workers=0, persistent_workers=False)
    data_config = DataConfig(**data_values)
    train_values = dict(payload["config"]["train"])
    train_values["output_root"] = Path(train_values["output_root"])
    train_config = TrainConfig(**train_values)
    config = ExperimentConfig(model=model_config, data=data_config, train=train_config)
    resolved_device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    model = BarGPTV1(model_config).to(resolved_device).eval()
    model.load_state_dict(payload["model"], strict=True)
    batch = collate_compiled_blocks(
        [sample.block],
        horizons_us=tuple(data_config.horizons_us),
        base_timeframe_us=int(data_config.base_timeframe_us),
    ).to(resolved_device, non_blocking=False)
    output, _loss = _forward(model, batch, config)
    assert output.horizon_quantiles is not None and output.horizon_direction_logits is not None
    median_index = min(range(len(model_config.quantiles)), key=lambda index: abs(model_config.quantiles[index] - 0.5))
    rows: list[dict[str, Any]] = []
    for horizon_index, horizon_us in enumerate(data_config.horizons_us):
        quantiles = output.horizon_quantiles[0, origin_offset, horizon_index, 0].double().cpu()
        logit = output.horizon_direction_logits[0, origin_offset, horizon_index].double().cpu()
        row = {
            "horizon": f"{int(horizon_us) // 1_000_000}s",
            "predicted_endpoint_bps_q10": float(torch.sinh(quantiles[0]) * 100.0),
            "predicted_endpoint_bps_q50": float(torch.sinh(quantiles[median_index]) * 100.0),
            "predicted_endpoint_bps_q90": float(torch.sinh(quantiles[-1]) * 100.0),
            "direction_logit": float(logit),
            "direction_probability_up": float(torch.sigmoid(logit)),
            "predicted_direction": "up" if logit > 0 else "down",
        }
        rows.append(row)
    return rows


def sample_manifest(sample: LoadedAuditSample, *, origin_offset: int) -> dict[str, Any]:
    return {
        "reference": asdict(sample.ref),
        "origin_offset": int(origin_offset),
        "origin_timestamp_us": int(sample.block.origin_timestamps_us[origin_offset]),
        "origin_index_1s": int(sample.block.origin_indices[origin_offset]),
        "context": context_rows(sample, origin_offset),
    }
