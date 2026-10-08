"""Typed producer packet for causal native channels; never execution approval."""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from pathlib import Path
import re

import polars as pl
import pyarrow as pa

from src.backend.backtest_market_data import CertifiedMarketDayPlan, FIXED_RESOLUTIONS_MS
from src.market_engine.completed_endpoint_return_contract import require_hash, require_uuid, table_hash
from src.trading_runtime.journal_contract import canonical_json

FEATURE_TABLE = 'arte.native_causal_channels_v1'
COVERAGE_TABLE = 'arte.native_causal_channel_coverage_v1'
STORAGE_POLICY = 'live_market_ssd'
HASH_DOMAIN = 'native-causal-channel-producer:utf8-lf@1'
MAX_SOURCE_ROWS = 2_000_000
MAX_TICKERS = 8


def require_bars_output_hash(value):
    """Canonical ARTE sum(cityHash64(tuple(*))) UInt64 text, not SHA-256."""
    if (type(value) is not str or re.fullmatch(r'0|[1-9][0-9]{0,19}', value) is None or
            int(value) > 2**64 - 1):
        raise ValueError('Canonical UInt64 bars output checksum required')


BASE_FIELDS = [
    *((n, pa.string()) for n in ('build_id', 'session_date', 'ticker', 'attempt_id')),
    ('resolution_ms', pa.uint32()), ('bucket_index', pa.uint32()),
    *((n, pa.uint64()) for n in ('open_int', 'high_int', 'low_int', 'close_int')),
    ('execution_volume', pa.float64()), ('execution_notional', pa.float64()),
    ('trade_count', pa.uint64()), ('price_valid', pa.uint8()), ('extremes_valid', pa.uint8()),
]
SOURCE_SCHEMA = pa.schema([pa.field(n, t, nullable=False) for n, t in BASE_FIELDS])
RELATIVE_FIELDS = (
    'open_return', 'high_return', 'low_return', 'close_return', 'relative_execution_volume',
    'body_return', 'upper_wick_return', 'lower_wick_return', 'prior_return_volatility',
    'relative_trade_count', 'open_volatility_units', 'high_volatility_units',
    'low_volatility_units', 'close_volatility_units', 'body_volatility_units',
    'upper_wick_volatility_units', 'lower_wick_volatility_units',
)
FEATURE_SCHEMA = pa.schema([
    *SOURCE_SCHEMA,
    pa.field('feature_attempt_id', pa.string(), nullable=False),
    pa.field('policy_digest', pa.string(), nullable=False),
    pa.field('available_day_boundary_ms', pa.int64(), nullable=False),
    pa.field('candle_available', pa.bool_(), nullable=False),
    pa.field('volatility_baseline_available', pa.bool_(), nullable=False),
    pa.field('bar_duration_seconds', pa.float64(), nullable=False),
    pa.field('source_day_fraction', pa.float64(), nullable=False),
    *[pa.field(n, pa.float64()) for n in RELATIVE_FIELDS],
])
COVERAGE_SCHEMA = pa.schema([pa.field(n, t, nullable=False) for n, t in [
    *((n, pa.string()) for n in ('build_id', 'session_date', 'ticker', 'attempt_id',
        'feature_attempt_id', 'policy_digest', 'bars_source_hash', 'bars_output_hash',
        'market_plan_token', 'producer_source_hash_domain', 'producer_source_hash', 'content_hash')),
    ('resolution_ms', pa.uint32()), ('through_day_boundary_ms', pa.uint32()),
    ('row_count', pa.uint32()), ('candle_count', pa.uint32()),
]])


@dataclass(frozen=True, slots=True)
class NativeChannelPolicy:
    resolutions_ms: tuple[int, ...]
    decision_interval_ms: int
    freshness_by_resolution: tuple[tuple[int, int], ...]
    participation_lookback_bars: int = 5
    volatility_lookback_bars: int = 5

    def __post_init__(self):
        r = self.resolutions_ms
        if (type(r) is not tuple or not r or
                any(type(v) is not int or v not in FIXED_RESOLUTIONS_MS for v in r) or
                r != tuple(sorted(set(r)))):
            raise ValueError('Ordered unique native source resolutions required')
        if type(self.decision_interval_ms) is not int or self.decision_interval_ms not in FIXED_RESOLUTIONS_MS:
            raise ValueError('Declared native decision interval required')
        if (type(self.freshness_by_resolution) is not tuple or
                len(self.freshness_by_resolution) != len(r) or
                any(type(p) is not tuple or len(p) != 2 for p in self.freshness_by_resolution) or
                tuple(p[0] for p in self.freshness_by_resolution) != r or
                any(type(p[0]) is not int or type(p[1]) is not int or p[1] < 0
                    for p in self.freshness_by_resolution)):
            raise ValueError('Explicit freshness for every source resolution required')
        if (type(self.participation_lookback_bars) is not int or self.participation_lookback_bars < 1 or
                type(self.volatility_lookback_bars) is not int or self.volatility_lookback_bars < 2):
            raise ValueError('Prior-only participation/volatility lookbacks required')

    def payload(self):
        self.__post_init__()
        return dict(contract='native-causal-channel@1', source='arte.bars_v1',
            resolutions_ms=self.resolutions_ms, decision_interval_ms=self.decision_interval_ms,
            freshness_by_resolution=self.freshness_by_resolution,
            participation_lookback_bars=self.participation_lookback_bars,
            volatility_lookback_bars=self.volatility_lookback_bars,
            clock='America/New_York source midnight; completed bucket end',
            returns='OHLC / immediately prior consecutive valid close - 1',
            participation='current value / mean of N prior consecutive source bars; current excluded',
            volatility='prior N consecutive close returns population std; current excluded',
            missing='null; no gap bridging, imputation, forming bars or cross-attempt borrowing',
            absolute='native integer OHLC and source participation retained',
            timing='completed duration seconds and source-day fraction',
            fundamentals=False, splits=False, news=False, hash_domain=HASH_DOMAIN)

    @property
    def digest(self):
        return sha256(canonical_json(self.payload()).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class NativeChannelRequest:
    market: CertifiedMarketDayPlan
    session_date: str
    tickers: tuple[str, ...]
    through_day_boundary_ms: int
    policy: NativeChannelPolicy

    def __post_init__(self):
        m = self.market
        if type(m) is not CertifiedMarketDayPlan or type(self.policy) is not NativeChannelPolicy:
            raise ValueError('Exact market plan and typed native channel policy required')
        self.policy.__post_init__()
        if type(self.session_date) is not str or self.session_date not in m.sessions:
            raise ValueError('Feature day differs from market certificate')
        date.fromisoformat(self.session_date)
        if (type(self.tickers) is not tuple or not 1 <= len(self.tickers) <= MAX_TICKERS or
                any(type(t) is not str for t in self.tickers) or
                self.tickers != tuple(sorted(set(self.tickers))) or
                not set(self.tickers).issubset(m.tickers)):
            raise ValueError('Bounded ordered certified ticker scope required')
        if (type(self.through_day_boundary_ms) is not int or
                not 0 <= self.through_day_boundary_ms <= 86400000 or
                not set(self.policy.resolutions_ms).issubset(m.required_resolutions_ms)):
            raise ValueError('Completed source boundary or certified resolution differs')
        if self.maximum_source_rows > MAX_SOURCE_ROWS:
            raise ValueError('Native feature source packet exceeds row bound')
        for value in (m.build_id, m.definition_hash, m.token):
            require_hash(value)
        for ticker in self.tickers:
            u = self.unit(ticker)
            require_uuid(u.attempt_id)
            require_hash(u.source_hash)
            require_bars_output_hash(u.output_hash)
            if u.build_id != m.build_id or type(u.output_rows) is not int or u.output_rows <= 0:
                raise ValueError('Feature source lacks nonempty certified bars coverage')

    @property
    def maximum_source_rows(self):
        return len(self.tickers) * sum(self.through_day_boundary_ms // r for r in self.policy.resolutions_ms)

    def unit(self, ticker):
        found = [u for u in self.market.units if u.stage == 'bars' and
                 u.session_date == self.session_date and u.ticker == ticker]
        if len(found) != 1:
            raise ValueError('Feature bars source is missing or ambiguous')
        return found[0]


def producer_implementation_hash():
    root = Path(__file__).resolve().parents[2]
    paths = ('src/market_engine/native_causal_channel_contract.py',
             'pipelines/market_sip/events/native_causal_channel_producer.py',
             'research/causal_strategy_features/v3/native_bars.py',
             'research/causal_strategy_features/v4/native_bars.py',
             'src/market_engine/completed_endpoint_return_contract.py',
             'pipelines/market_sip/events/completed_endpoint_return_producer.py',
             'src/backend/backtest_market_data.py',
             'src/trading_runtime/journal_contract.py')
    digest = sha256((HASH_DOMAIN + '\0').encode())
    for path in paths:
        text = (root / path).read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
        digest.update(path.encode() + b'\0' + text.encode() + b'\0')
    return digest.hexdigest()


def projection_token(request, attempt, rows, coverage):
    return sha256((request.market.token + request.policy.digest + attempt +
                   str(request.through_day_boundary_ms) + table_hash(rows) + table_hash(coverage)).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class NativeChannelProjection:
    request: NativeChannelRequest
    feature_attempt_id: str
    rows: pa.Table
    coverage: pa.Table
    token: str

    def __post_init__(self):
        if type(self.request) is not NativeChannelRequest:
            raise ValueError('Typed native feature request required')
        self.request.__post_init__()
        require_uuid(self.feature_attempt_id)
        for table, schema in ((self.rows, FEATURE_SCHEMA), (self.coverage, COVERAGE_SCHEMA)):
            if type(table) is not pa.Table or table.schema.remove_metadata() != schema:
                raise ValueError('Foreign or lossy native feature schema')
            if any(table[f.name].null_count for f in schema if not f.nullable):
                raise ValueError('Required native feature fields are missing')
        r, c = pl.from_arrow(self.rows), pl.from_arrow(self.coverage)
        if r.height > self.request.maximum_source_rows:
            raise ValueError('Feature rows exceed completed source bound')
        keys = ['ticker', 'resolution_ms', 'bucket_index']
        if r.select(keys).n_unique() != r.height or not r.equals(r.sort(keys)):
            raise ValueError('Native feature keys are duplicate or out of order')
        fixed = dict(build_id=self.request.market.build_id, session_date=self.request.session_date,
                     feature_attempt_id=self.feature_attempt_id, policy_digest=self.request.policy.digest)
        for frame in (r, c):
            if any(frame.filter(pl.col(n) != value).height for n, value in fixed.items()):
                raise ValueError('Feature identity differs from declared request')
        expected_end = (pl.col('bucket_index').cast(pl.Int64) + 1) * pl.col('resolution_ms')
        if r.filter(~pl.col('resolution_ms').is_in(self.request.policy.resolutions_ms) |
                    ~pl.col('ticker').is_in(self.request.tickers) |
                    (pl.col('available_day_boundary_ms') != expected_end) |
                    (expected_end > self.request.through_day_boundary_ms) |
                    (pl.col('bar_duration_seconds') != pl.col('resolution_ms') / 1000) |
                    (pl.col('source_day_fraction') != expected_end / 86400000)).height:
            raise ValueError('Feature scope, completed availability or timing differs')
        geometry = ((pl.col('price_valid') == 1) & (pl.col('extremes_valid') == 1) &
            (pl.col('low_int') > 0) & pl.col('open_int').is_between(pl.col('low_int'), pl.col('high_int')) &
            pl.col('close_int').is_between(pl.col('low_int'), pl.col('high_int')))
        if r.filter((pl.col('price_valid') > 1) | (pl.col('extremes_valid') > 1) |
                    (pl.col('candle_available') != geometry)).height:
            raise ValueError('Native candle validity differs from geometry')
        for name in (*RELATIVE_FIELDS, 'execution_volume', 'execution_notional'):
            if r.filter(pl.col(name).is_not_null() & ~pl.col(name).is_finite()).height:
                raise ValueError('Native feature has nonfinite values')
        if r.filter((pl.col('execution_volume') < 0) | (pl.col('execution_notional') < 0) |
                    (~pl.col('candle_available') & pl.any_horizontal(
                        [pl.col(n).is_not_null() for n in ('open_return', 'high_return', 'low_return', 'close_return')])) |
                    (pl.col('volatility_baseline_available') &
                     ~(pl.col('prior_return_volatility') > 0).fill_null(False))).height:
            raise ValueError('Native participation or unavailable feature was imputed')
        expected = [(t, res) for t in self.request.tickers for res in self.request.policy.resolutions_ms]
        if c.select('ticker', 'resolution_ms').rows() != expected:
            raise ValueError('Native feature coverage is missing, duplicate or reordered')
        producers = c['producer_source_hash'].unique().to_list()
        if len(producers) != 1:
            raise ValueError('Ambiguous native feature producer identity')
        require_hash(producers[0])
        for child in c.iter_rows(named=True):
            u = self.request.unit(child['ticker'])
            subset = r.filter((pl.col('ticker') == child['ticker']) &
                              (pl.col('resolution_ms') == child['resolution_ms']))
            if (child['attempt_id'] != u.attempt_id or child['bars_source_hash'] != u.source_hash or
                    child['bars_output_hash'] != u.output_hash or child['market_plan_token'] != self.request.market.token or
                    child['producer_source_hash_domain'] != HASH_DOMAIN or
                    child['through_day_boundary_ms'] != self.request.through_day_boundary_ms or
                    child['row_count'] != subset.height or child['candle_count'] != subset['candle_available'].sum() or
                    child['content_hash'] != table_hash(subset.to_arrow().cast(FEATURE_SCHEMA)) or
                    subset.filter(pl.col('attempt_id') != u.attempt_id).height):
                raise ValueError('Native feature child coverage/source seal differs')
        if self.token != projection_token(self.request, self.feature_attempt_id, self.rows, self.coverage):
            raise ValueError('Native feature projection seal differs')


@dataclass(frozen=True, slots=True)
class NativeChannelSourcePlan:
    """Typed content witness; installation and native admission remain separate."""
    request: NativeChannelRequest
    feature_attempt_id: str
    producer_source_hash: str
    feature_hash: str
    coverage_hash: str
    token: str

    def __post_init__(self):
        if type(self.request) is not NativeChannelRequest:
            raise ValueError('Typed native source request required')
        self.request.__post_init__()
        require_uuid(self.feature_attempt_id)
        for value in (self.producer_source_hash, self.feature_hash, self.coverage_hash, self.token):
            require_hash(value)
        expected = sha256((self.request.market.token + self.request.policy.digest +
            self.feature_attempt_id + str(self.request.through_day_boundary_ms) +
            self.feature_hash + self.coverage_hash).encode()).hexdigest()
        if self.token != expected:
            raise ValueError('Native feature source-plan seal differs')


def issue_source_plan(projection):
    if type(projection) is not NativeChannelProjection:
        raise ValueError('Cannot issue native source plan without typed projection')
    projection.__post_init__()
    return NativeChannelSourcePlan(projection.request, projection.feature_attempt_id,
        projection.coverage['producer_source_hash'][0].as_py(),
        table_hash(projection.rows), table_hash(projection.coverage), projection.token)


def ddl():
    """Explicit installer contract only: no writes, grant changes or fallback disk."""
    types = {pa.string(): 'String', pa.uint32(): 'UInt32', pa.uint64(): 'UInt64',
             pa.uint8(): 'UInt8', pa.int64(): 'Int64', pa.float64(): 'Float64', pa.bool_(): 'Bool'}
    statements = []
    for table, schema in ((FEATURE_TABLE, FEATURE_SCHEMA), (COVERAGE_TABLE, COVERAGE_SCHEMA)):
        columns = []
        for field in schema:
            kind = 'UUID' if field.name.endswith('attempt_id') else types[field.type]
            if field.nullable:
                kind = f'Nullable({kind})'
            columns.append(f'{field.name} {kind}')
        suffix = ',bucket_index' if table == FEATURE_TABLE else ''
        statements.append(f"CREATE TABLE IF NOT EXISTS {table} ({','.join(columns)}) "
            "ENGINE=MergeTree PARTITION BY toYYYYMM(toDate(session_date)) "
            f"ORDER BY (build_id,session_date,feature_attempt_id,ticker,resolution_ms{suffix}) "
            f"SETTINGS storage_policy='{STORAGE_POLICY}'")
    return tuple(statements)
