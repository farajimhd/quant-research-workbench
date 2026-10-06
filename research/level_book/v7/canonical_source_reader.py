"""Private SELECT-only canonical V7 preparation source authority.

Separate from Backtest configuration/product certification and archive INSERT
credentials. Storage assignment is explicit, never inferred from a default.
"""
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re

PRINCIPAL = 'canonical_v7_source_reader'
POLICY_ID = 'canonical-v7-private-source-read@1'
STEM = 'CANONICAL_V7_SOURCE_READ_CLICKHOUSE_'
FILE_KEY = 'CANONICAL_V7_SOURCE_READ_CREDENTIAL_FILE'
POLICY_KEY = 'CANONICAL_V7_SOURCE_READ_STORAGE_POLICY'
REFERENCE_TABLES = (('market_sip_compact','events_ordinal_continuity'),
                    ('market_sip_compact','event_condition_token_reference'),
                    ('q_live','historical_trade_reporting_coverage_v1'),
                    ('q_live','market_stock_split_v1'))
SYSTEM_TABLES = ('tables','parts','storage_policies')


@dataclass(frozen=True)
class SourceReadPlan:
    years: tuple[int, ...]
    canonical_policy: str

    def __post_init__(self):
        if (type(self) is not SourceReadPlan or type(self.years) is not tuple or not self.years or len(self.years)>128
                or any(type(y) is not int or not 1 <= y <= 9999 for y in self.years)
                or self.years != tuple(sorted(set(self.years)))):
            raise ValueError('Exact ordered source years required')
        if (type(self.canonical_policy) is not str or not re.fullmatch('[A-Za-z_][A-Za-z0-9_]*',self.canonical_policy)
                or self.canonical_policy.lower() in ('default','hdd')):
            raise ValueError('Explicit assigned canonical SIP storage policy required')

    @property
    def tables(self):
        self.__post_init__()
        return tuple(sorted((*REFERENCE_TABLES,
            *(('market_sip_compact',f'events_{y}') for y in self.years),
            *(('system',name) for name in SYSTEM_TABLES))))

    @property
    def grants(self):
        return frozenset(('SELECT',db,table) for db,table in self.tables)

    def payload(self):
        return dict(policy_id=POLICY_ID,years=list(self.years),canonical_policy=self.canonical_policy,
                    tables=[list(t) for t in self.tables])


def parse_source_contract(value):
    if (type(value) is not dict or set(value)!={'policy_id','years','canonical_policy','tables'}
            or value['policy_id']!=POLICY_ID or type(value['years']) is not list):
        raise ValueError('Exact declared canonical source-reader contract required')
    selected=SourceReadPlan(tuple(value['years']),value['canonical_policy'])
    if (type(value['tables']) is not list or any(type(t) is not list or len(t)!=2
            or any(type(s) is not str for s in t) for t in value['tables'])
            or value != selected.payload()):
        raise ValueError('Declared canonical source table closure differs')
    return selected


class SourceReader:
    """Database grants are the table boundary; SQL transport is read-only too."""
    def __init__(self, client):
        self._client = client

    def execute(self, sql):
        if type(sql) is not str or ';' in sql:
            raise ValueError('Canonical source transport permits read-only inspection only')
        inspection=re.match(r'^\s*(SHOW GRANTS FINAL\s*$|CHECK GRANT\b)',sql,re.I)
        if not inspection:
            from src.backend.backtest_market_data import assert_select_only
            # Classification only: execute the original SQL unchanged. The
            # shared guard otherwise mistakes quoted writer words and approved
            # system metadata qualifiers for actual administrative operations.
            masked=re.sub(r"'(?:\\.|''|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|`(?:\\.|[^`\\])*`|--[^\n]*(?:\n|$)|/\*.*?\*/",' ',sql,flags=re.S)
            masked=re.sub(r'\bsystem\.(tables|parts|storage_policies)\b',r'layout.\1',masked,flags=re.I)
            assert_select_only(masked)
            if (not re.search(r'\bSELECT\b',masked,re.I)
                    or re.search(r'\b(UPDATE|DELETE|RENAME)\b',masked,re.I)):
                raise ValueError('Canonical source query is not a SELECT operation')
        try:
            return self._client.execute(sql)
        except Exception as exc:
            # SELECT text contains no credentials; underlying HTTP errors can.
            raise ValueError(f'Canonical source request failed ({type(exc).__name__}); '
                             f'query_sha256={sha256(sql.encode()).hexdigest()}') from None

    def close(self):
        self._client.close()


def _rows(client, sql):
    raw = client.execute(sql+' FORMAT JSONEachRow')
    if len(raw.encode()) > 64*1024**2:
        raise ValueError('Canonical source metadata response exceeds bound')
    rows = [json.loads(line) for line in raw.splitlines() if line]
    if len(rows)>100000:
        raise ValueError('Canonical source metadata row bound exceeded')
    return rows


def verify_grants(client, plan, *, complete=True):
    if type(plan) is not SourceReadPlan:
        raise ValueError('Exact canonical source read plan required')
    plan.__post_init__()
    actual = set()
    for line in client.execute('SHOW GRANTS FINAL').splitlines():
        match = re.fullmatch(r'GRANT SELECT ON ([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*) TO '+PRINCIPAL,line.strip())
        if match is None:
            raise ValueError('Canonical source principal has broad or foreign authority')
        value = ('SELECT',*match.groups())
        if value not in plan.grants or value in actual:
            raise ValueError('Canonical source principal grant scope differs')
        actual.add(value)
    if complete and actual != plan.grants:
        raise ValueError('Canonical source principal lacks exact required SELECT grants')
    return frozenset(actual)


def storage_preflight(client, plan):
    """Verify assigned canonical policy and actual active event part placement."""
    if type(plan) is not SourceReadPlan:
        raise ValueError('Exact canonical source read plan required')
    plan.__post_init__()
    policy = _rows(client,f"SELECT disks FROM system.storage_policies WHERE policy_name='{plan.canonical_policy}'")
    if (len(policy)!=1 or type(policy[0].get('disks')) is not list or not policy[0]['disks']
            or any(type(d) is not str or d.lower() in ('default','hdd') for d in policy[0]['disks'])):
        raise ValueError('Assigned canonical policy is absent or routes to backup storage')
    names = [f'events_{y}' for y in plan.years]
    selected = ','.join("'"+name+"'" for name in names)
    tables = _rows(client,"SELECT name,storage_policy FROM system.tables WHERE database='market_sip_compact' "
                         f'AND name IN ({selected})')
    if (len(tables)!=len(names) or {r['name'] for r in tables}!=set(names)
            or any(r['storage_policy']!=plan.canonical_policy for r in tables)):
        raise ValueError('Canonical event tables differ from assigned storage policy')
    disks = ','.join("'"+d.replace("'","\\'")+"'" for d in policy[0]['disks'])
    bad = _rows(client,"SELECT table,disk_name FROM system.parts WHERE active AND database='market_sip_compact' "
                      f'AND table IN ({selected}) AND disk_name NOT IN ({disks}) LIMIT 1')
    if bad:
        raise ValueError('Canonical event active parts lie outside assigned policy')
    # Supporting metadata must exist; no ALTER/CREATE or disk migration here.
    for db,table in REFERENCE_TABLES:
        if _rows(client,f"SELECT name FROM system.tables WHERE database='{db}' AND name='{table}'") != [{'name':table}]:
            raise ValueError('Canonical source reference table missing')


def source_client(plan):
    """Existing protected FILE only; no generic discovery, inline or fallback."""
    from scripts.clickhouse.provision_trading_journal import SECRET_ROOT, _restrict_secret_file
    from scripts.clickhouse.provision_fixed_backtest_v3_principals import URL
    from src.trading_runtime.arte_journal_writer import _dedicated_clickhouse_credentials
    from src.trading_runtime.clickhouse_transport import workstation_ipv4_transport
    from research.mlops.clickhouse import ClickHouseHttpClient
    if type(plan) is not SourceReadPlan:
        raise ValueError('Exact canonical source read plan required')
    plan.__post_init__()
    path = Path(os.environ.get(FILE_KEY,''))
    roots = (SECRET_ROOT.resolve(),Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\secrets').resolve())
    if (not path.is_file() or path.resolve().parent not in roots or path.name!=PRINCIPAL+'.env'
            or any(os.environ.get(STEM+suffix) for suffix in ('URL','USER','PASSWORD'))):
        raise ValueError('Canonical source requires its exact protected private FILE profile')
    _restrict_secret_file(path)
    url,user,password = _dedicated_clickhouse_credentials(STEM,FILE_KEY)
    if url != URL or user != PRINCIPAL or not password:
        raise ValueError('Canonical source private identity differs')
    raw = ClickHouseHttpClient(workstation_ipv4_transport(url),user,password,
        timeout_seconds=180,persistent=True,default_query_params=dict(readonly=1,max_threads=1,
        max_execution_time=180,max_memory_usage=1024**3,max_result_rows=100000,
        max_result_bytes=64*1024**2,result_overflow_mode='throw'))
    client = SourceReader(raw)
    try:
        if client.execute('SELECT currentUser()').strip()!=PRINCIPAL:
            raise ValueError('Canonical source authenticated as foreign principal')
        verify_grants(client,plan)
        storage_preflight(client,plan)
        return client
    except BaseException:
        client.close()
        raise
