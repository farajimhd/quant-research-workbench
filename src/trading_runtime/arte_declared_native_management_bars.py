"""Independent completed management bars from certified producer attempts.

This reads sparse source observations, never creates bars/indicators or attests
held quantities, prior protection, profit arms, V7 geometry or cash.
"""
from dataclasses import dataclass, asdict, replace
from hashlib import sha256
from math import isfinite

import pyarrow as pa

from .arte_declared_native_managed_sources import PreparedDeclaredManagedSourceResolver
from .journal_contract import canonical_json
from src.backend.backtest_market_data import SESSION_OPEN_OFFSET_MS, _literal
from src.backend.backtest_declared_native_fixed_plan import _arrow


@dataclass(frozen=True, slots=True)
class DeclaredManagementBar:
    boundary_ms: int
    resolution_ms: int
    close_int: int
    high_int: int
    low_int: int
    trade_count: int
    price_valid: bool
    extremes_valid: bool
    macd_line: float | None
    macd_signal: float | None


@dataclass(frozen=True, slots=True)
class DeclaredManagementBars:
    run_id: str
    configuration_hash: str
    market_plan_token: str
    source_build_id: str
    bars_attempt_id: str
    technical_attempt_id: str
    session_date: str
    ticker: str
    boundary_ms: int
    five_second: tuple[DeclaredManagementBar | None, ...]
    ten_second: DeclaredManagementBar | None
    content_hash: str

    def payload(self):
        value = asdict(self)
        value.pop('content_hash')
        return value


def _momentum(value):
    if not isfinite(value):
        if value != value:  # Producer NaN denotes unavailability, not zero.
            return None
        raise ValueError('Declared completed management MACD is infinite')
    return value


def read_declared_management_bars(resolver, ticker, boundary_ms):
    """Read exact four completed 5s observations and the completed 10s bar.

    Cardinalities/resolutions are the current inherited management input
    contract. Optional missing sparse rows remain None; zero trade counts stay
    observed zero. Every query retains the actual certified attempt identity.
    """
    if type(resolver) is not PreparedDeclaredManagedSourceResolver:
        raise ValueError('Declared management bars require the full managed source')
    native, _, envelope, end = resolver._binding()
    if (type(ticker) is not str or ticker not in resolver.market.tickers
            or type(boundary_ms) is not int or not 0 < boundary_ms <= end or boundary_ms % 100):
        raise ValueError('Declared management bar scope is outside its actual source fence')
    client = resolver.client
    if client.execute("SELECT getSetting('readonly')").strip() != '1':
        raise ValueError('Declared management bars require a SELECT-only source principal')
    units = {stage: tuple(u for u in resolver.market.units if u.ticker == ticker and u.stage == stage)
             for stage in ('bars', 'technical')}
    if any(len(rows) != 1 for rows in units.values()):
        raise ValueError('Declared management bar attempts are missing or ambiguous')
    bars_attempt = units['bars'][0].attempt_id
    technical_attempt = units['technical'][0].attempt_id
    common = (f'build_id={_literal(resolver.market.build_id)} '
              f'AND session_date=toDate({_literal(native["session_date"])}) AND ticker={_literal(ticker)}')
    def observations(resolution, clocks):
        buckets = {(clock + SESSION_OPEN_OFFSET_MS)//resolution - 1: clock for clock in clocks if clock > 0}
        if not buckets:
            return tuple(None for _ in clocks)
        selection = ','.join(map(str, sorted(buckets)))
        bar_names = ('bucket_index', 'resolution_ms', 'close_int', 'high_int', 'low_int',
                     'trade_count', 'price_valid', 'extremes_valid')
        bar_types = (pa.uint32(), pa.uint32(), pa.uint64(), pa.uint64(), pa.uint64(),
                     pa.uint64(), pa.uint8(), pa.uint8())
        sql = (f'SELECT {",".join(bar_names)} FROM arte.bars_v1 WHERE {common} '
               f'AND attempt_id=toUUID({_literal(bars_attempt)}) AND resolution_ms={resolution} '
               f'AND bucket_index IN ({selection}) ORDER BY bucket_index FORMAT ArrowStream')
        bars = _arrow(client, sql, bar_names, bar_types, set(buckets))
        names = ('bucket_index', 'resolution_ms', 'macd_line', 'macd_signal')
        types = (pa.uint32(), pa.uint32(), pa.float64(), pa.float64())
        sql = (f'SELECT {",".join(names)} FROM arte.indicators_v1 WHERE {common} '
               f'AND attempt_id=toUUID({_literal(technical_attempt)}) AND resolution_ms={resolution} '
               f'AND bucket_index IN ({selection}) ORDER BY bucket_index FORMAT ArrowStream')
        indicators = _arrow(client, sql, names, types, set(buckets))
        momentum = {}
        for bucket, observed_resolution, line, signal in indicators:
            if observed_resolution != resolution:
                raise ValueError('Declared management indicator resolution differs')
            momentum[bucket] = (_momentum(line), _momentum(signal))
        observed = {}
        for bucket, observed_resolution, close, high, low, count, valid, extremes in bars:
            if observed_resolution != resolution or valid not in (0, 1) or extremes not in (0, 1):
                raise ValueError('Declared management bar resolution or flags differ')
            if valid and close <= 0 or extremes and not 0 < low <= high:
                raise ValueError('Declared management valid bar geometry is malformed')
            if valid and extremes and not low <= close <= high:
                raise ValueError('Declared management close is outside source extremes')
            line, signal = momentum.get(bucket, (None, None))
            observed[buckets[bucket]] = DeclaredManagementBar(buckets[bucket], resolution,
                close, high, low, count, bool(valid), bool(extremes), line, signal)
        if set(momentum) - {row[0] for row in bars}:
            raise ValueError('Declared management indicator lacks its observed source bar')
        return tuple(observed.get(clock) for clock in clocks)
    five_end = boundary_ms//5000*5000
    five = observations(5000, tuple(five_end - i*5000 for i in (3, 2, 1, 0)))
    ten, = observations(10000, (boundary_ms//10000*10000,))
    value = DeclaredManagementBars(resolver.run_id, envelope['payload_hash'], resolver.market.token,
        resolver.market.build_id, bars_attempt, technical_attempt, native['session_date'], ticker,
        boundary_ms, five, ten, '')
    return replace(value, content_hash=sha256(canonical_json(value.payload()).encode()).hexdigest())


def compare_declared_management_bars(resolver, claimed):
    if type(claimed) is not DeclaredManagementBars:
        raise ValueError('Declared management bar claim has a foreign type')
    expected = read_declared_management_bars(resolver, claimed.ticker, claimed.boundary_ms)
    from .declared_native_management_command import _equal
    # Compare the complete typed tree, not just its serialized scalar values.
    if not _equal(claimed, expected):
        raise ValueError('Declared management bars differ from independently reloaded producers')
    return expected
