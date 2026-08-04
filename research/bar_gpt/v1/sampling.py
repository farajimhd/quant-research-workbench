from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Iterable
from zoneinfo import ZoneInfo

import torch

from research.bar_gpt.v1.data import BarGPTExample
from research.bar_gpt.v1.schema import SESSION_TIMEZONE


SESSION_PHASES: tuple[str, ...] = (
    "premarket",
    "regular_open",
    "regular_midday",
    "regular_close",
    "after_hours",
)


@dataclass(frozen=True, slots=True)
class CoverageCursor:
    unit_index: int
    block_offset: int


@dataclass(frozen=True, slots=True)
class CoveragePlanSummary:
    start_date: str
    end_date: str
    training_tickers: tuple[str, ...]
    months: int
    units: int
    blocks_per_unit: int
    fetch_candidate_blocks: int
    emit_blocks_per_chunk: int
    origin_bars: int
    epochs: int
    expected_blocks: int
    expected_origins: int
    plan_hash: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def coverage_plan_summary(
    *,
    start_date: str,
    end_date: str,
    training_tickers: tuple[str, ...],
    blocks_per_unit: int,
    origin_bars: int,
    epochs: int,
    seed: int,
    fetch_candidate_blocks: int = 16,
    emit_blocks_per_chunk: int = 16,
) -> CoveragePlanSummary:
    start = dt.date.fromisoformat(start_date)
    end = dt.date.fromisoformat(end_date)
    months = 0
    cursor = start
    while cursor < end:
        cursor = min(end, (cursor.replace(day=28) + dt.timedelta(days=4)).replace(day=1))
        months += 1
    units = months * len(training_tickers)
    expected_blocks = units * int(blocks_per_unit) * int(epochs)
    payload = {
        "version": 2,
        "start_date": start_date,
        "end_date": end_date,
        "training_tickers": training_tickers,
        "blocks_per_unit": int(blocks_per_unit),
        "fetch_candidate_blocks": int(fetch_candidate_blocks),
        "emit_blocks_per_chunk": int(emit_blocks_per_chunk),
        "origin_bars": int(origin_bars),
        "epochs": int(epochs),
        "seed": int(seed),
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return CoveragePlanSummary(
        start_date=start_date,
        end_date=end_date,
        training_tickers=training_tickers,
        months=months,
        units=units,
        blocks_per_unit=int(blocks_per_unit),
        fetch_candidate_blocks=int(fetch_candidate_blocks),
        emit_blocks_per_chunk=int(emit_blocks_per_chunk),
        origin_bars=int(origin_bars),
        epochs=int(epochs),
        expected_blocks=expected_blocks,
        expected_origins=expected_blocks * int(origin_bars),
        plan_hash=digest,
    )


def session_phase(example: BarGPTExample) -> str:
    timestamp_us = int(example.origin_timestamps_us[0])
    local = dt.datetime.fromtimestamp(timestamp_us / 1_000_000, tz=dt.timezone.utc).astimezone(
        ZoneInfo(SESSION_TIMEZONE)
    )
    second = local.hour * 3600 + local.minute * 60 + local.second
    if second < 9 * 3600 + 30 * 60:
        return "premarket"
    if second < 10 * 3600 + 30 * 60:
        return "regular_open"
    if second < 15 * 3600:
        return "regular_midday"
    if second < 16 * 3600:
        return "regular_close"
    return "after_hours"


def has_condition_target(example: BarGPTExample) -> bool:
    if not bool(torch.any(example.target_condition_flags > 0)):
        return False
    maximum_steps = max(example.horizons_us) // int(example.base_timeframe_us)
    flags = example.target_condition_flags.gt(0).any(dim=-1).to(torch.int64)
    prefix = torch.cat((flags.new_zeros(1), flags.cumsum(0)))
    origins = example.support_origin_indices
    ends = (origins + int(maximum_steps) + 1).clamp(max=flags.shape[0])
    starts = (origins + 1).clamp(max=flags.shape[0])
    return bool(torch.any(prefix[ends] - prefix[starts] > 0))


def _score(example: BarGPTExample, seed: int) -> int:
    origin = int(example.raw_view_start_us["1s"][int(example.origin_indices[0])])
    payload = f"{seed}:{example.ticker}:{example.local_date}:{origin}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big", signed=False)


def _retain_best(values: list[tuple[int, BarGPTExample]], candidate: tuple[int, BarGPTExample], limit: int) -> None:
    values.append(candidate)
    values.sort(key=lambda item: item[0])
    del values[max(0, int(limit)) :]


def _would_retain(values: list[tuple[int, BarGPTExample]], score: int, limit: int) -> bool:
    return len(values) < limit or (bool(values) and score < values[-1][0])


def _materialize(example: BarGPTExample) -> BarGPTExample:
    """Detach a retained block from full-session Arrow tensors before the month advances."""
    return BarGPTExample(
        ticker=example.ticker,
        local_date=example.local_date,
        raw_views={name: value.clone() for name, value in example.raw_views.items()},
        raw_view_start_us={name: value.clone() for name, value in example.raw_view_start_us.items()},
        origin_indices=example.origin_indices.clone(),
        origin_timestamps_us=example.origin_timestamps_us.clone(),
        asof_indices={name: value.clone() for name, value in example.asof_indices.items()},
        target_support=example.target_support.clone(),
        target_share_factors=example.target_share_factors.clone(),
        target_condition_flags=example.target_condition_flags.clone(),
        support_origin_indices=example.support_origin_indices.clone(),
        horizons_us=example.horizons_us,
        base_timeframe_us=example.base_timeframe_us,
        activity_regime=example.activity_regime,
        worker_id=example.worker_id,
        unit_index=example.unit_index,
        block_offset=example.block_offset,
        session_phase=example.session_phase,
        has_condition_target=example.has_condition_target,
    )


def select_stratified_examples(
    source: Iterable[BarGPTExample],
    *,
    limit: int,
    seed: int,
    balance_activity_regimes: bool,
) -> list[BarGPTExample]:
    """Select a bounded deterministic coverage set without retaining a full ticker-month."""
    if limit <= 0:
        raise ValueError("coverage selection limit must be positive")
    global_best: list[tuple[int, BarGPTExample]] = []
    phase_best: dict[str, tuple[int, BarGPTExample]] = {}
    stratum_best: dict[tuple[str, int], tuple[int, BarGPTExample]] = {}
    condition_best: list[tuple[int, BarGPTExample]] = []
    for example in source:
        phase = session_phase(example)
        condition = has_condition_target(example)
        score = _score(example, seed)
        phase_item = phase_best.get(phase)
        stratum_key = (phase, int(example.activity_regime))
        stratum_item = stratum_best.get(stratum_key)
        retained = (
            _would_retain(global_best, score, limit)
            or phase_item is None
            or score < phase_item[0]
            or stratum_item is None
            or score < stratum_item[0]
            or (condition and _would_retain(condition_best, score, 1))
        )
        if not retained:
            continue
        example.session_phase = phase
        example.has_condition_target = condition
        compact = _materialize(example)
        scored = (score, compact)
        _retain_best(global_best, scored, limit)
        if phase not in phase_best or scored[0] < phase_best[phase][0]:
            phase_best[phase] = scored
        if stratum_key not in stratum_best or scored[0] < stratum_best[stratum_key][0]:
            stratum_best[stratum_key] = scored
        if condition:
            _retain_best(condition_best, scored, 1)

    selected: list[tuple[int, BarGPTExample]] = []
    seen: set[int] = set()

    def take(item: tuple[int, BarGPTExample] | None) -> None:
        if item is None or len(selected) >= limit or id(item[1]) in seen:
            return
        selected.append(item)
        seen.add(id(item[1]))

    for item in condition_best:
        take(item)
    for phase in SESSION_PHASES:
        take(phase_best.get(phase))
    if balance_activity_regimes:
        for regime in range(3):
            for phase in SESSION_PHASES:
                take(stratum_best.get((phase, regime)))
    for item in global_best:
        take(item)
    selected.sort(key=lambda item: item[0])
    return [example for _score_value, example in selected]
