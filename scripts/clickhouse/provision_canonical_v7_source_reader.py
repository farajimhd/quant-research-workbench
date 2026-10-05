"""Provision canonical metadata/events SELECT only; plan mode makes no connection."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import sys

os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from scripts.clickhouse.provision_fixed_backtest_v3_principals import (
    PrincipalPlan, WORKSTATION_IPV4, _desired_grants, _effective_grants)
from scripts.clickhouse.provision_trading_journal import SECRET_ROOT, _admin_client, _restrict_secret_file
from src.backend.canonical_v7_source_client import PRINCIPAL, STEM, URL, CanonicalSourceReadClient

SECRET_PATH=SECRET_ROOT/(PRINCIPAL+'.env')
REQUIRED_SOURCE_TABLES=frozenset({('market_sip_compact','events_source_day_stats'),
    ('market_sip_compact','events_ordinal_continuity'),('q_live','historical_trade_reporting_coverage_v1')})


def discover_event_tables(client):
    sql="SELECT name FROM system.tables WHERE database='market_sip_compact' AND match(name,'^events_[0-9]{4}$') ORDER BY name FORMAT JSONEachRow"
    rows=[json.loads(line) for line in client.execute(sql).splitlines() if line.strip()]
    names=tuple(row['name'] for row in rows)
    if (len(set(names))!=len(names) or any(type(name) is not str or re.fullmatch(r'events_[0-9]{4}',name) is None for name in names)):
        raise ValueError('Canonical event table catalog is ambiguous')
    selected=tuple(name for name in names if 1900<=int(name[-4:])<=2026)
    if not selected or 'events_2026' not in selected:
        raise ValueError('Canonical event catalog lacks required through2026 authority')
    return selected


def desired_plan(event_tables):
    if (type(event_tables) is not tuple or not event_tables or 'events_2026' not in event_tables or tuple(sorted(set(event_tables)))!=event_tables
            or any(type(name) is not str or re.fullmatch(r'events_[0-9]{4}',name) is None or not 1900<=int(name[-4:])<=2026 for name in event_tables)):
        raise ValueError('Canonical grants require an exact discovered through2026 table catalog')
    return PrincipalPlan('canonical-source',PRINCIPAL,frozenset(),frozenset(),frozenset({'tables'}),
        frozenset({('market_sip_compact',name) for name in event_tables}) | REQUIRED_SOURCE_TABLES)


def verify_required_source_catalog(client):
    pairs=','.join(f"('{database}','{table}')" for database,table in sorted(REQUIRED_SOURCE_TABLES))
    rows=[json.loads(line) for line in client.execute(
        'SELECT database,name FROM system.tables WHERE (database,name) IN ('+pairs+') FORMAT JSONEachRow').splitlines() if line.strip()]
    if len(rows)!=len(REQUIRED_SOURCE_TABLES) or {(row['database'],row['name']) for row in rows}!=REQUIRED_SOURCE_TABLES:
        raise ValueError('Canonical source metadata/reporting catalog is missing or duplicated')


def credential(*,account_exists):
    if SECRET_PATH.exists():
        _restrict_secret_file(SECRET_PATH)
        from src.backend.canonical_v7_source_client import _credential
        return _credential({})
    if account_exists:
        raise RuntimeError('Existing canonical source principal lacks its private credential')
    with SECRET_PATH.open('x'):
        pass
    _restrict_secret_file(SECRET_PATH)
    password=secrets.token_urlsafe(48)
    SECRET_PATH.write_text(f'{STEM}URL={URL}\n{STEM}USER={PRINCIPAL}\n{STEM}PASSWORD={password}\n')
    _restrict_secret_file(SECRET_PATH)
    return password


def apply_with_clients(*,admin,credential,client_factory):
    if admin.execute('SELECT currentUser()').strip()==PRINCIPAL:
        raise RuntimeError('Canonical source provisioning requires a distinct administrator')
    plan=desired_plan(discover_event_tables(admin))
    verify_required_source_catalog(admin)
    present=admin.execute(f"SELECT count() FROM system.users WHERE name='{PRINCIPAL}' FORMAT TabSeparated").strip()
    if present not in {'0','1'}:
        raise RuntimeError('Canonical source principal inventory is ambiguous')
    password=credential(account_exists=present=='1')
    if type(password) is not str or len(password)<40:
        raise RuntimeError('Canonical source principal requires a complete private credential')
    reader=None
    try:
        if present=='1':
            reader=client_factory(PRINCIPAL,password)
            if reader.execute('SELECT currentUser()').strip()!=PRINCIPAL:
                raise RuntimeError('Canonical private credential authenticates as another user')
            have=_effective_grants(reader,plan) # Reject inherited/broad/unexpected authority before mutation.
        else:
            have=frozenset()
            admin.execute(f"CREATE USER {PRINCIPAL} IDENTIFIED WITH sha256_hash BY '{sha256(password.encode()).hexdigest()}' "
                "HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1' SETTINGS readonly=1 READONLY")
            reader=client_factory(PRINCIPAL,password)
        for privilege,database,table in sorted(_desired_grants(plan)-have):
            admin.execute(f'GRANT {privilege} ON {database}.{table} TO {PRINCIPAL}')
        if (reader.execute('SELECT currentUser()').strip()!=PRINCIPAL
                or reader.execute("SELECT getSetting('readonly')").strip()!='1'
                or _effective_grants(reader,plan)!=_desired_grants(plan)
                or discover_event_tables(reader)!=tuple(sorted(name for database,name in plan.select_reference if database=='market_sip_compact' and re.fullmatch(r'events_[0-9]{4}',name)))):
            raise RuntimeError('Canonical source exact grants/read identity/catalog differ after provisioning')
        return plan
    finally:
        if reader is not None:
            reader.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--confirm-canonical-source-reader',action='store_true')
    args=parser.parse_args(argv)
    if not args.apply:
        print(f'Plan: {PRINCIPAL}; SELECT canonical source metadata, reporting coverage, exact events_YYYY through2026 and system.tables')
        print('No connection or change. Apply discovers exact catalog; extra existing grants fail closed.')
        return 0
    if not args.confirm_canonical_source_reader:
        parser.error('--apply requires --confirm-canonical-source-reader')
    if platform.node().upper()!='DESKTOP-SAAI85T' or not SECRET_ROOT.is_dir():
        raise RuntimeError('Canonical source provisioning requires managed workstation private secrets root')
    addresses={row[4][0] for row in socket.getaddrinfo('desktop-saai85t',18123,family=socket.AF_INET,type=socket.SOCK_STREAM)}
    if WORKSTATION_IPV4 not in addresses:
        raise RuntimeError('Canonical source workstation transport identity differs')
    from research.mlops.clickhouse import ClickHouseHttpClient
    transport=f'http://{WORKSTATION_IPV4}:18123'
    admin=None
    try:
        admin=_admin_client(transport)
        plan=apply_with_clients(admin=admin,credential=credential,
            client_factory=lambda user,password:CanonicalSourceReadClient(ClickHouseHttpClient(
                transport,user,password,timeout_seconds=60,default_query_params={'readonly':1})))
    except Exception as exc:
        print(f'Canonical source provisioning stopped: {type(exc).__name__}; inspect private diagnostics before retry',file=sys.stderr)
        return 1
    finally:
        if admin is not None:
            admin.close()
    print(f'Canonical source reader verified: {len(_desired_grants(plan))} exact SELECT grants; 0 rows written')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
