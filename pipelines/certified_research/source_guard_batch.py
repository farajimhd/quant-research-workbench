"""Round-local transport batching for unchanged source authority validators.

Both source snapshots are still fetched independently. No TTL, persistent
authority cache, source hashing change, or omitted mutation-owner check.
"""
import json

from . import source

OWNER_EXISTS = "SELECT count() n FROM system.tables WHERE database='q_live' AND name='historical_native_reporting_repair_v1'"


def statements(days):
    """Discover exact read statements from existing validators, without I/O.

    Empty capture results only build SQL; they never qualify an authority.
    Mutation-owner metadata is fetched in the same transport request, while
    its optional header read remains fresh and conditional in source.py.
    """
    captured=[]
    def capture(sql):
        captured.append(sql)
        return []
    canonical=source.inventory(capture,days[0],days[-1],sessions=days)
    source.reporting_inventory(capture,canonical)
    if len(captured)!=4 or len(set(captured))!=4 or any(not q.startswith('SELECT ') for q in captured):
        raise ValueError('Source metadata batching contract changed; review exact validators')
    return tuple(captured)+(OWNER_EXISTS,)


def fetch(query,queries):
    """Tagged named tuples preserve column names and JSONEachRow scalar types."""
    branches=[]
    for tag,sql in enumerate(queries):
        branches.append(f"SELECT toUInt8({tag}) guard_tag,'' guard_payload")
        branches.append(f'SELECT toUInt8({tag}) guard_tag,toJSONString(tuple(*)) guard_payload FROM ({sql})')
    sql=' UNION ALL '.join('('+branch+')' for branch in branches)
    sql+=' SETTINGS enable_named_columns_in_function_tuple=1,output_format_json_named_tuples_as_objects=1'
    rows=query(sql)
    result={q:[] for q in queries};sentinels=[0]*len(queries)
    for row in rows:
        if set(row)!={'guard_tag','guard_payload'}:raise ValueError('Invalid batched source metadata envelope')
        tag=int(row['guard_tag'])
        if not 0<=tag<len(queries):raise ValueError('Unknown batched source metadata tag')
        if row['guard_payload']=='':
            sentinels[tag]+=1
            continue
        value=json.loads(row['guard_payload'])
        if not isinstance(value,dict):raise ValueError('Source metadata lost typed column names')
        result[queries[tag]].append(value)
    if sentinels!=[1]*len(queries):raise ValueError('Source metadata batch is incomplete or duplicated')
    return result


class RoundQuery:
    """Dispatch exact original SQL, refreshing on each inventory-start read."""
    def __init__(self,query,days):
        self.query=query
        self.queries=statements(days)
        self.pending={}

    def __call__(self,sql):
        if sql==self.queries[0]:
            self.pending=fetch(self.query,self.queries)
        if sql in self.pending:
            return self.pending.pop(sql)
        # Header reads and any new validator checks retain their existing path.
        return self.query(sql)
