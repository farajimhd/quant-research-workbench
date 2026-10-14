"""Shared scalar QMD prior-RTH history, scoped to a later consumer cutoff.

The selected population belongs to the consumer day. This product makes no
claim about membership on the historical source day and never supplies fills.
"""
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timezone
from types import MappingProxyType
from uuid import UUID

from .opening_price_reference import content_hash, require_hash, previous_regular_session, NEW_YORK
from src.trading_runtime.arte_journal_schema import TableContract

REVISION = 'qmd-consumer-scoped-prior-rth-v1'
PREFIX = (('consumer_build_id', 'String'), ('consumer_session_date', 'Date'), ('attempt_id', 'UUID'))
VALUES = TableContract('qmd_regular_close_history_v1', PREFIX + (
    ('ticker', 'LowCardinality(String)'), ('prior_session_date', 'Date'),
    ('canonical_event_count', 'UInt64'), ('next_ordinal', 'UInt64'), ('last_ordinal', 'UInt64'),
    ('first_sip_timestamp_us', 'UInt64'), ('last_sip_timestamp_us', 'UInt64'),
    ('source_build_step', 'UInt64'), ('source_updated_at', "Nullable(DateTime64(6, 'UTC'))"),
    ('closing_bar_count', 'UInt64'), ('prior_close_int', 'Nullable(UInt64)'),
    ('close_sip_timestamp_us', 'Nullable(UInt64)'), ('close_ordinal', 'Nullable(UInt64)'),
    ('content_hash', 'FixedString(64)')),
    'toYYYYMM(consumer_session_date)', 'consumer_build_id,consumer_session_date,attempt_id,ticker')
RULES = TableContract('qmd_regular_close_rule_v1', PREFIX + (
    ('token_id', 'UInt64'), ('modifier_int', 'UInt64'), ('update_high_low', 'UInt8'),
    ('update_last', 'UInt8'), ('update_volume', 'UInt8')),
    'toYYYYMM(consumer_session_date)', 'consumer_build_id,consumer_session_date,attempt_id,token_id')
COVERAGE = TableContract('qmd_regular_close_coverage_v1', PREFIX + (
    ('revision', 'String'), ('consumer_market_plan_hash', 'FixedString(64)'),
    ('consumer_identity_plan_hash', 'FixedString(64)'), ('consumer_population_reference_hash', 'FixedString(64)'),
    ('consumer_cutoff', "DateTime64(6, 'UTC')"), ('prior_session_date', 'Date'),
    ('canonical_storage_policy', 'String'), ('canonical_disk', 'String'),
    ('stats_version', 'UInt32'), ('source_filter_key', 'String'),
    ('total_event_rows_after_filters', 'UInt64'), ('source_updated_at', "DateTime64(6, 'UTC')"),
    ('source_inventory_hash', 'FixedString(64)'), ('calculation_source_hash', 'FixedString(64)'),
    ('producer_code_hash', 'FixedString(64)'), ('ticker_count', 'UInt32'),
    ('resolved_count', 'UInt32'), ('covered_empty_count', 'UInt32'), ('rule_count', 'UInt32'),
    ('values_hash', 'FixedString(64)'), ('rules_hash', 'FixedString(64)'),
    ('content_hash', 'FixedString(64)'), ('certified_at', "DateTime64(6, 'UTC')")),
    'toYYYYMM(consumer_session_date)', 'consumer_build_id,consumer_session_date')
VALUES, RULES, COVERAGE = tuple(replace(t, database='q_live') for t in (VALUES, RULES, COVERAGE))
TABLES = VALUES, RULES, COVERAGE
_PROOF = object()


@dataclass(frozen=True)
class PriorCloseScope:
    consumer_build_id: str
    consumer_session_date: date
    consumer_market_plan_hash: str
    tickers: tuple
    producer_code_hash: str

    def __post_init__(self):
        import re
        if (type(self.consumer_build_id) is not str or not 1 <= len(self.consumer_build_id) <= 128
                or type(self.consumer_session_date) is not date or type(self.tickers) is not tuple
                or not 1 <= len(self.tickers) <= 25000 or tuple(sorted(set(self.tickers))) != self.tickers
                or any(re.fullmatch(r'[A-Z0-9][A-Z0-9._-]{0,31}', t or '') is None for t in self.tickers)):
            raise ValueError('Prior-close history requires a complete bounded consumer scope')
        require_hash(self.consumer_market_plan_hash)
        require_hash(self.producer_code_hash)

    @property
    def cutoff(self):
        return datetime.combine(self.consumer_session_date, time(4), NEW_YORK).astimezone(timezone.utc)

    @property
    def regular(self):
        return previous_regular_session(self.consumer_session_date)


def restore(table, row):
    if table not in TABLES or set(row) != {name for name, _ in table.columns}:
        raise ValueError('Prior-close record differs from its normalized schema')
    result = {}
    for name, kind in table.columns:
        value = row[name]
        if kind.startswith('Nullable('):
            if value is None:
                result[name] = None
                continue
            kind = kind[9:-1]
        if kind.startswith('UInt'):
            value = unsigned(value, bits=int(kind[4:]))
        elif kind == 'Date':
            value = date.fromisoformat(value) if type(value) is str else value
            if type(value) is not date:
                raise ValueError('Prior-close date differs')
        elif kind.startswith('DateTime64'):
            value = datetime.fromisoformat(value.replace('Z', '+00:00')) if type(value) is str else value
            if not isinstance(value, datetime):
                raise ValueError('Prior-close clock differs')
            value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        elif kind == 'UUID':
            if type(value) is not str or str(UUID(value)) != value:
                raise ValueError('Prior-close attempt differs')
        else:
            if type(value) is not str or not value or len(value) > 512:
                raise ValueError('Prior-close named scalar differs')
            if kind == 'FixedString(64)':
                require_hash(value)
        result[name] = value
    return result


def unsigned(value, *, bits=64):
    if type(value) is str and value.isascii() and value.isdecimal():
        value = int(value)
    if type(value) is not int or not 0 <= value < 2**bits:
        raise ValueError('Prior-close unsigned scalar differs')
    return value


@dataclass(frozen=True)
class PriorCloseUnit:
    ticker: str
    attempt_id: str
    source_hash: str
    output_hash: str


@dataclass(frozen=True)
class CertifiedPriorClosePlan:
    scope: PriorCloseScope
    header: object
    records: tuple
    _proof: object

    def __post_init__(self):
        if self._proof is not _PROOF:
            raise ValueError('Prior-close plan requires native cold readback proof')
        object.__setattr__(self, 'header', MappingProxyType(dict(self.header)))
        object.__setattr__(self, 'records', tuple(MappingProxyType(dict(row)) for row in self.records))

    @property
    def token(self):
        return self.header['content_hash']

    @property
    def build_id(self):
        return 'qmd-prior-rth-' + self.token

    @property
    def sessions(self):
        return (self.scope.regular.day.isoformat(),)

    @property
    def tickers(self):
        return self.scope.tickers

    @property
    def units(self):
        return tuple(PriorCloseUnit(row['ticker'], row['attempt_id'], self.token, row['content_hash'])
            for row in self.records)

    def verify(self, reader):
        from pipelines.market_sip.events.regular_close_history_producer import certify_prior_close
        if certify_prior_close(reader, self.scope, expected_hash=self.token) != self:
            raise ValueError('Prior-close history changed after certification')

    def verify_consumer(self, market):
        if ((market.build_id, market.sessions, market.token, market.tickers) !=
                (self.scope.consumer_build_id, (self.scope.consumer_session_date.isoformat(),),
                 self.scope.consumer_market_plan_hash, self.scope.tickers)):
            raise ValueError('Prior-close history differs from its certified consumer population')
