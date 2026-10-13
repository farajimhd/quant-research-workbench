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
    source_ticker: str|None=None

    def __post_init__(self):
        UUID(self.dataset_id);UUID(self.attempt_id)
        if type(self.day) is not date or not self.ticker or type(self.listing_key) is not int or not 0<self.listing_key<2**64:
            raise ValueError('Invalid market unit identity')
        if self.source_ticker is not None and (not isinstance(self.source_ticker,str) or not self.source_ticker
                or self.source_ticker.upper()!=self.ticker.upper()):
            raise ValueError('Source spelling must preserve normalized symbol identity')

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
    if source is None and unit.source_ticker is not None and unit.source_ticker!=unit.ticker:
        source=core.canonical_source(unit.day,unit.source_ticker)
    return core.broker_sql(database,unit.dataset_id,unit.day,unit.ticker,unit.attempt_id,rules,source,
                           verified_timely=True,table_names=STAGING,reporting_audit=True)


def selected_frames(frames=core.FRAMES):
    frames=tuple(frames)
    if not frames or tuple(sorted(set(frames)))!=frames or any(type(f) is not int or f not in core.FRAMES for f in frames):
        raise ValueError('Invalid selected market resolutions')
    return frames


def bar_queries(unit: MarketUnit,database='arte',*,frames=core.FRAMES):
    frames=selected_frames(frames)
    args=(database,unit.dataset_id,unit.day,unit.ticker,unit.attempt_id)
    higher=tuple(f for f in frames if f>=60000)
    lower=tuple(sorted({f for f in frames if 100<f<=30000}|({30000} if higher else set())))
    result=[core.base_sql(*args,table_names=STAGING)]
    if lower:result.append(core.rollup_sql(*args,100,lower,table_names=STAGING))
    if higher:result.append(core.rollup_sql(*args,30000,higher,table_names=STAGING))
    return tuple(result)


def indicator_sql(unit: MarketUnit,database='arte',*,prior=None,frames=core.FRAMES):
    from .indicators import technical_sql
    source=(f'SELECT session_date,resolution_ms,bucket_index,close_int,high_int,low_int '
        f"FROM {core.table(database,'bars',STAGING)} WHERE {unit.where()} AND price_valid=1 AND extremes_valid=1")
    return technical_sql(database,unit,source,prior or {},frames=selected_frames(frames))


def normalized_insert(unit: MarketUnit, product: str,database='arte',*,frames=core.FRAMES):
    target={'bars':'research_bars_v1','technical':'research_indicators_v1','broker_100ms':'research_liquidity_v1'}[product]
    table=next(t for t in schema.TABLES if t.name==target)
    names=[c.name for c in table.columns]
    return (f"INSERT INTO {identifier(database)}.{target} ({','.join(names)}) "
            +normalized_projection(unit,product,database,frames=frames))


def normalized_projection(unit: MarketUnit,product: str,database='arte',*,frames=core.FRAMES):
    from .publication import PRODUCTS
    table=next(t for t in schema.TABLES if t.name==PRODUCTS[product])
    values=dict(dataset_id='toUUID(build_id)',listing_key=f'toUInt64({unit.listing_key})')
    select=','.join(values.get(c.name,c.name)+' AS '+c.name for c in table.columns)
    selection=' AND resolution_ms IN ('+','.join(map(str,selected_frames(frames)))+')' if product in ('bars','technical') else ''
    return f"SELECT {select} FROM {core.table(database,product,STAGING)} WHERE {unit.where()}"+selection


def expected_evidence_sql(unit: MarketUnit,product: str,database='arte',*,frames=core.FRAMES):
    from .publication import PRODUCTS,evidence_sql
    table=next(t for t in schema.TABLES if t.name==PRODUCTS[product])
    return evidence_sql(table,normalized_projection(unit,product,database,frames=frames),'tuple(resolution_ms,bucket_index)')


def staged_evidence(unit: MarketUnit,product: str,database='arte'):
    return ("SELECT count() rows,uniqExact(tuple(resolution_ms,bucket_index)) unique_keys,"
            "sum(cityHash64(tuple(*))) hash "
            f"FROM {core.table(database,product,STAGING)} WHERE {unit.where()}")


def cleanup_sql(unit: MarketUnit,database='arte'):
    # Called only after normalized readback/receipt certification by the controller.
    for name in STAGING:
        yield (f"ALTER TABLE {core.table(database,name,STAGING)} DELETE WHERE {unit.where()} "
               "SETTINGS mutations_sync=2")
