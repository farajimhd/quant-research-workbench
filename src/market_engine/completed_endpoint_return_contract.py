"""Producer-owned completed-minute return product, independent of any strategy.

Typed Arrow rows/coverage are authoritative; digests are integrity seals, not
installed execution approval. The producer alone derives returns from bars.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import re
from pathlib import Path
from uuid import UUID

import pyarrow as pa

from src.trading_runtime.journal_contract import canonical_json

FEATURE_TABLE = 'arte.completed_endpoint_return_v1'
COVERAGE_TABLE = 'arte.completed_endpoint_return_coverage_v1'
STORAGE_POLICY = 'live_market_ssd'
PRODUCER_HASH_DOMAIN = 'completed-endpoint-return-producer-source:utf8-lf@1'
MAX_KEYS = 512
MAX_TICKERS_PER_READ = 8
MAX_SOURCE_ROWS = MAX_TICKERS_PER_READ * 1920
_HASH = re.compile(r'[0-9a-f]{64}')


@dataclass(frozen=True, slots=True)
class CompletedEndpointReturnPolicy:
    """Exact research endpoint semantics; never a source-certification tolerance."""
    contract: str = 'completed-minute-endpoint-return@1'
    resolution_ms: int = 30_000
    minute_ms: int = 60_000
    current_lag: int = 1
    prior_lag: int = 6
    maximum_asof_age_minutes: int = 2
    price_scale: int = 10_000

    def __post_init__(self):
        expected = ('completed-minute-endpoint-return@1', 30_000, 60_000, 1, 6, 2, 10_000)
        values = (self.contract, self.resolution_ms, self.minute_ms, self.current_lag,
                  self.prior_lag, self.maximum_asof_age_minutes, self.price_scale)
        if values != expected or any(type(v) is not int for v in values[1:]):
            raise ValueError('Endpoint policy differs from its declared research hypothesis')

    def payload(self):
        return dict(contract=self.contract, source='arte.bars_v1', resolution_ms=self.resolution_ms,
            anchor='floor(completed_decision_boundary_ms/60000)', endpoint_lags=[1, 6],
            maximum_asof_age_minutes=2, minute_close='last price-valid close; at least one geometry-valid source row',
            same_day_only=True, availability='source minute end <= anchor boundary <= decision boundary',
            formula='P1/P6-1', score='(P1/10000)*(P1/P6-1)',
            score_units='USD/share price-scaled momentum, not historical dollar change',
            missing='certified expected absence is null; missing source certificate is fatal',
            producer_source_hash_domain=PRODUCER_HASH_DOMAIN)

    @property
    def digest(self):
        return sha256(canonical_json(self.payload()).encode()).hexdigest()


POLICY = CompletedEndpointReturnPolicy()
FEATURE_SCHEMA = pa.schema([
    ('build_id', pa.string()), ('session_date', pa.date32()), ('ticker', pa.string()),
    ('feature_attempt_id', pa.string()), ('bars_attempt_id', pa.string()),
    ('policy_digest', pa.string()), ('decision_boundary_ms', pa.uint32()),
    ('anchor_minute', pa.uint16()), ('target1_minute', pa.int32()), ('target6_minute', pa.int32()),
    ('endpoint1_boundary_ms', pa.uint32()), ('endpoint6_boundary_ms', pa.uint32()),
    ('endpoint1_close_int', pa.uint64()), ('endpoint6_close_int', pa.uint64()),
    ('endpoint1_age_ms', pa.uint32()), ('endpoint6_age_ms', pa.uint32()),
    ('feature_available_boundary_ms', pa.uint32()), ('return_available', pa.uint8()),
    ('return5', pa.float64()), ('price_scaled_momentum', pa.float64()),
])
COVERAGE_SCHEMA = pa.schema([
    ('build_id', pa.string()), ('session_date', pa.date32()), ('ticker', pa.string()),
    ('feature_attempt_id', pa.string()), ('bars_attempt_id', pa.string()),
    ('bars_source_hash', pa.string()), ('bars_output_hash', pa.string()),
    ('policy_digest', pa.string()), ('producer_source_hash_domain', pa.string()),
    ('producer_source_hash', pa.string()), ('market_plan_token', pa.string()),
    ('requested_keys_hash', pa.string()), ('requested_count', pa.uint32()),
    ('available_count', pa.uint32()), ('content_hash', pa.string()),
])


def require_hash(value):
    if type(value) is not str or _HASH.fullmatch(value) is None:
        raise ValueError('Return source requires a canonical 64hex identity')


def require_uuid(value):
    if type(value) is not str or str(UUID(value)) != value or UUID(value).int == 0:
        raise ValueError('Return source requires a canonical nonzero UUID')


def table_hash(table: pa.Table) -> str:
    """Exact typed bytes, including Float64 bits and explicit nulls, not JSON rows."""
    sink = pa.BufferOutputStream()
    table = table.combine_chunks().replace_schema_metadata(None)
    with pa.ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sha256(sink.getvalue().to_pybytes()).hexdigest()


def require_table(table, schema):
    if type(table) is not pa.Table or table.schema.remove_metadata() != schema:
        raise ValueError('Return product has foreign or lossy typed columns')


@dataclass(frozen=True, slots=True)
class ReturnSourceRequest:
    market: object
    keys: tuple[tuple[str, int], ...]
    policy: CompletedEndpointReturnPolicy = POLICY

    def __post_init__(self):
        from src.backend.backtest_market_data import CertifiedMarketDayPlan
        m = self.market
        if type(m) is not CertifiedMarketDayPlan or len(m.sessions) != 1:
            raise ValueError('Return product requires one certified market day')
        if (type(self.policy) is not CompletedEndpointReturnPolicy or self.policy != POLICY
                or m.execution_interval.kind != 'fixed' or m.execution_interval.milliseconds != 100
                or 30_000 not in m.required_resolutions_ms):
            raise ValueError('Return source lacks declared 100ms/30s clocks')
        self.policy.__post_init__()
        for value in (m.build_id, m.definition_hash, m.token):
            require_hash(value)
        date.fromisoformat(m.sessions[0])
        if (type(self.keys) is not tuple or not 1 <= len(self.keys) <= MAX_KEYS
                or self.keys != tuple(sorted(set(self.keys)))):
            raise ValueError('Return request requires <=512 unique ordered keys')
        for ticker, boundary in self.keys:
            if (type(ticker) is not str or ticker not in m.tickers
                    or type(boundary) is not int or not 0 < boundary <= 57_600_000
                    or boundary % 100):
                raise ValueError('Return key is outside certified completed decision scope')
        units = {(u.session_date, u.ticker): u for u in m.units if u.stage == 'bars'}
        if len(units) != sum(u.stage == 'bars' for u in m.units):
            raise ValueError('Return bars source is ambiguous')
        for ticker in self.tickers:
            u = units.get((m.sessions[0], ticker))
            if u is None or u.build_id != m.build_id or u.output_rows <= 0:
                raise ValueError('Return request lacks complete certified bars coverage')
            require_uuid(u.attempt_id)
            require_hash(u.source_hash)
            require_hash(u.output_hash)

    @property
    def tickers(self):
        return tuple(sorted({ticker for ticker, _ in self.keys}))

    def unit(self, ticker):
        return next(u for u in self.market.units if u.stage == 'bars'
                    and u.session_date == self.market.sessions[0] and u.ticker == ticker)

    def keys_hash(self, ticker):
        return sha256(canonical_json([b for t, b in self.keys if t == ticker]).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CompletedReturnProjection:
    request: ReturnSourceRequest
    feature_attempt_id: str
    rows: pa.Table
    coverage: pa.Table
    token: str

    def __post_init__(self):
        import polars as pl
        if type(self.request) is not ReturnSourceRequest:
            raise ValueError('Return projection lacks typed source request')
        self.request.__post_init__()
        require_uuid(self.feature_attempt_id)
        require_table(self.rows, FEATURE_SCHEMA)
        require_table(self.coverage, COVERAGE_SCHEMA)
        r = pl.from_arrow(self.rows)
        expected_keys = pl.DataFrame(self.request.keys, schema=['ticker', 'decision_boundary_ms'], orient='row')
        if (r.height != len(self.request.keys) or r.select('ticker', 'decision_boundary_ms').rows()
                != expected_keys.rows()):
            raise ValueError('Return feature rows omit/reorder/duplicate source keys')
        fixed = dict(build_id=self.request.market.build_id,
            session_date=date.fromisoformat(self.request.market.sessions[0]),
            feature_attempt_id=self.feature_attempt_id, policy_digest=self.request.policy.digest)
        for table in (self.rows, self.coverage):
            for name, value in fixed.items():
                if table[name].null_count or table[name].unique().to_pylist() != [value]:
                    raise ValueError('Return product identity differs from required certificate')
        required = ['ticker', 'bars_attempt_id', 'decision_boundary_ms', 'anchor_minute',
                    'target1_minute', 'target6_minute', 'feature_available_boundary_ms', 'return_available']
        if any(self.rows[name].null_count for name in required):
            raise ValueError('Return row has missing required clock/authority')
        if r.filter((pl.col('anchor_minute') != pl.col('decision_boundary_ms') // 60000)
                | (pl.col('target1_minute') != pl.col('anchor_minute').cast(pl.Int32) - 1)
                | (pl.col('target6_minute') != pl.col('anchor_minute').cast(pl.Int32) - 6)
                | (pl.col('feature_available_boundary_ms') != pl.col('decision_boundary_ms'))
                | (pl.col('return_available') > 1)).height:
            raise ValueError('Return source has changed anchor/availability semantics')
        for lag in (1, 6):
            names = [f'endpoint{lag}_{suffix}' for suffix in ('boundary_ms', 'close_int', 'age_ms')]
            partial = pl.sum_horizontal([pl.col(c).is_not_null().cast(pl.UInt8) for c in names])
            if r.filter((partial != 0) & (partial != 3)).height:
                raise ValueError('Endpoint witness is partial')
            if r.filter(pl.col(names[0]).is_not_null() & ((pl.col(names[1]) <= 0)
                    | (pl.col(names[0]) <= 0)
                    | (pl.col(names[0]) % 60000 != 0)
                    | (pl.col(names[0]) > pl.col('decision_boundary_ms'))
                    | (pl.col(names[2]) > 120000)
                    | (pl.col(names[0]).cast(pl.Int64) + pl.col(names[2])
                       != (pl.col(f'target{lag}_minute') + 1).cast(pl.Int64) * 60000))).height:
                raise ValueError('Endpoint witness is future, stale or outside its target')
        expected_available = pl.col('endpoint1_close_int').is_not_null() & pl.col('endpoint6_close_int').is_not_null()
        if r.filter((pl.col('return_available') == 1) != expected_available).height:
            raise ValueError('Return availability differs from endpoint witnesses')
        for name in ('return5', 'price_scaled_momentum'):
            if r.filter(((pl.col('return_available') == 0) & pl.col(name).is_not_null())
                    | ((pl.col('return_available') == 1) & ~pl.col(name).is_finite().fill_null(False))).height:
                raise ValueError('Missing return was imputed or available return is nonfinite')
        # Integrity validation only: readers do not produce indicators or fill
        # missing endpoints. Exact Float64 equality uses the declared evaluation
        # order (division, subtraction, then P1/10000 multiplication).
        expected_return = pl.col('endpoint1_close_int').cast(pl.Float64) / pl.col('endpoint6_close_int').cast(pl.Float64) - 1
        expected_score = pl.col('endpoint1_close_int').cast(pl.Float64) / 10000 * expected_return
        if r.filter((pl.col('return_available') == 1) & (
                (pl.col('return5') != expected_return)
                | (pl.col('price_scaled_momentum') != expected_score))).height:
            raise ValueError('Return product differs from declared endpoint formula/units')
        c = pl.from_arrow(self.coverage)
        if c.height != len(self.request.tickers) or c['ticker'].to_list() != list(self.request.tickers):
            raise ValueError('Return coverage is missing, duplicate or out of order')
        producer_hashes = self.coverage['producer_source_hash'].unique().to_pylist()
        if len(producer_hashes) != 1:
            raise ValueError('Return coverage has ambiguous producer implementation identity')
        require_hash(producer_hashes[0])
        if self.coverage['producer_source_hash_domain'].unique().to_pylist() != [PRODUCER_HASH_DOMAIN]:
            raise ValueError('Return coverage has foreign producer source hash domain')
        for child in c.iter_rows(named=True):
            ticker = child['ticker']
            u = self.request.unit(ticker)
            subset = r.filter(pl.col('ticker') == ticker).to_arrow().cast(FEATURE_SCHEMA)
            if (child['bars_attempt_id'] != u.attempt_id or child['bars_source_hash'] != u.source_hash
                    or child['bars_output_hash'] != u.output_hash
                    or child['market_plan_token'] != self.request.market.token
                    or child['requested_keys_hash'] != self.request.keys_hash(ticker)
                    or child['requested_count'] != subset.num_rows
                    or child['available_count'] != int(r.filter((pl.col('ticker') == ticker)
                        & (pl.col('return_available') == 1)).height)
                    or child['content_hash'] != table_hash(subset)
                    or subset['bars_attempt_id'].unique().to_pylist() != [u.attempt_id]):
                raise ValueError('Return coverage/source/child seal differs')
        if self.token != projection_token(self.request, self.feature_attempt_id, self.rows, self.coverage):
            raise ValueError('Return projection content seal differs')


def projection_token(request, attempt, rows, coverage):
    return sha256((request.market.token + request.policy.digest + attempt
                   + table_hash(rows) + table_hash(coverage)).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class CompletedReturnSourcePlan:
    """Pinned producer-issued projection seal, required by native preflight.

    Issuance proves typed projection integrity, not installed execution approval.
    The existing producer campaign owner must persist normalized coverage-last
    and certify installation before a saved-run plan may admit this dependency.
    """
    request: ReturnSourceRequest
    feature_attempt_id: str
    producer_source_hash: str
    feature_hash: str
    coverage_hash: str
    token: str

    def __post_init__(self):
        if type(self.request) is not ReturnSourceRequest:
            raise ValueError('Return source plan lacks typed parent request')
        self.request.__post_init__()
        require_uuid(self.feature_attempt_id)
        for value in (self.producer_source_hash, self.feature_hash, self.coverage_hash, self.token):
            require_hash(value)
        expected = sha256((self.request.market.token + self.request.policy.digest
            + self.feature_attempt_id + self.feature_hash + self.coverage_hash).encode()).hexdigest()
        if self.token != expected:
            raise ValueError('Return source plan seal differs')


def issue_source_plan(projection):
    """Producer-owned exact typed output witness; no JSON authoritative substitute."""
    if type(projection) is not CompletedReturnProjection:
        raise ValueError('Cannot issue a source seal without a validated projection')
    projection.__post_init__()
    return CompletedReturnSourcePlan(projection.request, projection.feature_attempt_id,
        projection.coverage['producer_source_hash'][0].as_py(),
        table_hash(projection.rows), table_hash(projection.coverage), projection.token)


def canonical_source_hash(sources):
    """Versioned UTF8-LF source identity, stable across BOM and LF/CRLF transport.

    Domain: completed-endpoint-return-producer-source:utf8-lf@1. Each ordered
    repo-relative path is followed by NUL, UTF8 text with BOM removed and CRLF/CR
    normalized to LF, then NUL. Raw physical-byte hashes belong in delivery
    receipts, not portable certified producer identity.
    """
    digest = sha256((PRODUCER_HASH_DOMAIN + '\0').encode())
    for relative, raw in sources:
        text = raw.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
        digest.update(relative.encode('utf-8') + b'\0' + text.encode('utf-8') + b'\0')
    return digest.hexdigest()


def producer_implementation_hash():
    """Canonical UTF8-LF producer and typed rule source identity, pinned at issuance.

    This identity must be admitted by the installation/certification owner; it
    is not permission to install a product or bypass the saved-run dependency.
    """
    root = Path(__file__).resolve().parents[2]
    paths = ('src/market_engine/completed_endpoint_return_contract.py',
             'pipelines/market_sip/events/completed_endpoint_return_producer.py')
    return canonical_source_hash([(relative, (root / relative).read_bytes()) for relative in paths])


def ddl():
    """Explicit producer installation only; neither reader nor builder executes DDL."""
    types = {pa.string(): 'String', pa.date32(): 'Date', pa.uint32(): 'UInt32',
             pa.uint16(): 'UInt16', pa.int32(): 'Int32', pa.uint64(): 'UInt64',
             pa.uint8(): 'UInt8', pa.float64(): 'Float64'}
    statements = []
    nullable = {f'endpoint{lag}_{suffix}' for lag in (1, 6)
                for suffix in ('boundary_ms', 'close_int', 'age_ms')} | {'return5', 'price_scaled_momentum'}
    for table, schema in ((FEATURE_TABLE, FEATURE_SCHEMA), (COVERAGE_TABLE, COVERAGE_SCHEMA)):
        columns = []
        for field in schema:
            kind = 'UUID' if field.name.endswith('attempt_id') else types[field.type]
            if field.name in nullable:
                kind = f'Nullable({kind})'
            columns.append(f'{field.name} {kind}')
        suffix = ',decision_boundary_ms' if table == FEATURE_TABLE else ''
        statements.append(f"CREATE TABLE IF NOT EXISTS {table} ({','.join(columns)}) "
            "ENGINE=MergeTree PARTITION BY toYYYYMM(session_date) "
            f"ORDER BY (build_id,session_date,feature_attempt_id,ticker{suffix}) "
            f"SETTINGS storage_policy='{STORAGE_POLICY}'")
    return tuple(statements)
