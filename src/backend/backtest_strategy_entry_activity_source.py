"""Prepared source-bound entry activity projection; no numbered admission."""
from dataclasses import dataclass
from bisect import bisect_left
from hashlib import sha256

import numpy as np
import pyarrow as pa

from .backtest_market_data import CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, _literal, assert_select_only
from .backtest_strategy_certified_price_break import CertifiedInitialPriceBreakPlan
from .backtest_strategy_rising_momentum import _frozen
from src.trading_runtime.strategy_entry_activity_fade import (
    POLICY_ID, AFTERHOURS_START_MS, entry_activity_mask,
)

MAX_SOURCE_KEYS = 512
_NAMES = ('bucket_index', 'resolution_ms', 'trade_count')
_TYPES = (pa.uint32(), pa.uint32(), pa.uint64())


def _authority(market, parent):
    if (type(market) is not CertifiedMarketDayPlan
            or type(parent) is not CertifiedInitialPriceBreakPlan
            or len(market.sessions) != 1 or 5000 not in market.required_resolutions_ms
            or market.build_id != parent.source.market.build_id
            or market.token != parent.source.market.token):
        raise ValueError('Entry activity requires exact certified market and price parent')
    units = {(u.session_date, u.ticker): u for u in market.units if u.stage == 'bars'}
    coverage = {(u.session_date, u.ticker): u for u in parent.candidates.coverage}
    if (len(units) != sum(u.stage == 'bars' for u in market.units)
            or len(coverage) != len(parent.candidates.coverage)):
        raise ValueError('Entry activity source attempts are ambiguous')
    attempts = []
    for ticker, _ in parent.momentum.keys:
        scope = (market.sessions[0], ticker)
        unit, candidate = units.get(scope), coverage.get(scope)
        if (unit is None or candidate is None or unit.build_id != market.build_id
                or unit.attempt_id != candidate.source_attempts[0]):
            raise ValueError('Entry activity bars attempt differs from certified candidate')
        attempts.append(unit.attempt_id)
    boundaries = np.asarray([key[1] for key in parent.momentum.keys], dtype=np.int64)
    opening = np.where(boundaries >= AFTERHOURS_START_MS, AFTERHOURS_START_MS, 0)
    requested = parent.eligible_mask & (boundaries >= opening + 20_000)
    return tuple(attempts), requested


def _selection(parent, clocks, counts, observed):
    keys = parent.momentum.keys
    tickers = np.asarray([key[0] for key in keys])
    boundaries = np.asarray([key[1] for key in keys], dtype=np.int64)
    expected = ((boundaries // 5000 * 5000)[:, None]
                - np.asarray([15000, 10000, 5000, 0], dtype=np.int64))
    if np.any(observed & (clocks != expected)):
        raise ValueError('Entry activity observed clock differs from requested completion')
    result = np.zeros(len(keys), dtype=np.bool_)
    # Decisions are ordered per ticker, not across independent ticker clocks.
    for ticker in np.unique(tickers):
        rows = np.flatnonzero(tickers == ticker)
        result[rows] = entry_activity_mask(boundaries[rows], clocks[rows], counts[rows], observed[rows])
    return parent.eligible_mask & result


def _seal(market, parent, attempts, requested, clocks, counts, observed):
    digest = sha256((POLICY_ID + market.build_id + market.token + parent.token + repr(attempts)).encode())
    for array in (requested, clocks, counts, observed):
        digest.update(array.tobytes())
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class CertifiedEntryActivityPlan:
    market: CertifiedMarketDayPlan
    parent: CertifiedInitialPriceBreakPlan
    source_attempts: tuple[str, ...]
    requested_mask: np.ndarray
    candle_boundaries_ms: np.ndarray
    trade_counts: np.ndarray
    observed: np.ndarray
    eligible_mask: np.ndarray
    token: str

    def __post_init__(self):
        attempts, requested = _authority(self.market, self.parent)
        n = len(self.parent.momentum.keys)
        arrays = (self.candle_boundaries_ms, self.trade_counts, self.observed)
        if (self.source_attempts != attempts
                or type(self.requested_mask) is not np.ndarray
                or self.requested_mask.dtype != np.bool_
                or not np.array_equal(self.requested_mask, requested)
                or any(type(a) is not np.ndarray or a.shape != (n, 4) for a in arrays)
                or any(a.dtype != dtype for a, dtype in zip(arrays, (np.int64, np.uint64, np.bool_)))):
            raise ValueError('Entry activity source arrays differ from certified parent scope')
        if (np.any(self.candle_boundaries_ms[~self.observed] != -1)
                or np.any(self.trade_counts[~self.observed] != 0)
                or np.any(self.observed[~requested])):
            raise ValueError('Entry activity unobserved values contain fabricated evidence')
        expected = _selection(self.parent, *arrays)
        if (type(self.eligible_mask) is not np.ndarray or self.eligible_mask.dtype != np.bool_
                or not np.array_equal(self.eligible_mask, expected)):
            raise ValueError('Entry activity eligibility differs from source comparison')
        if self.token != _seal(self.market, self.parent, attempts, requested, *arrays):
            raise ValueError('Entry activity content seal differs')
        for name in ('requested_mask', 'candle_boundaries_ms', 'trade_counts', 'observed', 'eligible_mask'):
            object.__setattr__(self, name, _frozen(getattr(self, name)))

    def witness(self, ticker, boundary_ms):
        """Produce scalar evidence only for independently admitted source keys."""
        from src.trading_runtime.strategy_entry_activity_fade import EntryActivityCandle
        from src.trading_runtime.strategy_entry_activity_witness import (
            EntryActivityWitness, validate_entry_activity_witness,
        )
        if type(ticker) is not str or type(boundary_ms) is not int:
            raise ValueError('Entry activity lookup needs exact typed candidate identity')
        key = (ticker, boundary_ms)
        index = bisect_left(self.parent.momentum.keys, key)
        if (index >= len(self.parent.momentum.keys) or self.parent.momentum.keys[index] != key
                or not self.eligible_mask[index]):
            raise ValueError('Entry activity lookup is outside admitted source keys')
        candles = tuple(EntryActivityCandle(int(self.candle_boundaries_ms[index, column]),
                                          int(self.trade_counts[index, column]))
                        if self.observed[index, column] else None for column in range(4))
        return validate_entry_activity_witness(EntryActivityWitness(
            ticker, self.market.sessions[0], boundary_ms, self.market.build_id,
            self.source_attempts[index], self.market.token, self.token,
            self.parent.candidates.token, self.parent.entry.token, self.parent.token, candles))


@dataclass(frozen=True, slots=True)
class EntryActivityReadbackAuthority:
    """Use row identity only; all observed values come from the certified plan."""
    run_id: str
    plan: CertifiedEntryActivityPlan

    def __post_init__(self):
        from .backtest_strategy_certified_price_break import _source_parent_number
        if (type(self.run_id) is not str or not self.run_id
                or type(self.plan) is not CertifiedEntryActivityPlan):
            raise ValueError('Entry activity readback requires exact run and source plan')
        # Strategy36 must inherit the same ten-percent first-setup authority as
        # its pinned Strategy35 parent, never the legacy fifty-percent plan.
        _source_parent_number(self.plan.parent, 35)

    def resolve(self, run_id, entries, intents):
        from uuid import NAMESPACE_URL, uuid5
        from src.trading_runtime.arte_entry_activity_v4 import EntryActivityAuthority, activity_event_instant
        if run_id != self.run_id:
            raise ValueError('Entry activity readback differs from certified run')
        parents = {row['record_id']: row for row in intents}
        if len(parents) != len(intents):
            raise ValueError('Entry activity readback has duplicate intent parents')
        result, seen = [], set()
        for row in entries:
            if row['strategy_number'] != 36:
                continue
            parent = row['parent_record_id']
            intent = parents.get(parent)
            if (type(row['strategy_number']) is not int or parent in seen or intent is None
                    or row['run_id'] != run_id or intent['run_id'] != run_id
                    or row['batch_id'] != intent['batch_id']
                    or row['event_month'] != intent['event_month']
                    or intent['action'] != 'enter_long' or intent['reason'] != 'strategy_one_entry'):
                raise ValueError('Entry activity readback has unrelated entry or intent scope')
            key = (intent['ticker'], row['boundary_ms'])
            witness = self.plan.witness(*key)
            selection = self.plan.parent.selection_witness(*key)
            month = activity_event_instant(witness).date().replace(day=1).isoformat()
            if (row['episode_start_ms'] != selection.initial.episode_start_ms
                    or str(row['event_month']) != month):
                raise ValueError('Entry activity readback differs from native clock or episode')
            identity = (f"strategy-36:{witness.session_date}:{row['assignment_id']}:"
                        f"{intent['account_id']}:{witness.ticker}:{witness.boundary_ms}:"
                        f"{selection.initial.episode_start_ms}")
            if intent['intent_id'] != str(uuid5(NAMESPACE_URL, identity)):
                raise ValueError('Entry activity parent differs from exact numbered intent identity')
            result.append(EntryActivityAuthority(parent, witness, selection.initial.episode_start_ms))
            seen.add(parent)
        return tuple(result)


def load_entry_activity_plan(market, parent, *, client):
    """Read only parent survivors in bounded exact-key Arrow projections.

    Arrays have shape (candidate_count,4); all unrequested or missing slots
    retain separate absence flags. One source observation can serve multiple
    decisions, but it is never carried into a different completed clock.
    """
    attempts, requested = _authority(market, parent)
    keys = parent.momentum.keys
    n = len(keys)
    clocks = np.full((n, 4), -1, dtype=np.int64)
    counts = np.zeros((n, 4), dtype=np.uint64)
    observed = np.zeros((n, 4), dtype=np.bool_)
    tickers = np.asarray([key[0] for key in keys])
    boundaries = np.asarray([key[1] for key in keys], dtype=np.int64)
    for ticker in np.unique(tickers[requested]):
        rows = np.flatnonzero(requested & (tickers == ticker))
        targets = ((boundaries[rows] // 5000 * 5000)[:, None]
                   - np.asarray([15000, 10000, 5000, 0], dtype=np.int64))
        buckets = np.unique((targets + SESSION_OPEN_OFFSET_MS) // 5000 - 1)
        returned = []
        for start in range(0, len(buckets), MAX_SOURCE_KEYS):
            chunk = buckets[start:start + MAX_SOURCE_KEYS]
            query = assert_select_only('SELECT bucket_index,resolution_ms,trade_count FROM arte.bars_v1 '
                f'WHERE build_id={_literal(market.build_id)} AND session_date=toDate({_literal(market.sessions[0])}) '
                f'AND ticker={_literal(str(ticker))} AND attempt_id=toUUID({_literal(attempts[int(rows[0])])}) '
                f'AND resolution_ms=5000 AND bucket_index IN ({",".join(str(int(b)) for b in chunk)}) '
                'ORDER BY bucket_index FORMAT ArrowStream')
            stream = client.iter_arrow_record_batches(query)
            returned_count = 0
            try:
                for batch in stream:
                    if not isinstance(batch, pa.RecordBatch):
                        raise ValueError('Entry activity source is not an Arrow batch')
                    returned_count += batch.num_rows
                    if (tuple(batch.schema.names) != _NAMES or returned_count > len(chunk)
                            or batch.nbytes > 1048576
                            or any(batch.schema.field(name).type != dtype or batch.column(name).null_count
                                   for name, dtype in zip(_NAMES, _TYPES))):
                        raise ValueError('Entry activity source violates bounded exact Arrow schema')
                    columns = tuple(batch.column(i).to_numpy(zero_copy_only=False) for i in range(3))
                    if np.any(~np.isin(columns[0], chunk)) or np.any(columns[1] != 5000):
                        raise ValueError('Entry activity source returned an unrequested key or resolution')
                    if batch.num_rows:
                        returned.append(columns)
            finally:
                close = getattr(stream, 'close', None)
                if close is not None:
                    close()
        if returned:
            source_buckets = np.concatenate([a[0] for a in returned])
            source_counts = np.concatenate([a[2] for a in returned])
            if len(np.unique(source_buckets)) != len(source_buckets):
                raise ValueError('Entry activity source returned duplicate candles')
            order = np.argsort(source_buckets)
            source_clocks = (source_buckets[order].astype(np.int64) + 1) * 5000 - SESSION_OPEN_OFFSET_MS
            indices = np.searchsorted(source_clocks, targets)
            safe = np.minimum(indices, len(source_clocks) - 1)
            found = (indices < len(source_clocks)) & (source_clocks[safe] == targets)
            clocks[rows] = np.where(found, targets, -1)
            counts[rows] = np.where(found, source_counts[order][safe], np.uint64(0))
            observed[rows] = found
    eligible = _selection(parent, clocks, counts, observed)
    return CertifiedEntryActivityPlan(market, parent, attempts, requested, clocks, counts, observed,
                                     eligible, _seal(market, parent, attempts, requested, clocks, counts, observed))
