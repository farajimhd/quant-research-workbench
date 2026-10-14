"""Normalized shared provenance for extended QMD feature histories.

The plan hash is part of the history product revision. Plans and their explicit
scalar dependencies are sealed in ClickHouse before any feature coverage seal.
"""
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from .opening_price_reference import content_hash, require_hash
from src.trading_runtime.arte_journal_schema import TableContract

UNIT = TableContract('qmd_feature_source_unit_v1', (
    ('plan_hash', 'FixedString(64)'), ('ordinal', 'UInt32'),
    ('source_kind', 'LowCardinality(String)'), ('source_database', 'LowCardinality(String)'),
    ('source_table', 'LowCardinality(String)'), ('session_date', 'Date'),
    ('ticker', 'String'), ('source_identity', 'String'), ('row_count', 'UInt64'),
    ('content_hash', 'FixedString(64)')),
    'tuple()', 'plan_hash,ordinal', database='q_live')
SEAL = TableContract('qmd_feature_source_plan_v1', (
    ('plan_hash', 'FixedString(64)'), ('market_plan_hash', 'FixedString(64)'),
    ('algorithm_hash', 'FixedString(64)'), ('session_date', 'Date'),
    ('first_bucket', 'UInt32'), ('stop_bucket', 'UInt32'),
    ('universe_count', 'UInt32'), ('dependency_count', 'UInt32'),
    ('certified_at', "DateTime64(6, 'UTC')")),
    'tuple()', 'plan_hash', database='q_live')
TABLES = (UNIT, SEAL)


def required_grants():
    """Reviewable least-privilege delta; never executes SQL or creates users."""
    reads=('market_security_float_v1','feature_tradable_universe_snapshot_v2',
           'feature_tradable_universe_snapshot_coverage_v2',UNIT.name,SEAL.name)
    return tuple([f'GRANT SELECT ON q_live.{name} TO program_market_input_reader' for name in reads]
        +[f'GRANT {privilege} ON q_live.{table.name} TO program_history_publisher'
          for table in TABLES for privilege in ('SELECT','INSERT')])


@dataclass(frozen=True)
class FeatureDependency:
    source_kind: str
    source_database: str
    source_table: str
    session_date: date
    ticker: str
    source_identity: str
    row_count: int
    content_hash: str

    def __post_init__(self):
        require_hash(self.content_hash)
        if (self.source_database not in ('arte', 'q_live')
                or type(self.session_date) is not date
                or type(self.row_count) is not int or self.row_count < 0
                or any(type(v) is not str or not 1 <= len(v) <= 256 for v in
                       (self.source_kind, self.source_table, self.source_identity))
                or type(self.ticker) is not str or len(self.ticker) > 32):
            raise ValueError('Feature dependency requires explicit normalized source identity')

    def payload(self):
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


@dataclass(frozen=True)
class FeatureSourcePlan:
    market_plan_hash: str
    algorithm_hash: str
    session_date: date
    first_bucket: int
    stop_bucket: int
    universe_count: int
    dependencies: tuple[FeatureDependency, ...]
    _hash: str = field(init=False,repr=False)

    def __post_init__(self):
        require_hash(self.market_plan_hash)
        require_hash(self.algorithm_hash)
        if (type(self.session_date) is not date or type(self.universe_count) is not int
                or not 1<=self.universe_count<=25000
                or type(self.first_bucket) is not int or type(self.stop_bucket) is not int
                or not 14400 <= self.first_bucket < self.stop_bucket <= 72000
                or type(self.dependencies) is not tuple or not 1<=len(self.dependencies)<=200000
                or any(type(v) is not FeatureDependency for v in self.dependencies)):
            raise ValueError('Feature plan requires its full preopen source census')
        keys = [(v.source_kind, v.session_date, v.ticker, v.source_identity) for v in self.dependencies]
        if keys != sorted(set(keys)):
            raise ValueError('Feature dependencies must be unique and canonically ordered')
        if any(v.session_date>self.session_date for v in self.dependencies):
            raise ValueError('Feature source plan cannot depend on a future session')
        object.__setattr__(self,'_hash',content_hash(dict(market_plan_hash=self.market_plan_hash,
            algorithm_hash=self.algorithm_hash, session_date=self.session_date,
            first_bucket=self.first_bucket, stop_bucket=self.stop_bucket,
            universe_count=self.universe_count,
            dependencies=[v.payload() for v in self.dependencies])))

    @property
    def plan_hash(self):
        return self._hash

    def unit_records(self):
        return tuple(dict(plan_hash=self.plan_hash, ordinal=i, **v.payload())
                     for i, v in enumerate(self.dependencies))

    def seal(self, at=None):
        at=at or datetime.now(timezone.utc)
        if not isinstance(at,datetime) or at.tzinfo is None or at.utcoffset() is None:
            raise ValueError('Feature source seal requires an aware certification clock')
        return dict(plan_hash=self.plan_hash, market_plan_hash=self.market_plan_hash,
            algorithm_hash=self.algorithm_hash, session_date=self.session_date,
            first_bucket=self.first_bucket, stop_bucket=self.stop_bucket,
            universe_count=self.universe_count, dependency_count=len(self.dependencies),
            certified_at=at)


def wire_records(rows):
    return [{k:(v.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f') if isinstance(v,datetime)
        else v.isoformat() if type(v) is date else v) for k,v in row.items()} for row in rows]
