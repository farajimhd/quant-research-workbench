"""Exact normalized v1 persistence DDL and fail-closed physical placement audit.

All large payloads live in ClickHouse. Header/provenance dictionaries avoid
repeating JSON, issuer strings or source proofs on every market observation.
"""
from __future__ import annotations

from dataclasses import dataclass
from .contracts import STORAGE_POLICY, digest, identifier, literal


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    codec: str = "ZSTD(1)"

    def __post_init__(self):
        if self.codec.startswith('Delta,'):
            width=4 if self.type=='UInt32' else 8 if self.type in ('UInt64',"DateTime64(3,'UTC')","DateTime64(6,'UTC')") else None
            if width is None:raise ValueError('Delta codec needs an explicit supported numeric width')
            object.__setattr__(self,'codec',f'Delta({width}),'+self.codec.removeprefix('Delta,'))

    def sql(self):
        return f"{identifier(self.name)} {self.type} CODEC({self.codec})"


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    order: tuple[str, ...]
    partition: str = "tuple()"

    def __post_init__(self):
        identifier(self.name)
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)) or not set(self.order) <= set(names):
            raise ValueError("Duplicate columns or invalid sorting key")

    def ddl(self, database):
        from .retired_market import TABLES,require_available
        if self.name in TABLES:require_available(database)
        cols = ",\n    ".join(c.sql() for c in self.columns)
        nullable_order=any(c.name in self.order and c.type.startswith('Nullable(') for c in self.columns)
        return (f"CREATE TABLE IF NOT EXISTS {identifier(database)}.{self.name} (\n    {cols}\n) "
                f"ENGINE=MergeTree PARTITION BY {self.partition} ORDER BY ({','.join(self.order)}) "
                f"SETTINGS storage_policy='{STORAGE_POLICY}', default_compression_codec='ZSTD(1)'"
                +(', allow_nullable_key=1' if nullable_order else ''))


def c(name, type, codec="ZSTD(1)"):
    return Column(name, type, codec)


UNIT = (c("dataset_id", "UUID"), c("session_date", "Date"), c("listing_key", "UInt64", "T64,ZSTD(1)"),
        c("attempt_id", "UUID"))
BUCKET = UNIT + (c("resolution_ms", "UInt32", "T64,ZSTD(1)"), c("bucket_index", "UInt32", "Delta,ZSTD(1)"))
PRICES = tuple(c(n, "UInt64", "T64,ZSTD(1)") for n in ("open_int","high_int","low_int","close_int"))
ACTIVITY = (c("volume", "Float64"), c("trade_count", "UInt64", "T64,ZSTD(1)"), c("notional", "Float64"),
            c("execution_volume", "Float64"), c("execution_notional", "Float64"))
ORDER = ("dataset_id", "listing_key", "session_date", "attempt_id", "resolution_ms", "bucket_index")
MONTH = "toYYYYMM(session_date)"

TABLES = (
    Table("research_dataset_v1", (c("dataset_id","UUID"),c("contract_hash","FixedString(64)"),
          c("schema_hash","FixedString(64)"),c("start_date","Date"),c("end_date","Date"),
          c("definition","String"),c("created_at","DateTime64(6,'UTC')")), ("dataset_id",)),
    Table('research_campaign_unit_v1',(c('dataset_id','UUID'),c('unit_id','UInt64','T64,ZSTD(1)'),
        c('unit_key','String'),c('product','LowCardinality(String)'),c('parents','Array(UInt64)'),
        c('arguments','String'),c('arguments_hash','FixedString(64)'),c('memory_bytes','UInt64','T64,ZSTD(1)')),
        ('dataset_id','unit_id')),
    Table('research_campaign_state_v1',(c('dataset_id','UUID'),c('unit_id','UInt64','T64,ZSTD(1)'),
        c('sequence','UInt64','Delta,ZSTD(1)'),c('status','UInt8'),c('output_hash','FixedString(64)'),
        c('reason','LowCardinality(String)'),c('changed_at',"DateTime64(6,'UTC')")),('dataset_id','unit_id','sequence')),
    Table('research_campaign_seal_v1',(c('dataset_id','UUID'),c('plan_hash','FixedString(64)'),
        c('source_hash','FixedString(64)'),c('root_hash','FixedString(64)'),c('units','UInt64','T64,ZSTD(1)'),
        c('published_at',"DateTime64(6,'UTC')")),('dataset_id',)),
    Table("research_identity_v1", (c("entity_kind","UInt8"),c("entity_key","UInt64","T64,ZSTD(1)"),
          c("source_id","String")), ("entity_kind","entity_key")),
    Table("research_provenance_v1", (c("evidence_hash","FixedString(64)"),c("source_system","LowCardinality(String)"),
          c("source_record","String"),c("source_hash","FixedString(64)"),c("proof","String")), ("evidence_hash",)),
    Table("research_membership_v1", (c("dataset_id","UUID"),c("listing_key","UInt64","T64,ZSTD(1)"),
          c("security_key","UInt64","T64,ZSTD(1)"),c("issuer_key","UInt64","T64,ZSTD(1)"),
          c("ticker","LowCardinality(String)"),c("conid","UInt64","T64,ZSTD(1)"),
          c("valid_from","DateTime64(6,'UTC')","Delta,ZSTD(1)"),c("valid_to","DateTime64(6,'UTC')","Delta,ZSTD(1)"),
          c("eligible","UInt8"),c("reason","LowCardinality(String)"),c("evidence_hash","FixedString(64)")),
          ("dataset_id","listing_key","valid_from"), "toYear(valid_from)"),
    Table("research_bars_v1", BUCKET + PRICES + ACTIVITY + (c("price_valid","UInt8"),c("extremes_valid","UInt8")), ORDER, MONTH),
    Table("research_indicators_v1", BUCKET + tuple(c("ema_"+str(p),"Float64") for p in (7,9,12,15,20,26,50)) +
          tuple(c(n,"Float64") for n in ("macd_line","macd_signal","macd_histogram","rsi_14","atr_14","previous_close","avg_gain","avg_loss")) +
          (c("rsi_ready","UInt8"),c("atr_ready","UInt8"),c("sample_count","UInt64","T64,ZSTD(1)")), ORDER, MONTH),
    Table("research_indicator_state_v1", UNIT + (c("resolution_ms","UInt32","T64,ZSTD(1)"),
          c("recipe_hash","FixedString(64)"),c("predecessor_hash","FixedString(64)")) +
          (c("source_hash","FixedString(64)"),c("rules_hash","FixedString(64)")) +
          tuple(c("ema_"+str(p),"Float64") for p in (7,9,12,15,20,26,50)) +
          tuple(c(n,"Float64") for n in ("macd_signal","close","atr_14","avg_gain","avg_loss")) +
          (c("sample_count","UInt64","T64,ZSTD(1)"),),
          ("dataset_id","listing_key","session_date","attempt_id","resolution_ms"),MONTH),
    Table("research_activity_v1", BUCKET + (c("window_seconds","UInt32","T64,ZSTD(1)"),) +
          tuple(c(n,"Nullable(Float64)") for n in ("volume","notional")) +
          (c("trade_count","Nullable(UInt64)"),) +
          tuple(c(n,"Nullable(Float64)") for n in ("execution_volume","execution_notional")) +
          (c("context_ready","UInt8"),),
          ("dataset_id","session_date","listing_key","attempt_id","resolution_ms","window_seconds","bucket_index"),MONTH),
    Table("research_liquidity_v1", BUCKET + PRICES + ACTIVITY +
          tuple(c(n,"UInt64","Delta,ZSTD(1)") for n in ("first_event_us","last_event_us","quote_timestamp_us")) +
          tuple(c(n,"UInt64","T64,ZSTD(1)") for n in ("event_count","source_trade_count","quote_event_count","reporting_delayed_trades","reporting_unknown_trades","reporting_invalid_trades","volume_ineligible_trades","price_ineligible_trades","execution_ineligible_trades","pre_0405_trades","pre_0405_volume_eligible_trades","invalid_trade_values","bid_int","ask_int")) +
          tuple(c(n,"Float64") for n in ("bid_size","ask_size","cumulative_volume","cumulative_notional","cumulative_execution_volume","cumulative_execution_notional")) +
          tuple(c(n,"UInt8") for n in ("price_valid","extremes_valid","volume_valid","quote_valid")), ORDER, MONTH),
    Table("research_facts_v1", (c("dataset_id","UUID"),c("attempt_id","UUID"),c("entity_kind","UInt8"),c("entity_key","UInt64","T64,ZSTD(1)"),
          c("metric_key","UInt32","T64,ZSTD(1)"),c("period_start","Nullable(Date)"),c("period_end","Nullable(Date)"),
          c("context_kind","UInt8"),c("fiscal_year","UInt16"),c("fiscal_period","LowCardinality(String)"),
          c("taxonomy_key","UInt8"),
          c("available_at","DateTime64(6,'UTC')","Delta,ZSTD(1)"),c("revision","UInt32","T64,ZSTD(1)"),
          c("value","Nullable(Float64)"),c("unit_key","UInt16"),c("basis_unit_key","UInt16"),c("status","UInt8"),c("precision","UInt8"),
          c("confidence","UInt8"),c("reason","LowCardinality(String)"),c("evidence_hash","FixedString(64)")),
          ("dataset_id","entity_kind","entity_key","attempt_id","metric_key","unit_key","basis_unit_key","taxonomy_key","available_at","period_end","revision"),"toYYYYMM(available_at)"),
    Table("research_short_pressure_v1", (c("dataset_id","UUID"),c("attempt_id","UUID"),
          c("entity_kind","UInt8"),c("entity_key","UInt64","T64,ZSTD(1)"),
          c("available_at","DateTime64(6,'UTC')","Delta,ZSTD(1)"),
          c("window_sessions","UInt16","T64,ZSTD(1)"),c("window_end","Nullable(Date)"),
          c("observed_sessions","UInt16","T64,ZSTD(1)"),c("ratio","Nullable(Float64)"),
          c("context_ready","UInt8"),c("reason","LowCardinality(String)")),
          ("dataset_id","entity_key","attempt_id","available_at","window_sessions"),"toYYYYMM(available_at)"),
    Table("research_metric_v1", (c("catalog_hash","FixedString(64)"),c("metric_key","UInt32"),c("name","LowCardinality(String)"),
          c("unit_key","UInt16"),c("formula","String")), ("catalog_hash","metric_key")),
    Table("research_unit_v1", (c("unit_key","UInt16"),c("symbol","LowCardinality(String)"),
          c("kind","UInt8")), ("unit_key",)),
    Table("research_taxonomy_v1", (c("taxonomy_key","UInt8"),c("name","LowCardinality(String)")), ("taxonomy_key",)),
    Table("research_v7_checkpoint_v1", UNIT + (c("boundary","UInt8"),c("boundary_us","UInt64","DoubleDelta,ZSTD(1)"),
          c("last_input_us","UInt64","Delta,ZSTD(1)"),c("parent_hash","FixedString(64)"),c("state_hash","FixedString(64)"),
          c("codec_version","UInt8"),c("payload","String")),
          ("dataset_id","listing_key","session_date","boundary","attempt_id"),MONTH),
    Table("research_unit_receipt_v1", UNIT + (c("product","LowCardinality(String)"),c("status","UInt8"),
          c("output_rows","UInt64","T64,ZSTD(1)"),c("output_hash","FixedString(64)"),c("parent_hash","FixedString(64)"),
          c("reason","LowCardinality(String)"),c("published_at","DateTime64(6,'UTC')")),
          ("dataset_id","listing_key","session_date","product","attempt_id"),MONTH),
    Table("research_reference_receipt_v1", (c("dataset_id","UUID"),c("entity_kind","UInt8"),
          c("entity_key","UInt64","T64,ZSTD(1)"),c("family","LowCardinality(String)"),
          c("range_start","Date"),c("range_end","Date"),c("attempt_id","UUID"),c("status","UInt8"),
          c("output_rows","UInt64","T64,ZSTD(1)"),c("output_hash","FixedString(64)"),
          c("parent_hash","FixedString(64)"),c("catalog_hash","FixedString(64)"),
          c("published_at","DateTime64(6,'UTC')")),
          ("dataset_id","entity_kind","entity_key","family","range_start","range_end","attempt_id"),"toYYYYMM(range_end)"),
)


def schema_hash():
    return digest([dict(name=t.name, columns=[vars(c) for c in t.columns], order=t.order,
                        partition=t.partition) for t in TABLES])


def ddl(database="arte"):
    return tuple(t.ddl(database) for t in TABLES)


class BatchedStorageQuery:
    """Combine fresh metadata reads; never cache table/part authority."""
    def __init__(self,query):self.query=query
    def __call__(self,*args,**kwargs):return self.query(*args,**kwargs)
    def storage_snapshot(self,database,names,*,auxiliary=False):
        identifier(database)
        for name in names:identifier(name)
        selected=','.join(literal(name) for name in names)
        def branch(kind,columns,origin):
            values=list(columns)+["''"]*(5-len(columns))
            return 'SELECT '+literal(kind)+' kind,'+','.join(v+' v'+str(i) for i,v in enumerate(values))+' FROM '+origin
        tables=f'system.tables WHERE database={literal(database)} AND name IN ({selected})'
        parts=f'system.parts WHERE active AND database={literal(database)} AND table IN ({selected}) GROUP BY table,disk_name'
        branches=[branch('tables',('name','storage_policy') if auxiliary else ('name','storage_policy','engine','sorting_key','partition_key'),tables),
                  branch('parts',('table','disk_name'),parts)]
        keys=dict(tables=('name','storage_policy') if auxiliary else ('name','storage_policy','engine','sorting_key','partition_key'),
                  parts=('table','disk_name'))
        if not auxiliary:
            branches.extend((branch('policy',('policy_name','disk_name'),"system.storage_policies ARRAY JOIN disks AS disk_name WHERE policy_name="+literal(STORAGE_POLICY)),
                branch('columns',('table','name','type','compression_codec'),f'system.columns WHERE database={literal(database)} AND table IN ({selected})')))
            keys.update(policy=('policy_name','disk_name'),columns=('table','name','type','compression_codec'))
        result={kind:[] for kind in keys}
        for row in self.query(' UNION ALL '.join(branches)):
            kind=row['kind']
            if kind not in keys:raise ValueError('Unexpected storage metadata branch')
            result[kind].append({key:row['v'+str(i)] for i,key in enumerate(keys[kind])})
        return result


def verify_storage(query, database="arte", *, require_tables=False):
    """Read both policy routes and actual parts; never silently fix placement."""
    identifier(database)
    snapshot=query.storage_snapshot(database,tuple(t.name for t in TABLES)) if isinstance(query,BatchedStorageQuery) else None
    policy = snapshot['policy'] if snapshot is not None else query("SELECT policy_name,disk_name FROM system.storage_policies "
                   f"ARRAY JOIN disks AS disk_name WHERE policy_name={literal(STORAGE_POLICY)}")
    if not policy or any(r["disk_name"] != STORAGE_POLICY for r in policy):
        raise ValueError("live_market_ssd policy is absent or routes outside live_market_ssd")
    names = ",".join(literal(t.name) for t in TABLES)
    rows = snapshot['tables'] if snapshot is not None else query(f"SELECT name,storage_policy,engine,sorting_key,partition_key FROM system.tables WHERE database={literal(database)} AND name IN ({names})")
    if require_tables and {r["name"] for r in rows} != {t.name for t in TABLES}:
        raise ValueError("Required research tables are missing")
    if any(r["storage_policy"] != STORAGE_POLICY for r in rows):
        raise ValueError("Research table has incorrect storage policy")
    parts = snapshot['parts'] if snapshot is not None else query(f"SELECT table,disk_name FROM system.parts WHERE active AND database={literal(database)} AND table IN ({names}) GROUP BY table,disk_name")
    if any(r["disk_name"] != STORAGE_POLICY for r in parts):
        raise ValueError("Research parts are outside live_market_ssd")
    if rows:
        definitions={t.name:t for t in TABLES}
        for row in rows:
            table=definitions[row['name']]
            compact=lambda value:value.replace(' ','').replace('`','')
            if (row['engine']!='MergeTree' or compact(row['sorting_key'])!=','.join(table.order)
                    or compact(row['partition_key'])!=compact(table.partition)):
                raise ValueError('Existing research engine/sorting/partition differs; explicit migration required')
        columns = snapshot['columns'] if snapshot is not None else query(f"SELECT table,name,type,compression_codec FROM system.columns "
                        f"WHERE database={literal(database)} AND table IN ({names})")
        actual={(r['table'],r['name']):(r['type'].replace(' ',''),r['compression_codec'].replace(' ','')) for r in columns}
        present={r['name'] for r in rows}
        expected={(t.name,col.name):(col.type.replace(' ',''),'CODEC('+col.codec.replace(' ','')+')')
                  for t in TABLES if t.name in present for col in t.columns}
        if actual != expected:
            differences=[(key,expected.get(key),actual.get(key)) for key in sorted(set(actual)|set(expected))
                         if expected.get(key)!=actual.get(key)]
            raise ValueError(f'Existing research schema/type/codec differs; explicit migration required: {differences[:3]}')
    return dict(tables=len(rows), parts=len(parts), schema_hash=schema_hash())


def verify_auxiliary_storage(query,database,names):
    """Staging is operational data too; existing misplaced parts block writers."""
    identifier(database)
    for name in names:identifier(name)
    if not names:raise ValueError('Auxiliary table inventory is empty')
    selected=','.join(literal(name) for name in names)
    snapshot=query.storage_snapshot(database,names,auxiliary=True) if isinstance(query,BatchedStorageQuery) else None
    rows=snapshot['tables'] if snapshot is not None else query(f'SELECT name,storage_policy FROM system.tables WHERE database={literal(database)} '
               f'AND name IN ({selected})')
    if {r['name'] for r in rows}!=set(names) or any(r['storage_policy']!=STORAGE_POLICY for r in rows):
        raise ValueError('Required staging tables are absent or misplaced')
    parts=snapshot['parts'] if snapshot is not None else query(f'SELECT table,disk_name FROM system.parts WHERE active AND database={literal(database)} '
                f'AND table IN ({selected}) GROUP BY table,disk_name')
    if any(r['disk_name']!=STORAGE_POLICY for r in parts):
        raise ValueError('Staging parts are outside live_market_ssd')
