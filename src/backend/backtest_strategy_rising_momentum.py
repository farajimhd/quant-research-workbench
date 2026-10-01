"""Bounded SELECT-only adjacent MACD projection from certified technical attempts."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import UUID
import re

import numpy as np
import pyarrow as pa

from src.backend.backtest_market_data import (
    CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, _literal, assert_select_only,
)
from src.backend.backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from src.trading_runtime.strategy_rising_momentum_entry import rising_momentum_entry_mask
from src.trading_runtime.strategy_rising_momentum_witness import (
    CompletedMomentumObservation, RisingMomentumWitness,
)

MAX_SOURCE_KEYS = 512
_NAMES = ("resolution_ms", "boundary_ms", "macd_line", "macd_signal")


def _seal(build_id, market_token, candidate_token, keys, attempts, arrays):
    digest = sha256((build_id + market_token + candidate_token).encode())
    digest.update(repr(keys).encode())
    digest.update(repr(attempts).encode())
    for array in arrays:
        digest.update(array.tobytes())
    return digest.hexdigest()


def _frozen(value):
    value = np.ascontiguousarray(value)
    return np.frombuffer(value.tobytes(), dtype=value.dtype).reshape(value.shape)


@dataclass(frozen=True, slots=True)
class CertifiedRisingMomentumPlan:
    source_build_id: str
    market_plan_token: str
    candidate_plan_token: str
    keys: tuple[tuple[str, int], ...]
    source_attempts: tuple[str, ...]
    requested_mask: np.ndarray
    current_boundaries_ms: np.ndarray
    prior_boundaries_ms: np.ndarray
    current_line: np.ndarray
    current_signal: np.ndarray
    prior_line: np.ndarray
    prior_signal: np.ndarray
    token: str

    def __post_init__(self):
        arrays = (self.current_boundaries_ms, self.prior_boundaries_ms,
                  self.current_line, self.current_signal, self.prior_line, self.prior_signal)
        identities = set(self.source_attempts)
        try:
            valid_ids = all(str(UUID(value)) == value and UUID(value).int != 0
                            for value in identities)
        except (ValueError, TypeError, AttributeError):
            valid_ids = False
        checks = (
            (len(self.source_attempts) == len(self.keys), "attempt count"),
            (self.requested_mask.dtype == np.bool_
             and self.requested_mask.shape == (len(self.keys),), "requested mask"),
            (valid_ids, "canonical nonzero UUID technical attempts"),
            (isinstance(self.source_build_id, str)
             and re.fullmatch(r"[0-9a-f]{64}", self.source_build_id) is not None,
             "canonical 64hex producer build identity"),
            (all(isinstance(token, str) and re.fullmatch(r"[0-9a-f]{64}", token)
                 for token in (self.market_plan_token, self.candidate_plan_token, self.token)),
             "64hex source tokens"),
            (all(isinstance(ticker, str) and ticker and ticker == ticker.upper()
                 and type(boundary) is int for ticker, boundary in self.keys), "typed candidate keys"),
            (self.keys == tuple(sorted(set(self.keys))), "unique ordered candidate keys"),
            (all(array.shape == (len(self.keys), 2) for array in arrays), "array shape"),
            (self.token == _seal(self.source_build_id, self.market_plan_token,
                                self.candidate_plan_token, self.keys, self.source_attempts, (self.requested_mask, *arrays)),
             "source/array content seal"),
        )
        failed = [reason for valid, reason in checks if not valid]
        if failed:
            raise ValueError("Momentum sealed arrays differ from candidate source: "
                             + ", ".join(failed))
        if (any(np.any(array[~self.requested_mask] != 0) for array in arrays[:2])
                or any(np.any(~np.isnan(array[~self.requested_mask])) for array in arrays[2:])):
            raise ValueError("Unrequested momentum candidates contain source evidence")
        for name, array in zip(("current_boundaries_ms", "prior_boundaries_ms",
                                "current_line", "current_signal", "prior_line", "prior_signal"), arrays):
            object.__setattr__(self, name, _frozen(array))
        object.__setattr__(self, "requested_mask", _frozen(self.requested_mask))
        self.eligible_mask()

    def eligible_mask(self, strategy_number: int = 13) -> np.ndarray:
        if type(strategy_number) is not int or strategy_number not in (13, 14, 15, 16, 17):
            raise ValueError("Momentum plan has no installed numbered rule")
        rule = rising_momentum_entry_mask
        if strategy_number == 17:
            from src.trading_runtime.strategy_strong_ten_second_momentum import strong_ten_second_momentum_entry_mask
            rule = strong_ten_second_momentum_entry_mask
        return rule(
            np.array([key[1] for key in self.keys], dtype=np.int64),
            self.current_boundaries_ms, self.prior_boundaries_ms,
            self.current_line, self.current_signal, self.prior_line, self.prior_signal)

    def lookup(self, ticker: str, boundary_ms: int) -> RisingMomentumWitness:
        # Only admitted sparse proposals materialize scalar witnesses.
        from bisect import bisect_left
        index = bisect_left(self.keys, (ticker, boundary_ms))
        if index >= len(self.keys) or self.keys[index] != (ticker, boundary_ms):
            raise ValueError("Momentum witness is outside certified candidate keys")
        if not self.requested_mask[index]:
            raise ValueError("Momentum witness was not requested from certified source")
        observations = tuple(CompletedMomentumObservation(
            resolution, int(self.current_boundaries_ms[index, branch]),
            int(self.prior_boundaries_ms[index, branch]),
            *(None if np.isnan(values[index, branch]) else float(values[index, branch])
              for values in (self.current_line, self.current_signal,
                             self.prior_line, self.prior_signal)))
            for branch, resolution in enumerate((1_000, 10_000)))
        return RisingMomentumWitness(ticker, boundary_ms, self.source_build_id,
                                     self.source_attempts[index],
                                     self.market_plan_token, observations)


def load_rising_momentum_plan(market: CertifiedMarketDayPlan,
                             candidates: CertifiedCandidatePlan, *,
                             client: Any, candidate_indices: np.ndarray | None = None) -> CertifiedRisingMomentumPlan:
    """Read at most 512 exact source keys per query; no old-bucket fallback.

    Full technical coverage was certified by market preflight. Candidate
    coverage must pin those same attempts before a projected key may be read.
    Arrow Float64 values retain the producer comparison precision.
    """
    if (not isinstance(market, CertifiedMarketDayPlan)
            or not isinstance(candidates, CertifiedCandidatePlan)
            or candidates.source_build_id != market.build_id
            or len(market.sessions) != 1
            or not {1_000, 10_000} <= set(market.required_resolutions_ms)
            or len(market.token) != 64 or len(candidates.token) != 64):
        raise ValueError("Momentum source lacks certified market/candidate identity")
    units = {(unit.session_date, unit.ticker): unit for unit in market.units
             if unit.stage == "technical"}
    coverage = {(row.session_date, row.ticker): row for row in candidates.coverage}
    if (len(units) != sum(unit.stage == "technical" for unit in market.units)
            or len(coverage) != len(candidates.coverage)):
        raise ValueError("Momentum technical attempts are ambiguous")
    keys = tuple((row.ticker, int(boundary)) for row in candidates.prepared
                 for boundary in row.boundary_ms)
    if keys != tuple(sorted(set(keys))):
        raise ValueError("Momentum candidate keys are repeated or unordered")
    count = len(keys)
    requested_mask = np.ones(count, dtype=np.bool_)
    if candidate_indices is not None:
        indices = np.asarray(candidate_indices)
        if (indices.ndim != 1 or indices.dtype != np.int64
                or np.any(indices < 0) or np.any(indices >= count)
                or np.any(indices[1:] <= indices[:-1])):
            raise ValueError("Momentum requested indices are not a distinct ordered candidate subset")
        requested_mask[:] = False
        requested_mask[indices] = True
    clocks = [np.zeros((count, 2), dtype=np.int64) for _ in range(2)]
    values = [np.full((count, 2), np.nan, dtype=np.float64) for _ in range(4)]
    attempts = []
    offset = 0
    for prepared in candidates.prepared:
        scope = (market.sessions[0], prepared.ticker)
        unit, authority = units.get(scope), coverage.get(scope)
        if (unit is None or authority is None or unit.build_id != market.build_id
                or authority.source_attempts[1] != unit.attempt_id):
            raise ValueError("Momentum technical attempt differs from candidate coverage")
        full_count = len(prepared.boundary_ms)
        row_indices = np.flatnonzero(requested_mask[offset:offset + full_count])
        boundaries = np.asarray(prepared.boundary_ms, dtype=np.int64)[row_indices]
        expected = boundaries[:, None] // np.array([1_000, 10_000]) * np.array([1_000, 10_000])
        prior = expected - np.array([1_000, 10_000])
        requested = np.unique(np.concatenate([
            np.column_stack((np.full(len(boundaries), resolution), targets[:, branch]))
            for branch, resolution in enumerate((1_000, 10_000))
            for targets in (expected, prior)]), axis=0)
        requested = requested[requested[:, 1] > 0]
        returned = []
        for start in range(0, len(requested), MAX_SOURCE_KEYS):
            chunk = requested[start:start + MAX_SOURCE_KEYS]
            pairs = ",".join(f"({int(resolution)},{(int(clock) + SESSION_OPEN_OFFSET_MS) // int(resolution) - 1})"
                             for resolution, clock in chunk)
            sql = assert_select_only(f"SELECT resolution_ms,"
                f"(toInt64(bucket_index)+1)*resolution_ms-{SESSION_OPEN_OFFSET_MS} AS boundary_ms,"
                f"macd_line,macd_signal FROM arte.indicators_v1 "
                f"WHERE build_id={_literal(unit.build_id)} AND session_date=toDate({_literal(scope[0])}) "
                f"AND ticker={_literal(scope[1])} AND attempt_id=toUUID({_literal(unit.attempt_id)}) "
                f"AND (resolution_ms,bucket_index) IN ({pairs}) "
                "ORDER BY resolution_ms,boundary_ms FORMAT ArrowStream")
            stream = client.iter_arrow_record_batches(sql)
            batch_count = 0
            try:
                for batch in stream:
                    if not isinstance(batch, pa.RecordBatch):
                        raise ValueError("Momentum source is not an Arrow batch")
                    batch_count += batch.num_rows
                    if (tuple(batch.schema.names) != _NAMES
                            or batch_count > len(chunk) or batch.nbytes > 1_048_576
                            or any(not pa.types.is_integer(batch.schema.field(name).type)
                                   or batch.column(name).null_count
                                   for name in ("resolution_ms", "boundary_ms"))
                            or not all(pa.types.is_float64(batch.schema.field(name).type)
                                       for name in ("macd_line", "macd_signal"))):
                        raise ValueError("Momentum Arrow source violates bounded exact columns")
                    returned.append(tuple(batch.column(i).to_numpy(zero_copy_only=False)
                                          for i in range(4)))
            finally:
                close = getattr(stream, "close", None)
                if close is not None:
                    close()
        if returned:
            columns = [np.concatenate([batch[i] for batch in returned]) for i in range(4)]
            source_keys = np.column_stack(columns[:2]).astype(np.int64)
            if (len(np.unique(source_keys, axis=0)) != len(source_keys)
                    or not set(map(tuple, source_keys)) <= set(map(tuple, requested))):
                raise ValueError("Momentum source returned duplicate or unrequested clocks")
            for branch, resolution in enumerate((1_000, 10_000)):
                mask = source_keys[:, 0] == resolution
                source_clocks = source_keys[mask, 1]
                order = np.argsort(source_clocks)
                source_clocks = source_clocks[order]
                for side, targets in enumerate((expected[:, branch], prior[:, branch])):
                    indices = np.searchsorted(source_clocks, targets)
                    safe = np.minimum(indices, max(0, len(source_clocks) - 1))
                    found = indices < len(source_clocks)
                    if len(source_clocks):
                        found &= source_clocks[safe] == targets
                        clocks[side][offset + row_indices[found], branch] = targets[found]
                        for field in range(2):
                            values[side * 2 + field][offset + row_indices[found], branch] = columns[field + 2][mask][order][safe[found]]
        # Missing current observations cannot expose an isolated older prior.
        missing = clocks[0][offset:offset + full_count] == 0
        clocks[1][offset:offset + full_count][missing] = 0
        for value in values[2:]:
            value[offset:offset + full_count][missing] = np.nan
        attempts.extend([unit.attempt_id] * full_count)
        offset += full_count
    result = CertifiedRisingMomentumPlan(market.build_id, market.token, candidates.token,
        keys, tuple(attempts), _frozen(requested_mask), *(_frozen(array) for array in (*clocks, *values)),
        _seal(market.build_id, market.token, candidates.token, keys, tuple(attempts), (requested_mask, *clocks, *values)))
    result.eligible_mask()
    return result
