"""Bounded SELECT-only certification of normalized QMD auxiliary lineage."""
from datetime import date
from collections import OrderedDict
from threading import RLock
from src.market_engine.feature_source_plan import UNIT, SEAL, TABLES, FeatureDependency, FeatureSourcePlan
from src.market_engine.opening_price_reference import require_hash
from src.trading_runtime.arte_journal_schema import storage_preflight
from .qmd_columnar_history import _rows


class FeatureSourcePlanCache:
    """Producer/session-owned cold proofs, fenced by actual immutable parts.

    A merge, mutation, insert, deletion, placement change or seal change forces
    a full normalized read. No process-global or disk cache is used.
    """
    def __init__(self, maximum_plans=2):
        if type(maximum_plans) is not int or not 1<=maximum_plans<=8:
            raise ValueError('Feature plan cache requires bounded ownership')
        self.maximum_plans=maximum_plans;self._items=OrderedDict();self._lock=RLock()

    def read(self, reader, plan_hash):
        require_hash(plan_hash)
        def snapshot():
            names=','.join("'"+t.name+"'" for t in TABLES)
            parts=_rows(reader,'SELECT table,name,disk_name,hash_of_all_files FROM system.parts '
                f"WHERE active AND database='q_live' AND table IN ({names}) ORDER BY table,name LIMIT 20001",20000)
            if not parts or any(r['disk_name']!='live_market_ssd' for r in parts):
                raise ValueError('Feature plan cache lacks actual SSD part evidence')
            seals=_rows(reader,f"SELECT * FROM q_live.{SEAL.name} WHERE plan_hash='{plan_hash}' LIMIT 2",2)
            return parts,seals
        with self._lock:
            storage_preflight(reader,tables=TABLES)
            before=snapshot()
            cached=self._items.get(plan_hash)
            if cached is not None and cached[0]==before:
                self._items.move_to_end(plan_hash)
                return cached[1]
            plan=read_feature_source_plan(reader,plan_hash)
            if snapshot()!=before:
                raise ValueError('Feature source plan parts changed during cold audit')
            self._items[plan_hash]=(before,plan)
            self._items.move_to_end(plan_hash)
            while len(self._items)>self.maximum_plans:self._items.popitem(last=False)
            return plan


def read_feature_source_plan(reader, plan_hash):
    require_hash(plan_hash)
    storage_preflight(reader, tables=TABLES)
    predicate = f"plan_hash='{plan_hash}'"
    seals = _rows(reader, f'SELECT * FROM q_live.{SEAL.name} WHERE {predicate} LIMIT 2', 2)
    if len(seals) != 1 or set(seals[0]) != {n for n, _ in SEAL.columns}:
        raise ValueError('Feature source plan is absent, ambiguous or malformed')
    seal = seals[0]
    count = int(seal['dependency_count'])
    if not 1 <= count <= 200000:
        raise ValueError('Feature source dependency census exceeds its bound')
    dependencies = []
    for first in range(0, count, 1024):
        records = _rows(reader, f'SELECT * FROM q_live.{UNIT.name} WHERE {predicate} '
            f'AND ordinal>={first} AND ordinal<{min(count,first+1024)} ORDER BY ordinal LIMIT 1025',1025)
        if [int(r['ordinal']) for r in records] != list(range(first,min(count,first+1024))):
            raise ValueError('Feature dependencies have missing or duplicate ordinals')
        for record in records:
            if set(record) != {n for n, _ in UNIT.columns} or record['plan_hash'] != plan_hash:
                raise ValueError('Feature dependency differs from its scalar schema')
            row = {k:v for k,v in record.items() if k not in ('plan_hash','ordinal')}
            row['session_date'] = date.fromisoformat(row['session_date'])
            row['row_count'] = int(row['row_count'])
            dependencies.append(FeatureDependency(**row))
    totals = _rows(reader, f'SELECT count() AS n,uniqExact(ordinal) AS u FROM q_live.{UNIT.name} WHERE {predicate}',1)
    if len(totals)!=1 or int(totals[0]['n'])!=count or int(totals[0]['u'])!=count:
        raise ValueError('Feature dependencies contain extra or duplicate rows')
    plan = FeatureSourcePlan(seal['market_plan_hash'],seal['algorithm_hash'],
        date.fromisoformat(seal['session_date']),int(seal['first_bucket']),int(seal['stop_bucket']),
        int(seal['universe_count']),tuple(dependencies))
    if plan.plan_hash != plan_hash:
        raise ValueError('Feature source plan content hash differs')
    if _rows(reader, f'SELECT * FROM q_live.{SEAL.name} WHERE {predicate} LIMIT 2',2) != seals:
        raise ValueError('Feature source plan changed during certification')
    return plan
