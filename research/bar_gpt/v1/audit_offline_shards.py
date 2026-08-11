from __future__ import annotations

import argparse
import datetime as dt
import dataclasses
import json
import os
import time
from pathlib import Path
from typing import Any, Sequence

import torch

from research.bar_gpt.v1.config import DataConfig
from research.bar_gpt.v1.data import TIMEFRAME_US_BY_NAME
from research.bar_gpt.v1.direct_event_shards import DirectEventArrowStreamClient
from research.bar_gpt.v1.features import MODEL_FEATURE_NAMES
from research.bar_gpt.v1.offline_shards import (
    OFFLINE_SHARD_CONTRACT_VERSION,
    _atomic_json,
    _sha256,
    condition_positive_counts,
    load_shard,
)
from research.bar_gpt.v1.schema import FEATURE_VERSION
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files
from research.bar_gpt.v1.targets import (
    AUTOREGRESSIVE_TARGET_NAMES,
    OHLC_FIELDS,
    PRICE_FAMILIES,
    TARGET_NAMES,
)


DEFAULT_PILOT_ROOT = Path(r"D:\TradingML\runtimes\bar_gpt\v1\offline_shards_v9_pilot")


def _csv(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip().upper() for item in value.split(",") if item.strip()))


def _require_ohlc_geometry(values: torch.Tensor, mask: torch.Tensor, label: str) -> None:
    """OHLC returns share one family base, so transformed ordering is invariant."""
    for family_index, family in enumerate(PRICE_FAMILIES):
        first = family_index * len(OHLC_FIELDS)
        family_values = values[..., first : first + len(OHLC_FIELDS)]
        family_masks = mask[..., first : first + len(OHLC_FIELDS)]
        open_value, high_value, low_value, close_value = family_values.unbind(dim=-1)
        open_mask, high_mask, low_mask, close_mask = family_masks.unbind(dim=-1)
        tolerance = 1e-6
        invalid = (
            (high_mask & open_mask & (high_value + tolerance < open_value))
            | (high_mask & close_mask & (high_value + tolerance < close_value))
            | (low_mask & open_mask & (low_value - tolerance > open_value))
            | (low_mask & close_mask & (low_value - tolerance > close_value))
        )
        _require(not bool(invalid.any()), f"{label}/{family}: invalid OHLC return geometry")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail-closed structural and causal audit of a bounded BarGPT offline-shard sample."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_PILOT_ROOT)
    parser.add_argument("--tickers", default="", help="Optional comma-separated ticker filter.")
    parser.add_argument("--start-date", default="", help="Optional inclusive YYYY-MM-DD month boundary.")
    parser.add_argument("--end-date", default="", help="Optional exclusive YYYY-MM-DD month boundary.")
    parser.add_argument("--max-shards", type=int, default=2)
    parser.add_argument(
        "--verify-sha256",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Verify the full tensor-file digest recorded by each sidecar.",
    )
    parser.add_argument(
        "--require-calendar-context",
        action="store_true",
        help="Require every audited origin to have complete 1D, 1W, and 1MO context.",
    )
    parser.add_argument(
        "--verify-direct-source",
        action="store_true",
        help="Rebuild eligible trade timestamps from compact events and match every audited origin.",
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.max_shards <= 0:
        parser.error("--max-shards must be positive")
    if bool(args.start_date) != bool(args.end_date):
        parser.error("--start-date and --end-date must be provided together")
    if args.start_date:
        start = dt.date.fromisoformat(str(args.start_date))
        end = dt.date.fromisoformat(str(args.end_date))
        if start.day != 1 or end.day != 1 or start >= end:
            parser.error("date filters must be non-empty month boundaries")
    return args


def discover_sidecars(
    root: Path,
    *,
    tickers: Sequence[str] = (),
    start_date: str = "",
    end_date: str = "",
    limit: int = 2,
) -> tuple[Path, ...]:
    allowed = {ticker.upper() for ticker in tickers}
    start = dt.date.fromisoformat(start_date) if start_date else None
    end = dt.date.fromisoformat(end_date) if end_date else None
    selected: list[Path] = []
    for path in sorted(root.glob("tickers/*/*/*.json")):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("status") != "complete":
            continue
        unit_key = str(value.get("unit_key", ""))
        try:
            ticker, month = unit_key.split(":", 1)
            month_date = dt.date.fromisoformat(f"{month}-01")
        except ValueError as exc:
            raise RuntimeError(f"invalid unit_key in {path}: {unit_key!r}") from exc
        if allowed and ticker.upper() not in allowed:
            continue
        if start is not None and not (start <= month_date < end):
            continue
        selected.append(path)
        if len(selected) >= int(limit):
            break
    if not selected:
        raise RuntimeError(f"no complete shard sidecars matched beneath {root}")
    return tuple(selected)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _strictly_increasing(value: torch.Tensor) -> bool:
    return value.numel() <= 1 or bool(torch.all(value[1:] > value[:-1]))


def audit_shard(
    sidecar_path: Path,
    *,
    verify_sha256: bool = True,
    require_calendar_context: bool = False,
    max_absolute_return_bps: float | None = None,
) -> dict[str, Any]:
    # Compatibility-only argument for older programmatic callers. Return
    # magnitude is intentionally not bounded; valid extreme moves remain data.
    del max_absolute_return_bps
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    unit_key = str(sidecar.get("unit_key", ""))
    shard_path = sidecar_path.with_suffix(".pt")
    _require(sidecar.get("status") == "complete", f"{unit_key}: sidecar is not complete")
    _require(
        int(sidecar.get("contract_version", -1)) == OFFLINE_SHARD_CONTRACT_VERSION,
        f"{unit_key}: sidecar contract is not v{OFFLINE_SHARD_CONTRACT_VERSION}",
    )
    _require(shard_path.is_file(), f"{unit_key}: tensor file is absent: {shard_path}")
    _require(int(sidecar.get("bytes", -1)) == shard_path.stat().st_size, f"{unit_key}: byte count mismatch")
    expected_digest = str(sidecar.get("sha256", ""))
    if verify_sha256:
        _require(bool(expected_digest), f"{unit_key}: certified SHA-256 is absent")
        _require(_sha256(shard_path) == expected_digest, f"{unit_key}: SHA-256 mismatch")
    shard = load_shard(shard_path)
    _require(shard.get("unit_key") == unit_key, f"{unit_key}: payload identity mismatch")
    _require(shard.get("config_hash") == sidecar.get("config_hash"), f"{unit_key}: config hash mismatch")
    sessions = shard.get("sessions")
    _require(isinstance(sessions, list), f"{unit_key}: sessions must be a list")
    expected_views = set(TIMEFRAME_US_BY_NAME)
    context_contract = shard.get("context_contract")
    _require(isinstance(context_contract, dict), f"{unit_key}: context contract is absent")
    _require(context_contract == sidecar.get("context_contract"), f"{unit_key}: context contract mismatch")
    source_authority = context_contract.get("source_authority")
    _require(isinstance(source_authority, dict), f"{unit_key}: source authority is absent")
    _require(
        bool(str(source_authority.get("database", "")).strip())
        and source_authority.get("mode") in {"direct_events", "materialized_bars"}
        and source_authority.get("condition_authority") == "embedded_1s"
        and source_authority.get("one_second_feature_version") == FEATURE_VERSION,
        f"{unit_key}: source authority is incomplete",
    )
    intraday_context = {
        str(name): int(count)
        for name, count in dict(context_contract.get("intraday_context_bars", {})).items()
    }
    calendar_context = {
        str(name): int(count)
        for name, count in dict(context_contract.get("calendar_context_bars", {})).items()
    }
    _require(
        set(intraday_context) == set(DataConfig().intraday_context_by_name),
        f"{unit_key}: intraday context view set mismatch",
    )
    _require(
        set(calendar_context) == set(DataConfig().calendar_context_by_name),
        f"{unit_key}: calendar context view set mismatch",
    )
    expected_warmup = max(
        int(count) * (int(TIMEFRAME_US_BY_NAME[name]) // int(shard.get("base_timeframe_us", 1)))
        for name, count in intraday_context.items()
    )
    _require(
        int(context_contract.get("intraday_warmup_bars_1s", -1)) == expected_warmup,
        f"{unit_key}: derived warmup mismatch",
    )
    horizon_count = len(tuple(shard.get("horizons_us", ())))
    origins = 0
    blocks = 0
    view_rows: dict[str, int] = {name: 0 for name in expected_views}
    feature_accumulators: dict[str, dict[str, torch.Tensor | int]] = {
        name: {
            "rows": 0,
            "nonzero": torch.zeros(len(MODEL_FEATURE_NAMES), dtype=torch.long),
            "sum": torch.zeros(len(MODEL_FEATURE_NAMES), dtype=torch.float64),
            "sum_squared": torch.zeros(len(MODEL_FEATURE_NAMES), dtype=torch.float64),
            "minimum": torch.full((len(MODEL_FEATURE_NAMES),), float("inf"), dtype=torch.float64),
            "maximum": torch.full((len(MODEL_FEATURE_NAMES),), float("-inf"), dtype=torch.float64),
        }
        for name in expected_views
    }
    target_valid = torch.zeros((horizon_count, len(TARGET_NAMES)), dtype=torch.long)
    target_total = torch.zeros_like(target_valid)
    for session_index, session in enumerate(sessions):
        label = f"{unit_key}/session-{session_index}"
        views = session.get("views")
        session_blocks = session.get("blocks")
        _require(isinstance(views, dict) and set(views) == expected_views, f"{label}: view set mismatch")
        _require(isinstance(session_blocks, list) and session_blocks, f"{label}: blocks are absent")
        session_origin_timestamps: list[torch.Tensor] = []
        session_origin_positions: list[torch.Tensor] = []
        observed_block_offsets: list[int] = []
        for name, view in views.items():
            features = view.get("features")
            starts = view.get("start_us")
            ends = view.get("end_us")
            available = view.get("available_at_us")
            _require(isinstance(features, torch.Tensor), f"{label}/{name}: features are absent")
            _require(
                features.ndim == 2 and features.shape[1] == len(MODEL_FEATURE_NAMES),
                f"{label}/{name}: invalid feature shape {tuple(features.shape)}",
            )
            _require(
                isinstance(starts, torch.Tensor) and isinstance(ends, torch.Tensor)
                and isinstance(available, torch.Tensor)
                and starts.shape == ends.shape == available.shape == (features.shape[0],),
                f"{label}/{name}: timestamp shape mismatch",
            )
            _require(_strictly_increasing(starts), f"{label}/{name}: starts are not strictly increasing")
            _require(_strictly_increasing(available), f"{label}/{name}: availability is not strictly increasing")
            unavailable_calendar_sentinel = (
                name in calendar_context
                and features.shape[0] == 1
                and int(starts[0]) == int(ends[0]) == int(available[0]) == 0
            )
            _require(
                unavailable_calendar_sentinel or bool(torch.all(starts < ends)),
                f"{label}/{name}: non-positive bar interval",
            )
            _require(bool(torch.all(available >= ends)), f"{label}/{name}: availability precedes bar end")
            for family in PRICE_FAMILIES:
                present_index = MODEL_FEATURE_NAMES.index(f"{family}_present")
                high_index = MODEL_FEATURE_NAMES.index(f"{family}_high_from_open_return")
                low_index = MODEL_FEATURE_NAMES.index(f"{family}_low_from_open_return")
                present = features[:, present_index] > 0
                _require(
                    not bool((present & (features[:, high_index] < -1e-6)).any()),
                    f"{label}/{name}/{family}: high return is below open",
                )
                _require(
                    not bool((present & (features[:, low_index] > 1e-6)).any()),
                    f"{label}/{name}/{family}: low return is above open",
                )
            if name in intraday_context:
                expected_duration = int(TIMEFRAME_US_BY_NAME[name])
                _require(bool(torch.all(ends - starts == expected_duration)), f"{label}/{name}: bar duration mismatch")
                source_index = MODEL_FEATURE_NAMES.index("log_source_event_count")
                trade_present_index = MODEL_FEATURE_NAMES.index("trade_present")
                _require(
                    bool(torch.all(features[:, source_index] > 0)),
                    f"{label}/{name}: empty-event bar was stored as intraday context",
                )
                _require(
                    bool(torch.all(features[:, trade_present_index] > 0)),
                    f"{label}/{name}: context bar without an eligible trade",
                )
                for condition_name in ("halt_pause", "resume", "news_risk", "luld_limit_state"):
                    present = features[:, MODEL_FEATURE_NAMES.index(f"{condition_name}_present")] > 0
                    counted = features[:, MODEL_FEATURE_NAMES.index(f"log_{condition_name}_count")] > 0
                    _require(
                        torch.equal(present, counted),
                        f"{label}/{name}: {condition_name} presence/count disagreement",
                    )
                ar_targets = view.get("autoregressive_targets")
                ar_mask = view.get("autoregressive_base_mask")
                expected_ar_shape = (max(0, features.shape[0] - 1), len(AUTOREGRESSIVE_TARGET_NAMES))
                _require(
                    isinstance(ar_targets, torch.Tensor) and isinstance(ar_mask, torch.Tensor)
                    and tuple(ar_targets.shape) == expected_ar_shape and ar_mask.shape == ar_targets.shape,
                    f"{label}/{name}: autoregressive target shape mismatch",
                )
                _require(bool(torch.isfinite(ar_targets).all()), f"{label}/{name}: non-finite AR target")
                _require(
                    bool(torch.all(ar_targets[~ar_mask] == 0)),
                    f"{label}/{name}: masked AR target contains a nonzero value",
                )
                _require_ohlc_geometry(ar_targets, ar_mask, f"{label}/{name}/autoregressive")
            accumulator = feature_accumulators[name]
            for left in range(0, int(features.shape[0]), 65_536):
                chunk = features[left : left + 65_536]
                finite = torch.isfinite(chunk)
                _require(bool(finite.all()), f"{label}/{name}: stored features contain non-finite values")
                values = chunk.to(torch.float64)
                accumulator["nonzero"] += torch.count_nonzero(chunk, dim=0)
                accumulator["sum"] += values.sum(dim=0)
                accumulator["sum_squared"] += values.square().sum(dim=0)
                accumulator["minimum"] = torch.minimum(accumulator["minimum"], values.amin(dim=0))
                accumulator["maximum"] = torch.maximum(accumulator["maximum"], values.amax(dim=0))
                accumulator["rows"] += int(chunk.shape[0])
            view_rows[name] += int(features.shape[0])
        for block_index, block in enumerate(session_blocks):
            block_label = f"{label}/block-{block_index}"
            origin_timestamps = block.get("origin_timestamps_us")
            origin_indices = block.get("origin_indices")
            _require(
                isinstance(origin_timestamps, torch.Tensor) and origin_timestamps.ndim == 1
                and origin_timestamps.numel() > 0,
                f"{block_label}: origins are absent",
            )
            _require(_strictly_increasing(origin_timestamps), f"{block_label}: origins are not strictly increasing")
            session_origin_timestamps.append(origin_timestamps)
            observed_block_offsets.append(int(block.get("block_offset", -1)))
            _require(
                isinstance(origin_indices, torch.Tensor) and origin_indices.shape == origin_timestamps.shape,
                f"{block_label}: origin-index shape mismatch",
            )
            slices = block.get("view_slices")
            asof = block.get("asof_indices")
            _require(isinstance(slices, dict) and set(slices) == expected_views, f"{block_label}: slice set mismatch")
            _require(isinstance(asof, dict) and set(asof) == expected_views - {"1s"}, f"{block_label}: as-of set mismatch")
            for name, raw_slice in slices.items():
                start, length = (int(raw_slice[0]), int(raw_slice[1]))
                available = views[name]["available_at_us"]
                _require(start >= 0 and length > 0 and start + length <= available.shape[0], f"{block_label}/{name}: slice out of range")
                if name == "1s":
                    _require(int(origin_indices.min()) >= intraday_context["1s"], f"{block_label}/1s: context underflow")
                    _require(
                        int(origin_indices[0]) == intraday_context["1s"],
                        f"{block_label}/1s: block does not begin with the exact configured context",
                    )
                    _require(int(origin_indices.max()) < length, f"{block_label}/1s: origin outside slice")
                    source_index = MODEL_FEATURE_NAMES.index("log_source_event_count")
                    trade_present_index = MODEL_FEATURE_NAMES.index("trade_present")
                    local_features = views[name]["features"][start : start + length]
                    _require(
                        bool(torch.all(local_features[origin_indices.long(), source_index] > 0)),
                        f"{block_label}/1s: zero-event origin detected",
                    )
                    _require(
                        bool(torch.all(local_features[origin_indices.long(), trade_present_index] > 0)),
                        f"{block_label}/1s: origin without an eligible trade",
                    )
                    local_available = available[start : start + length]
                    session_origin_positions.append(origin_indices.long() + start)
                    _require(
                        torch.equal(local_available[origin_indices.long()], origin_timestamps),
                        f"{block_label}/1s: origin timestamps do not match bar availability",
                    )
                    continue
                indices = asof[name]
                _require(isinstance(indices, torch.Tensor) and indices.shape == origin_timestamps.shape, f"{block_label}/{name}: as-of shape mismatch")
                if name in intraday_context:
                    required = int(intraday_context[name])
                    _require(int(indices.min()) >= required - 1, f"{block_label}/{name}: configured context underflow")
                    _require(
                        int(indices[0]) == required - 1,
                        f"{block_label}/{name}: block does not begin with the exact configured context",
                    )
                elif name in calendar_context:
                    required = int(calendar_context[name])
                    partial = (indices >= 0) & (indices < required - 1)
                    _require(
                        not bool(partial.any()),
                        f"{block_label}/{name}: partial calendar context is exposed",
                    )
                    if require_calendar_context:
                        _require(
                            int(indices.min()) >= required - 1,
                            f"{block_label}/{name}: required calendar context is unavailable",
                        )
                selected = indices >= 0
                if bool(selected.any()):
                    _require(int(indices[selected].max()) < length, f"{block_label}/{name}: as-of index outside slice")
                    local_available = available[start : start + length]
                    _require(
                        bool(torch.all(local_available[indices[selected]] <= origin_timestamps[selected])),
                        f"{block_label}/{name}: future bar is visible",
                    )
            horizon_targets = block.get("horizon_targets")
            horizon_mask = block.get("horizon_mask")
            expected_shape = (origin_timestamps.numel(), horizon_count, len(TARGET_NAMES))
            _require(
                isinstance(horizon_targets, torch.Tensor) and isinstance(horizon_mask, torch.Tensor)
                and tuple(horizon_targets.shape) == expected_shape
                and horizon_mask.shape == horizon_targets.shape,
                f"{block_label}: horizon target shape mismatch",
            )
            _require(bool(torch.isfinite(horizon_targets).all()), f"{block_label}: non-finite horizon target")
            _require(
                bool(torch.all(horizon_targets[~horizon_mask] == 0)),
                f"{block_label}: masked horizon target contains a nonzero value",
            )
            _require_ohlc_geometry(horizon_targets, horizon_mask, f"{block_label}/physical")
            target_valid += horizon_mask.to(torch.long).sum(dim=0)
            target_total += int(origin_timestamps.numel())
            origins += int(origin_timestamps.numel())
            blocks += 1
        combined_origins = torch.cat(session_origin_timestamps)
        combined_positions = torch.cat(session_origin_positions)
        _require(
            _strictly_increasing(combined_origins),
            f"{label}: block origins overlap or are out of order",
        )
        _require(
            combined_positions.numel() <= 1 or bool(torch.all(combined_positions[1:] == combined_positions[:-1] + 1)),
            f"{label}: active one-second origins are skipped between blocks",
        )
        _require(
            observed_block_offsets == list(range(observed_block_offsets[0], observed_block_offsets[0] + len(observed_block_offsets))),
            f"{label}: block offsets are not contiguous",
        )
    counts = shard.get("counts", {})
    recomputed_conditions = condition_positive_counts(sessions)
    _require(int(counts.get("sessions", -1)) == len(sessions), f"{unit_key}: session count mismatch")
    _require(int(counts.get("blocks", -1)) == blocks == int(sidecar.get("blocks", -2)), f"{unit_key}: block count mismatch")
    _require(int(counts.get("origins", -1)) == origins == int(sidecar.get("origins", -2)), f"{unit_key}: origin count mismatch")
    _require(
        tuple(int(value) for value in counts.get("condition_positive_counts", ())) == recomputed_conditions
        == tuple(int(value) for value in sidecar.get("condition_positive_counts", ())),
        f"{unit_key}: condition-positive metadata mismatch",
    )
    feature_coverage: dict[str, Any] = {}
    for name in sorted(expected_views):
        accumulator = feature_accumulators[name]
        rows = int(accumulator["rows"])
        _require(rows == view_rows[name] and rows > 0, f"{unit_key}/{name}: feature scan row mismatch")
        nonzero = accumulator["nonzero"]
        mean = accumulator["sum"] / rows
        variance = (accumulator["sum_squared"] / rows - mean.square()).clamp_min(0.0)
        feature_coverage[name] = {
            "rows_scanned": rows,
            "columns_present": len(MODEL_FEATURE_NAMES),
            "all_finite": True,
            "all_zero_features": [
                feature for feature, count in zip(MODEL_FEATURE_NAMES, nonzero.tolist(), strict=True)
                if int(count) == 0
            ],
            "nonzero_fraction": {
                feature: float(count / rows)
                for feature, count in zip(MODEL_FEATURE_NAMES, nonzero.tolist(), strict=True)
            },
            "standard_deviation": {
                feature: float(value)
                for feature, value in zip(MODEL_FEATURE_NAMES, variance.sqrt().tolist(), strict=True)
            },
            "minimum": {
                feature: float(value)
                for feature, value in zip(MODEL_FEATURE_NAMES, accumulator["minimum"].tolist(), strict=True)
            },
            "maximum": {
                feature: float(value)
                for feature, value in zip(MODEL_FEATURE_NAMES, accumulator["maximum"].tolist(), strict=True)
            },
        }
    return {
        "unit_key": unit_key,
        "path": str(shard_path),
        "bytes": shard_path.stat().st_size,
        "sha256_verified": bool(verify_sha256),
        "sessions": len(sessions),
        "blocks": blocks,
        "origins": origins,
        "view_rows": view_rows,
        "feature_coverage": feature_coverage,
        "condition_positive_counts": list(recomputed_conditions),
        "target_valid_fraction": {
            f"{int(horizon) // 1_000_000}s": {
                name: float(target_valid[horizon_index, target_index] / target_total[horizon_index, target_index].clamp_min(1))
                for target_index, name in enumerate(TARGET_NAMES)
            }
            for horizon_index, horizon in enumerate(shard.get("horizons_us", ()))
        },
        "status": "passed",
    }


def verify_direct_source(sidecar_path: Path) -> dict[str, Any]:
    """Match every audited origin to a reconstructed eligible trade second."""
    from research.bar_gpt.v1.train import _stream_config

    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    _require(
        sidecar.get("context_contract", {}).get("source_authority", {}).get("mode") == "direct_events",
        f"{sidecar_path}: direct-source audit requires a direct_events shard",
    )
    shard = load_shard(sidecar_path.with_suffix(".pt"))
    ticker = str(shard["unit_key"]).split(":", 1)[0]
    sessions = list(shard.get("sessions", ()))
    _require(bool(sessions), f"{ticker}: direct-source verification requires nonempty sessions")
    dates = sorted(str(session["local_date"]) for session in sessions)
    start = dt.date.fromisoformat(dates[0])
    end = dt.date.fromisoformat(dates[-1]) + dt.timedelta(days=1)
    base_config = DataConfig()
    config = dataclasses.replace(
        base_config,
        tickers=(ticker,),
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        validation_start_date=start.isoformat(),
        validation_slices=((ticker, start.isoformat(), end.isoformat()),),
    )
    config.validate()
    client = DirectEventArrowStreamClient(_stream_config(config), config)
    intervals = client.read_identity_intervals(
        (ticker,),
        identity_database=config.identity_database,
        interval_table=config.identity_interval_table,
        entity_table=config.identity_entity_table,
        event_table=config.identity_event_table,
        coverage_start=config.daily_history_start_date,
    )[ticker]
    reconstructed = {
        day: set(int(value) for value in view.available_at_us.tolist())
        for day, view in client.iter_session_views(
            ticker=ticker,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            source_intervals=intervals,
            prefetch_pages=1,
        )
    }
    checked = 0
    for session in sessions:
        day = str(session["local_date"])
        available = reconstructed.get(day, set())
        _require(bool(available), f"{ticker} {day}: no direct eligible-trade bars were reconstructed")
        for block in session["blocks"]:
            origins = [int(value) for value in block["origin_timestamps_us"].tolist()]
            missing = [value for value in origins if value not in available]
            _require(not missing, f"{ticker} {day}: {len(missing)} origins lack a direct eligible trade")
            checked += len(origins)
    return {
        "ticker": ticker,
        "sessions": len(sessions),
        "origins_checked": checked,
        "source": "compact events reconstructed with direct trade eligibility",
        "status": "passed",
    }


def run_audit(
    root: Path,
    *,
    tickers: Sequence[str] = (),
    start_date: str = "",
    end_date: str = "",
    limit: int = 2,
    verify_sha256: bool = True,
    require_calendar_context: bool = False,
    output: Path | None = None,
    verify_direct_events: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    sidecars = discover_sidecars(
        root,
        tickers=tickers,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
    )
    audited = [
        audit_shard(
            path,
            verify_sha256=verify_sha256,
            require_calendar_context=require_calendar_context,
        )
        for path in sidecars
    ]
    direct_source = [verify_direct_source(path) for path in sidecars] if verify_direct_events else []
    payload = {
        "created_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "root": str(root),
        "contract_version": OFFLINE_SHARD_CONTRACT_VERSION,
        "require_calendar_context": bool(require_calendar_context),
        "elapsed_seconds": time.perf_counter() - started,
        "audited_shards": len(audited),
        "status": "passed",
        "shards": audited,
        "direct_source_verification": direct_source,
    }
    destination = output or (
        root / "manifest" / "audits" / f"audit-{dt.datetime.now():%Y%m%d-%H%M%S}-p{os.getpid()}.json"
    )
    _atomic_json(destination, payload)
    payload["output"] = str(destination)
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.verify_direct_source:
        load_env_files(discover_clickhouse_env_files())
    report = run_audit(
        args.root.resolve(),
        tickers=_csv(str(args.tickers)),
        start_date=str(args.start_date),
        end_date=str(args.end_date),
        limit=int(args.max_shards),
        verify_sha256=bool(args.verify_sha256),
        require_calendar_context=bool(args.require_calendar_context),
        output=args.output.resolve() if args.output is not None else None,
        verify_direct_events=bool(args.verify_direct_source),
    )
    for shard in report["shards"]:
        print(
            f"PASS {shard['unit_key']} blocks={shard['blocks']:,} origins={shard['origins']:,} "
            f"conditions={shard['condition_positive_counts']}",
            flush=True,
        )
    print(f"offline shard audit passed: {report['output']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
