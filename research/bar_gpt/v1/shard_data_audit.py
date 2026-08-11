from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Sequence

import torch

from research.bar_gpt.v1.config import BarGPTConfig, DataConfig, ExperimentConfig, TrainConfig
from research.bar_gpt.v1.data import AUTOREGRESSIVE_VIEW_NAMES, collate_examples
from research.bar_gpt.v1.direct_event_shards import DirectEventShardDataset
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES
from research.bar_gpt.v1.loader import (
    BarGPTIterableDataset,
    ClickHouseBarStreamConfig,
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
from research.bar_gpt.v1.targets import (
    AUTOREGRESSIVE_TARGET_NAMES,
    DIRECTION_TARGET_COUNT,
    DIRECTION_TARGET_NAMES,
    OHLC_FIELDS,
    PRICE_FAMILIES,
    TARGET_NAMES,
)
from research.bar_gpt.v1.train import _forward


DEFAULT_FLOAT_ATOL = 1e-6
DEFAULT_FLOAT_RTOL = 1e-6
# Float32 price ratios can differ by one or two source ULPs when ClickHouse
# recomputes aggregate rows. The physical target transform multiplies log
# returns by 100, so bound the resulting comparison in transformed space to
# approximately 0.005 bp near zero while retaining the stricter default for
# every other target.
PHYSICAL_HORIZON_FLOAT_ATOL_BY_TARGET: tuple[float, ...] = (
    *(5e-5 for _ in range(DIRECTION_TARGET_COUNT)),
    *(DEFAULT_FLOAT_ATOL for _ in TARGET_NAMES[DIRECTION_TARGET_COUNT:]),
)


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
    source = dict(contract.get("source_authority", {}))
    intraday = tuple(
        (name, int(value))
        for name, value in dict(contract.get("intraday_context_bars", {})).items()
    )
    calendar = tuple(
        (name, int(value))
        for name, value in dict(contract.get("calendar_context_bars", {})).items()
    )
    configured_origins = int(contract.get("origin_bars_1s", 0))
    if not intraday or not calendar or configured_origins <= 0:
        raise RuntimeError(f"incomplete stored context contract for {sample.ref.unit_key}")
    config = replace(
        DataConfig(),
        base_timeframe_us=int(sample.shard["base_timeframe_us"]),
        horizons_us=tuple(int(value) for value in sample.shard["horizons_us"]),
        context_bars_1s=int(dict(intraday)["1s"]),
        origin_bars_1s=configured_origins,
        intraday_context_bars=intraday,
        calendar_context_bars=calendar,
        daily_context_bars=int(dict(calendar)["1D"]),
        source_mode=str(source.get("mode", DataConfig().source_mode)),
        database=str(source.get("database", DataConfig().database)),
        one_second_table=str(source.get("one_second_table", DataConfig().one_second_table)),
        daily_table=str(source.get("daily_table", DataConfig().daily_table)),
        condition_table=str(source.get("condition_table", DataConfig().condition_table)),
        condition_status_table=str(source.get("condition_status_table", DataConfig().condition_status_table)),
    )
    observed = shard_compatibility_hash(config)
    expected = str(sample.shard.get("config_hash", ""))
    if observed != expected:
        raise RuntimeError(
            f"stored shard contract cannot be reconstructed for {sample.ref.unit_key}: "
            f"expected {expected}, observed {observed}"
        )
    return config


def reconstruct_clickhouse_example(
    sample: LoadedAuditSample,
    *,
    data_config: DataConfig,
    stream_config: ClickHouseBarStreamConfig,
    clickhouse_prefetch_pages: int | None = None,
    clickhouse_max_threads_per_worker: int | None = None,
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
    month = sample.ref.unit_key.split(":", 1)[1]
    month_start = f"{month}-01"
    month_end = _next_month(month)
    resolved = replace(
        data_config,
        tickers=(sample.ref.ticker,),
        start_date=month_start,
        end_date=month_end,
        validation_start_date=month_start,
        validation_slices=((sample.ref.ticker, month_start, month_end),),
        loader_workers=0,
        persistent_workers=False,
        clickhouse_prefetch_pages=(
            int(clickhouse_prefetch_pages)
            if clickhouse_prefetch_pages is not None
            else int(data_config.clickhouse_prefetch_pages)
        ),
        clickhouse_max_threads_per_worker=(
            int(clickhouse_max_threads_per_worker)
            if clickhouse_max_threads_per_worker is not None
            else int(data_config.clickhouse_max_threads_per_worker)
        ),
    )
    dataset_class = (
        DirectEventShardDataset
        if resolved.source_mode == "direct_events"
        else BarGPTIterableDataset
    )
    dataset = dataset_class(
        data_config=resolved,
        stream_config=stream_config,
        split="cache",
        seed=17,
        unit_tickers=(sample.ref.ticker,),
    )
    for example in dataset:
        if (
            example.local_date == sample.ref.local_date
            and int(example.block_offset) == int(sample.ref.block_offset)
        ):
            return example
    raise RuntimeError(
        f"ClickHouse reconstruction did not reproduce {sample.ref.unit_key} "
        f"{sample.ref.local_date} block {sample.ref.block_offset}"
    )


def _float_outside_tolerance(
    left: torch.Tensor,
    right: torch.Tensor,
    *,
    atol: float | torch.Tensor,
    rtol: float,
) -> torch.Tensor:
    absolute = torch.as_tensor(atol, dtype=left.dtype, device=left.device)
    while absolute.ndim < left.ndim:
        absolute = absolute.unsqueeze(0)
    equal = left == right
    equal |= torch.isnan(left) & torch.isnan(right)
    finite = torch.isfinite(left) & torch.isfinite(right)
    equal |= finite & ((left - right).abs() <= absolute + float(rtol) * right.abs())
    return ~equal


def _tensor_comparison(
    left: torch.Tensor,
    right: torch.Tensor,
    *,
    atol: float | torch.Tensor = DEFAULT_FLOAT_ATOL,
    rtol: float = DEFAULT_FLOAT_RTOL,
) -> dict[str, Any]:
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
        outside_tolerance = _float_outside_tolerance(left, right, atol=atol, rtol=rtol)
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
        "float_atol": (
            float(torch.as_tensor(atol).max()) if left.dtype.is_floating_point else None
        ),
        "float_rtol": float(rtol) if left.dtype.is_floating_point else None,
        "max_abs_difference": maximum,
    }


def _labeled_tensor_comparison(
    left: torch.Tensor,
    right: torch.Tensor,
    labels: Sequence[str],
    *,
    atol_by_field: Sequence[float] | None = None,
) -> dict[str, Any]:
    if atol_by_field is not None and len(atol_by_field) != len(labels):
        raise ValueError("field tolerances must align with labels")
    atol: float | torch.Tensor = (
        torch.tensor(tuple(atol_by_field), dtype=left.dtype, device=left.device)
        if atol_by_field is not None
        else DEFAULT_FLOAT_ATOL
    )
    result = _tensor_comparison(left, right, atol=atol)
    if tuple(left.shape) != tuple(right.shape) or not left.ndim or left.shape[-1] != len(labels):
        return result
    if left.dtype == torch.bool or not left.dtype.is_floating_point:
        difference = left != right
    else:
        difference = _float_outside_tolerance(
            left,
            right,
            atol=atol,
            rtol=DEFAULT_FLOAT_RTOL,
        )
    reduce_dims = tuple(range(difference.ndim - 1))
    counts = difference.sum(dim=reduce_dims) if reduce_dims else difference.to(torch.long)
    result["outside_tolerance_by_field"] = {
        label: int(count)
        for label, count in zip(labels, counts.tolist(), strict=True)
        if int(count)
    }
    if atol_by_field is not None and left.dtype.is_floating_point:
        result["float_atol_by_field"] = {
            label: float(value)
            for label, value in zip(labels, atol_by_field, strict=True)
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
        add(f"input_mask/{name}", stored.view_mask[name], rebuilt.view_mask[name])
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
            AUTOREGRESSIVE_TARGET_NAMES,
        )
        add(
            f"autoregressive_mask/{name}",
            stored.autoregressive_mask[name],
            rebuilt.autoregressive_mask[name],
        )
    assert stored.horizon_targets is not None and rebuilt.horizon_targets is not None
    assert stored.horizon_mask is not None and rebuilt.horizon_mask is not None
    comparisons["horizon_targets"] = _labeled_tensor_comparison(
        stored.horizon_targets.cpu(),
        rebuilt.horizon_targets.cpu(),
        TARGET_NAMES,
        atol_by_field=PHYSICAL_HORIZON_FLOAT_ATOL_BY_TARGET,
    )
    add("horizon_mask", stored.horizon_mask, rebuilt.horizon_mask)
    failed = sorted(name for name, value in comparisons.items() if not value["match"])
    return {
        "match": not failed,
        "failed": failed,
        "comparisons": comparisons,
        "family_update_diagnostics": family_update_diagnostics(rebuilt, data_config),
    }


def family_update_diagnostics(batch, data_config: DataConfig) -> dict[str, Any]:
    """Verify every valid price target has a same-family future update."""
    support = batch.target_support[0, : int(batch.target_support_lengths[0])]
    available = batch.target_support_available_at_us[0, : int(batch.target_support_lengths[0])]
    origins = batch.support_origin_indices[0, : int(batch.origin_mask[0].sum())].long()
    values: dict[str, Any] = {}
    for index, horizon_us in enumerate(data_config.horizons_us):
        origin_times = available[origins]
        endpoints = torch.searchsorted(available, origin_times + int(horizon_us), right=True)
        family_values: dict[str, Any] = {}
        for family_index, family in enumerate(PRICE_FAMILIES):
            present = support[:, FEATURE_INDEX[f"{family}_present"]] > 0
            prefix = torch.cat((torch.zeros(1, dtype=torch.long), present.long().cumsum(0)))
            future_update = prefix[endpoints] - prefix[origins + 1] > 0
            first = family_index * len(OHLC_FIELDS)
            valid = batch.horizon_mask[
                0, : origins.numel(), index, first : first + len(OHLC_FIELDS)
            ].any(dim=-1)
            violations = valid & ~future_update
            family_values[family] = {
                "valid": int(valid.sum()),
                "valid_without_future_family_update": int(violations.sum()),
            }
        values[f"{int(horizon_us) // 1_000_000}s"] = family_values
    return values


def target_diagnostics(sample: LoadedAuditSample, data_config: DataConfig) -> dict[str, Any]:
    threshold = math.asinh(1.0 / 100.0)
    result: dict[str, Any] = {}
    for index, horizon_us in enumerate(data_config.horizons_us):
        families: dict[str, Any] = {}
        for target_index, target_name in enumerate(DIRECTION_TARGET_NAMES):
            valid = sample.block.horizon_mask[:, index, target_index]
            selected = sample.block.horizon_targets[:, index, target_index][valid]
            bps = torch.sinh(selected.double()) * 100.0
            directional = selected.abs() > threshold
            directional_count = int(directional.sum())
            positive = int((selected > threshold).sum())
            families[target_name] = {
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
        result[f"{int(horizon_us) // 1_000_000}s"] = families
    return result


def autoregressive_target_diagnostics(sample: LoadedAuditSample) -> dict[str, Any]:
    """Report class balance and scale for every AR OHLC task in every view."""
    threshold = math.asinh(1.0 / 100.0)
    result: dict[str, Any] = {}
    for view in AUTOREGRESSIVE_VIEW_NAMES:
        targets = sample.block.autoregressive_targets[view]
        masks = sample.block.autoregressive_mask[view]
        values: dict[str, Any] = {}
        for target_index, target_name in enumerate(DIRECTION_TARGET_NAMES):
            valid = masks[..., target_index]
            selected = targets[..., target_index][valid]
            bps = torch.sinh(selected.double()) * 100.0
            directional = selected.abs() > threshold
            directional_count = int(directional.sum())
            positive = int((selected > threshold).sum())
            values[target_name] = {
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
        result[view] = values
    return result


def diagnostic_findings(
    target_values: dict[str, Any],
    clickhouse_comparison: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Return explicit heuristic warnings without weakening exact contract failures."""
    findings: list[dict[str, str]] = []
    for horizon, families in target_values.items():
        for family, values in families.items():
            neutral = values.get("neutral_fraction")
            up = values.get("up_fraction_directional")
            if neutral is not None and float(neutral) > 0.95:
                findings.append({
                    "severity": "warning", "code": "near_all_neutral_direction",
                    "message": f"{horizon}/{family}: {float(neutral):.2%} of valid returns are neutral",
                })
            if up is not None and not 0.35 <= float(up) <= 0.65:
                findings.append({
                    "severity": "warning", "code": "direction_class_imbalance",
                    "message": f"{horizon}/{family}: directional up share is {float(up):.2%}",
                })
            extreme = int(values.get("over_1000_bps", 0))
            if extreme:
                findings.append({
                    "severity": "warning", "code": "extreme_endpoint_return",
                    "message": f"{horizon}/{family}: {extreme} valid returns exceed 1,000 bps",
                })
    if clickhouse_comparison is not None:
        for horizon, families in clickhouse_comparison["family_update_diagnostics"].items():
            for family, values in families.items():
                violations = int(values["valid_without_future_family_update"])
                if violations:
                    findings.append({
                        "severity": "error", "code": "missing_future_family_update",
                        "message": f"{horizon}/{family}: {violations} valid targets lack a future same-family update",
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
                "bar_end_us": int(shared["end_us"][shared_index]),
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
        }
        for target_index, target_name in enumerate(DIRECTION_TARGET_NAMES):
            row[f"{target_name}_bps"] = float(torch.sinh(values[target_index].double()) * 100.0)
            row[f"{target_name}_direction"] = (
                "up" if values[target_index] > 0 else ("down" if values[target_index] < 0 else "flat")
            )
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
        }
        for index, target_name in enumerate(DIRECTION_TARGET_NAMES):
            row[f"{target_name}_bps"] = float(torch.sinh(values[index].double()) * 100.0)
            row[f"{target_name}_direction"] = (
                "up" if values[index] > 0 else ("down" if values[index] < 0 else "flat")
            )
        for index, target_name in enumerate(AUTOREGRESSIVE_TARGET_NAMES):
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
        row: dict[str, Any] = {"horizon": f"{int(horizon_us) // 1_000_000}s"}
        for target_index, target_name in enumerate(DIRECTION_TARGET_NAMES):
            quantiles = output.horizon_quantiles[0, origin_offset, horizon_index, target_index].double().cpu()
            logit = output.horizon_direction_logits[0, origin_offset, horizon_index, target_index].double().cpu()
            row.update({
                f"{target_name}_bps_q10": float(torch.sinh(quantiles[0]) * 100.0),
                f"{target_name}_bps_q50": float(torch.sinh(quantiles[median_index]) * 100.0),
                f"{target_name}_bps_q90": float(torch.sinh(quantiles[-1]) * 100.0),
                f"{target_name}_direction_logit": float(logit),
                f"{target_name}_probability_up": float(torch.sigmoid(logit)),
                f"{target_name}_predicted_direction": "up" if logit > 0 else "down",
            })
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
