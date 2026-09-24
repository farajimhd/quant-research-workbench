"""Inactive, read-only HOD cold-replay pin and parity contract.

The caller must supply externally certified complete, ordered persisted frame
rows. This module never queries ClickHouse and cannot make a live source complete.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from math import isfinite
from types import SimpleNamespace
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from src.backend.fixed_v7_stream import FixedV7Stream
from src.market_engine.historical_level_checkpoint import digest as seed_digest
from src.market_engine.streaming_level_book import VERSION as V7_VERSION
from src.trading_runtime.arte_journal_schema import TableContract
from src.trading_runtime.historical_hod import observe_frame
from src.trading_runtime.journal_contract import canonical_json


_NY = ZoneInfo("America/New_York")


MANIFEST_TABLE = TableContract(
    "trading_assignment_hod_replay_pin_v1",
    (("run_id", "String"), ("assignment_id", "String"),
     ("ticker", "String"), ("session", "Date"),
     ("as_of_us", "UInt64"), ("last_completed_1s_us", "UInt64"),
     ("last_completed_5s_us", "UInt64"),
     ("market_build_id", "String"), ("bars_attempt_id", "String"),
     ("indicators_attempt_id", "String"), ("liquidity_attempt_id", "String"),
     ("market_source_plan_hash", "FixedString(64)"),
     ("market_coverage_hash", "FixedString(64)"),
     ("frame_source_revision", "String"),
     ("seed_checkpoint_hash", "FixedString(64)"),
     ("seed_source_plan_hash", "FixedString(64)"),
     ("seed_coverage_hash", "FixedString(64)"),
     ("split_evidence_hash", "FixedString(64)"),
     ("v7_version", "String"), ("hod_parameters_hash", "FixedString(64)"),
     ("replay_code_revision", "String"),
     ("frame_count", "UInt32"), ("frame_set_hash", "FixedString(64)"),
     ("expected_v7_levels_hash", "FixedString(64)"),
     ("expected_hod_state_hash", "FixedString(64)"),
     ("content_hash", "FixedString(64)")),
    "toYYYYMM(session)", "run_id, assignment_id, ticker, session, as_of_us",
)


@dataclass(frozen=True, slots=True)
class HodReplayFrame:
    """One externally pinned completed frame in actual producer order."""

    sequence: int
    timeframe: str
    completed_at_us: int
    open_int: int
    high_int: int
    low_int: int
    close_int: int
    volume: float
    macd_line: float | None
    macd_signal: float | None
    execution_vwap: float | None
    atr_14: float | None


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _hex(value: Any, name: str) -> str:
    if (not isinstance(value, str) or len(value) != 64
            or any(char not in "0123456789abcdef" for char in value)):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


def _positive_name(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value:
        raise ValueError(f"{name} must be nonempty single-line identity")
    return value


def _frames(frames: Sequence[HodReplayFrame], *, session: str,
            as_of_us: int) -> tuple[list[dict[str, Any]], int, int]:
    if not isinstance(frames, (list, tuple)) or not frames or len(frames) > 115_200:
        raise ValueError("HOD replay frames must be a bounded nonempty sequence")
    previous = -1
    last = {"1s": 0, "5s": 0}
    rows = []
    for ordinal, frame in enumerate(frames):
        if not isinstance(frame, HodReplayFrame) or frame.sequence != ordinal:
            raise ValueError("HOD frame sequence is missing or reordered")
        if frame.timeframe not in last or type(frame.completed_at_us) is not int:
            raise ValueError("unsupported HOD frame timeframe/clock")
        at = frame.completed_at_us
        if (at % 1_000_000 or at < previous or at <= last[frame.timeframe]
                or at > as_of_us or datetime.fromtimestamp(at / 1_000_000, timezone.utc)
                .astimezone(_NY).date().isoformat() != session):
            raise ValueError("HOD completed frame clock is noncausal")
        if frame.timeframe == "5s" and at % 5_000_000:
            raise ValueError("HOD 5s frame must close on five-second boundary")
        prices = (frame.open_int, frame.high_int, frame.low_int, frame.close_int)
        if (any(type(value) is not int or value <= 0 for value in prices)
                or frame.low_int > min(frame.open_int, frame.close_int)
                or frame.high_int < max(frame.open_int, frame.close_int)):
            raise ValueError("invalid HOD completed OHLC")
        for name in ("volume", "macd_line", "macd_signal", "execution_vwap", "atr_14"):
            value = getattr(frame, name)
            if value is not None and (type(value) not in (int, float) or not isfinite(value)):
                raise ValueError(f"invalid HOD {name}")
        if frame.volume < 0 or frame.execution_vwap is not None and frame.execution_vwap <= 0:
            raise ValueError("invalid HOD volume/VWAP")
        previous = at
        last[frame.timeframe] = at
        rows.append(asdict(frame))
    return rows, last["1s"], last["5s"]


def replay_hod_state(*, ticker: str, session: str, as_of_us: int,
                     seed: Mapping[str, Any], splits: Sequence[dict[str, Any]],
                     frames: Sequence[HodReplayFrame],
                     parameters: Mapping[str, Any]) -> tuple[dict[str, Any], str]:
    """Run the actual V7 and passive HOD producer, without market I/O."""
    _positive_name(ticker, "ticker")
    session_day = date.fromisoformat(session)
    _frames(frames, session=session, as_of_us=as_of_us)
    if not isinstance(parameters, Mapping) or not isinstance(parameters.get("historical_hod"), Mapping):
        raise ValueError("resolved HOD parameters are required")
    if seed.get("ticker") != ticker or seed.get("checkpoint_hash") != seed_digest({
        key: value for key, value in seed.items() if key != "checkpoint_hash"
    }):
        raise ValueError("V7 seed identity or digest mismatch")
    stream = FixedV7Stream(seed, ticker=ticker, session=session_day, splits=splits)
    state: dict[str, Any] = {}
    for item in frames:
        at = datetime.fromtimestamp(item.completed_at_us / 1_000_000, timezone.utc)
        prices = {key: getattr(item, key + "_int") / 10_000.0
                  for key in ("open", "high", "low", "close")}
        if item.timeframe == "1s":
            stream.update_second({"resolution_ms": 1000, "price_valid": 1,
                                  "extremes_valid": 1, "open_int": item.open_int,
                                  "high_int": item.high_int, "low_int": item.low_int,
                                  "close_int": item.close_int, "volume": item.volume}, at=at)
        context = stream.context(as_of=at, price=prices["close"])
        frame = SimpleNamespace(as_of=at, timeframe=item.timeframe,
                                bar={**prices, "volume": item.volume},
                                indicator={"macd_line": item.macd_line,
                                           "macd_signal": item.macd_signal,
                                           "execution_vwap": item.execution_vwap,
                                           "atr_14": item.atr_14,
                                           "qmd_structure_session_high": context["qmd_structure_session_high"]})
        state = observe_frame(frame, state, dict(parameters),
                              snapshot={"unified_levels": context["qmd_structure_unified_levels"],
                                        "session_high": context["qmd_structure_session_high"]})
    as_of = datetime.fromtimestamp(as_of_us / 1_000_000, timezone.utc)
    level_context = stream.context(as_of=as_of)
    return state, _hash(level_context["qmd_structure_unified_levels"])


def project_hod_replay_manifest(
    *, run_id: str, assignment_id: str, ticker: str, session: str,
    as_of_us: int, market_build_id: str, bars_attempt_id: str,
    indicators_attempt_id: str, liquidity_attempt_id: str,
    market_source_plan_hash: str, market_coverage_hash: str,
    frame_source_revision: str, seed_source_plan_hash: str,
    seed_coverage_hash: str, replay_code_revision: str,
    seed: Mapping[str, Any], splits: Sequence[dict[str, Any]],
    frames: Sequence[HodReplayFrame], parameters: Mapping[str, Any],
    expected_v7_levels_hash: str, expected_hod_state_hash: str,
) -> dict[str, Any]:
    """Seal source pins and independent expected replay digests."""
    for name, value in (("run_id", run_id), ("assignment_id", assignment_id),
                        ("ticker", ticker), ("market_build_id", market_build_id),
                        ("bars_attempt_id", bars_attempt_id),
                        ("indicators_attempt_id", indicators_attempt_id),
                        ("liquidity_attempt_id", liquidity_attempt_id),
                        ("frame_source_revision", frame_source_revision),
                        ("replay_code_revision", replay_code_revision)):
        _positive_name(value, name)
    if type(as_of_us) is not int or as_of_us <= 0:
        raise ValueError("invalid HOD as-of clock")
    date.fromisoformat(session)
    frame_rows, last_1s, last_5s = _frames(frames, session=session, as_of_us=as_of_us)
    if not last_1s or not last_5s:
        raise ValueError("HOD replay requires both completed 1s and 5s frame coverage")
    if not isinstance(seed, Mapping) or seed.get("ticker") != ticker:
        raise ValueError("HOD V7 seed ticker mismatch")
    for name, value in (("market_source_plan_hash", market_source_plan_hash),
                        ("market_coverage_hash", market_coverage_hash),
                        ("seed_source_plan_hash", seed_source_plan_hash),
                        ("seed_coverage_hash", seed_coverage_hash),
                        ("seed_checkpoint_hash", seed.get("checkpoint_hash")),
                        ("expected_v7_levels_hash", expected_v7_levels_hash),
                        ("expected_hod_state_hash", expected_hod_state_hash)):
        _hex(value, name)
    if not isinstance(parameters, Mapping) or not isinstance(parameters.get("historical_hod"), Mapping):
        raise ValueError("resolved HOD parameters are required")
    row = dict(run_id=run_id, assignment_id=assignment_id, ticker=ticker,
               session=session, as_of_us=as_of_us,
               last_completed_1s_us=last_1s, last_completed_5s_us=last_5s,
               market_build_id=market_build_id, bars_attempt_id=bars_attempt_id,
               indicators_attempt_id=indicators_attempt_id,
               liquidity_attempt_id=liquidity_attempt_id,
               market_source_plan_hash=market_source_plan_hash,
               market_coverage_hash=market_coverage_hash,
               frame_source_revision=frame_source_revision,
               seed_checkpoint_hash=seed["checkpoint_hash"],
               seed_source_plan_hash=seed_source_plan_hash,
               seed_coverage_hash=seed_coverage_hash,
               split_evidence_hash=_hash(splits), v7_version=V7_VERSION,
               hod_parameters_hash=_hash(parameters),
               replay_code_revision=replay_code_revision,
               frame_count=len(frame_rows), frame_set_hash=_hash(frame_rows),
               expected_v7_levels_hash=expected_v7_levels_hash,
               expected_hod_state_hash=expected_hod_state_hash)
    return {**row, "content_hash": _hash(row)}


def verify_hod_cold_replay(manifest: Mapping[str, Any], *,
                           seed: Mapping[str, Any], splits: Sequence[dict[str, Any]],
                           frames: Sequence[HodReplayFrame],
                           parameters: Mapping[str, Any]) -> dict[str, Any]:
    """Return recovered state only after every pin and output digest matches."""
    if not isinstance(manifest, Mapping) or set(manifest) != {key for key, _ in MANIFEST_TABLE.columns}:
        raise ValueError("incomplete HOD replay manifest")
    if manifest["content_hash"] != _hash({key: value for key, value in manifest.items()
                                           if key != "content_hash"}):
        raise ValueError("tampered HOD replay manifest")
    candidate = project_hod_replay_manifest(
        **{key: manifest[key] for key in (
            "run_id", "assignment_id", "ticker", "session", "as_of_us",
            "market_build_id", "bars_attempt_id", "indicators_attempt_id",
            "liquidity_attempt_id", "market_source_plan_hash", "market_coverage_hash",
            "frame_source_revision", "seed_source_plan_hash", "seed_coverage_hash",
            "replay_code_revision", "expected_v7_levels_hash", "expected_hod_state_hash")},
        seed=seed, splits=splits, frames=frames, parameters=parameters,
    )
    if candidate != manifest:
        raise ValueError("HOD replay source pin or frame set mismatch")
    state, level_hash = replay_hod_state(
        ticker=manifest["ticker"], session=manifest["session"],
        as_of_us=manifest["as_of_us"], seed=seed, splits=splits,
        frames=frames, parameters=parameters,
    )
    if level_hash != manifest["expected_v7_levels_hash"] or _hash(state) != manifest["expected_hod_state_hash"]:
        raise ValueError("HOD cold replay parity mismatch")
    return state
