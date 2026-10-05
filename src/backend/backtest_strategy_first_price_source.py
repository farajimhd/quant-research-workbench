"""Bounded certified bars projection for Strategy20's original first setups."""
from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

import numpy as np
import pyarrow as pa

from .backtest_market_data import CertifiedMarketDayPlan, SESSION_OPEN_OFFSET_MS, _literal, assert_select_only
from .backtest_strategy_initial_momentum_growth import CertifiedInitialMomentumGrowthPlan
from .backtest_strategy_initial_ten_percent import CertifiedInitialTenPercentPlan
from .backtest_declared_initial_momentum import CertifiedDeclaredInitialMomentumPlan
from .backtest_strategy_initial_price_break import stage_initial_price_break_plan
from .backtest_strategy_rising_momentum import _frozen
from src.trading_runtime.strategy_initial_price_break import PREMARKET_END_MS

MAX_SOURCE_KEYS = 512
_NAMES = ('bucket_index', 'close_int', 'high_int', 'price_valid', 'extremes_valid')
_TYPES = (pa.uint32(), pa.uint64(), pa.uint64(), pa.uint8(), pa.uint8())


@dataclass(frozen=True, slots=True)
class CertifiedFirstPriceSource:
    market: CertifiedMarketDayPlan
    parent: CertifiedInitialMomentumGrowthPlan | CertifiedInitialTenPercentPlan
    source_attempts: tuple[str, ...]
    requested_mask: np.ndarray
    observations: tuple[np.ndarray, ...]
    token: str

    def __post_init__(self):
        attempts, requested = _authority(self.market, self.parent)
        staged = stage_initial_price_break_plan(self.parent, self.observations)
        if (self.source_attempts != attempts
                or type(self.requested_mask) is not np.ndarray
                or self.requested_mask.dtype != np.bool_
                or not np.array_equal(self.requested_mask, requested)):
            raise ValueError('First price source scope differs from certified parent')
        if any(np.any(column[~requested] != 0) for column in self.observations):
            raise ValueError('Unrequested first price rows contain observations')
        expected = _seal(self.market, self.parent, attempts, requested, staged.token)
        if self.token != expected:
            raise ValueError('First price source content seal differs')
        object.__setattr__(self, 'observations', staged.observations)
        object.__setattr__(self, 'requested_mask', _frozen(requested))


def _authority(market, parent):
    if (type(market) is not CertifiedMarketDayPlan
            or type(parent) not in (CertifiedInitialMomentumGrowthPlan, CertifiedInitialTenPercentPlan, CertifiedDeclaredInitialMomentumPlan)
            or len(market.sessions) != 1
            or 1000 not in market.required_resolutions_ms
            or market.build_id != parent.momentum.source_build_id
            or market.token != parent.momentum.market_plan_token):
        raise ValueError('First price source lacks exact certified market/parent identity')
    units = {(u.session_date, u.ticker): u for u in market.units if u.stage == 'bars'}
    coverage = {(u.session_date, u.ticker): u for u in parent.candidates.coverage}
    if (len(units) != sum(u.stage == 'bars' for u in market.units)
            or len(coverage) != len(parent.candidates.coverage)):
        raise ValueError('First price source attempts are ambiguous')
    attempts = []
    for ticker, _ in parent.momentum.keys:
        scope = (market.sessions[0], ticker)
        unit, candidate = units.get(scope), coverage.get(scope)
        if (unit is None or candidate is None or unit.build_id != market.build_id
                or unit.attempt_id != candidate.source_attempts[0]):
            raise ValueError('First price bars attempt differs from candidate authority')
        try:
            valid = str(UUID(unit.attempt_id)) == unit.attempt_id and UUID(unit.attempt_id).int != 0
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise ValueError('First price bars attempt is not a canonical nonzero UUID')
        attempts.append(unit.attempt_id)
    # Shape (N,): request only original anchors referenced by parent-admitted PM
    # rows. This selection precedes price comparison and portfolio admission.
    boundaries = np.fromiter((key[1] for key in parent.momentum.keys), dtype=np.int64)
    first = parent.initial.first_indices[parent.eligible_mask]
    requested = np.zeros(len(boundaries), dtype=np.bool_)
    requested[first[first >= 0]] = True
    requested &= boundaries < PREMARKET_END_MS
    return tuple(attempts), requested


def _seal(market, parent, attempts, requested, staged_token):
    digest = sha256((market.build_id + market.token + parent.token + staged_token).encode())
    digest.update(repr(attempts).encode())
    digest.update(requested.tobytes())
    return digest.hexdigest()


def load_first_price_source(market, parent, *, client):
    attempts, requested = _authority(market, parent)
    n = len(parent.momentum.keys)
    columns = (np.zeros(n, dtype=np.int64), np.zeros(n, dtype=np.int64),
               np.zeros(n, dtype=np.uint64), np.zeros(n, dtype=np.uint64),
               np.zeros(n, dtype=np.bool_), np.zeros(n, dtype=np.bool_))
    keys = parent.momentum.keys
    tickers = np.array([key[0] for key in keys])
    boundaries = np.fromiter((key[1] for key in keys), dtype=np.int64)
    for ticker in np.unique(tickers[requested]):
        ticker = str(ticker)
        rows = np.flatnonzero(requested & (tickers == ticker))
        current = boundaries[rows] // 1000 * 1000
        prior = current - 1000
        buckets = np.unique(np.concatenate((current[current > 0], prior[prior > 0])))
        buckets = (buckets + SESSION_OPEN_OFFSET_MS) // 1000 - 1
        returned = []
        for start in range(0, len(buckets), MAX_SOURCE_KEYS):
            chunk = buckets[start:start + MAX_SOURCE_KEYS]
            sql = assert_select_only('SELECT bucket_index,close_int,high_int,price_valid,extremes_valid '
                'FROM arte.bars_v1 '
                f'WHERE build_id={_literal(market.build_id)} AND session_date=toDate({_literal(market.sessions[0])}) '
                f'AND ticker={_literal(ticker)} AND attempt_id=toUUID({_literal(attempts[int(rows[0])])}) '
                f'AND resolution_ms=1000 AND bucket_index IN ({",".join(str(int(v)) for v in chunk)}) '
                'ORDER BY bucket_index FORMAT ArrowStream')
            stream = client.iter_arrow_record_batches(sql)
            count = 0
            try:
                for batch in stream:
                    if not isinstance(batch, pa.RecordBatch):
                        raise ValueError('First price source is not an Arrow batch')
                    count += batch.num_rows
                    if (tuple(batch.schema.names) != _NAMES or count > len(chunk)
                            or batch.nbytes > 1048576
                            or any(batch.schema.field(name).type != dtype or batch.column(name).null_count
                                   for name, dtype in zip(_NAMES, _TYPES))):
                        raise ValueError('First price source violates exact bounded Arrow columns')
                    values = tuple(batch.column(i).to_numpy(zero_copy_only=False) for i in range(5))
                    if (np.any(~np.isin(values[0], chunk))
                            or np.any(values[3] > 1) or np.any(values[4] > 1)):
                        raise ValueError('First price source returned unrequested keys or malformed flags')
                    returned.append(values)
            finally:
                close = getattr(stream, 'close', None)
                if close is not None:
                    close()
        if returned:
            values = tuple(np.concatenate([v[i] for v in returned]) for i in range(5))
            if len(np.unique(values[0])) != len(values[0]):
                raise ValueError('First price source returned duplicate bars')
            order = np.argsort(values[0])
            clocks = (values[0][order].astype(np.int64) + 1) * 1000 - SESSION_OPEN_OFFSET_MS
            for side, targets in enumerate((current, prior)):
                indices = np.searchsorted(clocks, targets)
                safe = np.minimum(indices, len(clocks) - 1)
                found = (indices < len(clocks)) & (clocks[safe] == targets) & (targets > 0)
                columns[side][rows[found]] = targets[found]
                columns[side + 2][rows[found]] = values[side + 1][order][safe[found]]
                columns[side + 4][rows[found]] = values[side + 3][order][safe[found]].astype(np.bool_)
    staged = stage_initial_price_break_plan(parent, columns)
    return CertifiedFirstPriceSource(market, parent, attempts, requested, columns,
        _seal(market, parent, attempts, requested, staged.token))
