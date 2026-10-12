"""SQL-native strict aggregate stage and normalized output projections.

The mature market-day calculation kernel is reused, without touching its
operational tables. Source input is scanned only by the first aggregate query.
Staging stays compressed in ClickHouse and is excluded from publication.
"""
from __future__ import annotations

from datetime import date
from dataclasses import dataclass
from uuid import UUID

from pipelines.market_sip.events import market_day_sql as core
from . import schema
from .contracts import identifier,literal

STAGING = dict(bars='research_stage_bars_v1',technical='research_stage_indicators_v1',
               broker_100ms='research_stage_liquidity_v1')


@dataclass(frozen=True)
class MarketUnit:
    dataset_id: str
    day: date
    ticker: str
    listing_key: int
    attempt_id: str

    def __post_init__(self):
        UUID(self.dataset_id);UUID(self.attempt_id)
        if type(self.day) is not date or not self.ticker or type(self.listing_key) is not int or not 0<self.listing_key<2**64:
            raise ValueError('Invalid market unit identity')

    def where(self):
        return core.selection(self.dataset_id,self.day,self.ticker,self.attempt_id)


def staging_ddl(database='arte'):
    for ddl in core.ddl(database):
        for name in STAGING:
            ddl=ddl.replace(core.table(database,name),core.table(database,name,STAGING))
        if STAGING['broker_100ms'] in ddl:
            ddl=ddl.replace('reporting_delayed_trades UInt32,',
                'reporting_delayed_trades UInt32, reporting_unknown_trades UInt32, reporting_invalid_trades UInt32,')
        # Staging holds source precision; changing source numeric types is forbidden.
        ddl=ddl.replace("SETTINGS storage_policy='live_market_ssd'",
                        "SETTINGS storage_policy='live_market_ssd', default_compression_codec='ZSTD(1)'")
        yield ddl


def aggregate_sql(unit: MarketUnit, rules, database='arte', *, source=None):
    return core.broker_sql(database,unit.dataset_id,unit.day,unit.ticker,unit.attempt_id,rules,source,
                           verified_timely=True,table_names=STAGING,reporting_audit=True)


def bar_queries(unit: MarketUnit,database='arte'):
    args=(database,unit.dataset_id,unit.day,unit.ticker,unit.attempt_id)
    return (core.base_sql(*args,table_names=STAGING),
            core.rollup_sql(*args,100,(1000,5000,10000,30000),table_names=STAGING),
            core.rollup_sql(*args,30000,(60000,300000,3600000),table_names=STAGING))


def indicator_sql(unit: MarketUnit,database='arte',*,prior=None):
    return core.technical_sql(database,unit.dataset_id,unit.day,unit.ticker,unit.attempt_id,
                             unit.attempt_id,prior,table_names=STAGING)


def normalized_insert(unit: MarketUnit, product: str,database='arte'):
    target={'bars':'research_bars_v1','technical':'research_indicators_v1','broker_100ms':'research_liquidity_v1'}[product]
    table=next(t for t in schema.TABLES if t.name==target)
    names=[c.name for c in table.columns]
    return (f"INSERT INTO {identifier(database)}.{target} ({','.join(names)}) "
            +normalized_projection(unit,product,database))


def normalized_projection(unit: MarketUnit,product: str,database='arte'):
    from .publication import PRODUCTS
    table=next(t for t in schema.TABLES if t.name==PRODUCTS[product])
    values=dict(dataset_id='toUUID(build_id)',listing_key=f'toUInt64({unit.listing_key})')
    select=','.join(values.get(c.name,c.name)+' AS '+c.name for c in table.columns)
    return f"SELECT {select} FROM {core.table(database,product,STAGING)} WHERE {unit.where()}"


def expected_evidence_sql(unit: MarketUnit,product: str,database='arte'):
    from .publication import PRODUCTS,evidence_sql
    table=next(t for t in schema.TABLES if t.name==PRODUCTS[product])
    return evidence_sql(table,normalized_projection(unit,product,database),'tuple(resolution_ms,bucket_index)')


def staged_evidence(unit: MarketUnit,product: str,database='arte'):
    return ("SELECT count() rows,uniqExact(tuple(resolution_ms,bucket_index)) unique_keys,"
            "sum(cityHash64(tuple(*))) hash "
            f"FROM {core.table(database,product,STAGING)} WHERE {unit.where()}")


def cleanup_sql(unit: MarketUnit,database='arte'):
    # Called only after normalized readback/receipt certification by the controller.
    for name in STAGING:
        yield (f"ALTER TABLE {core.table(database,name,STAGING)} DELETE WHERE {unit.where()} "
               "SETTINGS mutations_sync=2")
