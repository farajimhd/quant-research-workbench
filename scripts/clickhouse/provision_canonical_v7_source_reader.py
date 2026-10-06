"""Plan a private canonical V7 SELECT principal; explicit apply only.

No tables, data or existing principal grants are changed.
"""
import argparse
from hashlib import sha256
import os
from pathlib import Path
import platform
import secrets
import socket
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from research.level_book.v7.canonical_source_reader import PRINCIPAL, STEM, SourceReadPlan, storage_preflight, verify_grants
from scripts.clickhouse.provision_trading_journal import SECRET_ROOT, _admin_client, _restrict_secret_file
from scripts.clickhouse.provision_fixed_backtest_v3_principals import URL, WORKSTATION_IPV4
from src.runtime_paths import WORKSTATION_NAME


def credential(*, account_exists):
    path=SECRET_ROOT/(PRINCIPAL+'.env')
    keys={STEM+s for s in ('URL','USER','PASSWORD')}
    if path.exists():
        _restrict_secret_file(path)
        values={}
        for line in path.read_text(encoding='utf-8').splitlines():
            key,separator,value=line.partition('=')
            if not separator or key not in keys or key in values:
                raise ValueError('Existing canonical source credential fields differ')
            values[key]=value
        if (set(values)!=keys or values[STEM+'URL']!=URL or values[STEM+'USER']!=PRINCIPAL
                or len(values[STEM+'PASSWORD'])<40):
            raise ValueError('Existing canonical source private identity differs')
        return values[STEM+'PASSWORD']
    if account_exists:
        raise ValueError('Existing source principal lacks its private file; refusing rotation')
    with path.open('x'):pass
    _restrict_secret_file(path)
    password=secrets.token_urlsafe(48)
    path.write_text(f'{STEM}URL={URL}\n{STEM}USER={PRINCIPAL}\n{STEM}PASSWORD={password}\n',encoding='utf-8')
    _restrict_secret_file(path)
    return password


def apply_with_clients(plan, *, admin, private_credential, client_factory):
    if type(plan) is not SourceReadPlan:raise ValueError('Exact canonical source read plan required')
    plan.__post_init__()
    if admin.execute('SELECT currentUser()').strip()==PRINCIPAL:
        raise ValueError('Distinct provisioning administrator required')
    storage_preflight(admin,plan)
    present=admin.execute(f"SELECT count() FROM system.users WHERE name='{PRINCIPAL}' FORMAT TabSeparated").strip()
    if present not in ('0','1'):raise ValueError('Principal inventory ambiguous')
    password=private_credential(account_exists=present=='1')
    if type(password) is not str or len(password)<40:raise ValueError('Private complete credential required')
    if present=='0':
        hashed=sha256(password.encode()).hexdigest()
        admin.execute(f"CREATE USER {PRINCIPAL} IDENTIFIED WITH sha256_hash BY '{hashed}' HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1'")
    reader=client_factory(PRINCIPAL,password)
    try:
        if reader.execute('SELECT currentUser()').strip()!=PRINCIPAL:raise ValueError('Foreign authenticated principal')
        have=verify_grants(reader,plan,complete=False)
        for privilege,db,table in sorted(plan.grants-have):
            admin.execute(f'GRANT {privilege} ON {db}.{table} TO {PRINCIPAL}')
        verify_grants(reader,plan)
        storage_preflight(reader,plan)
    finally:reader.close()


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--years',type=int,nargs='+',required=True,help='Exact verified parent source years')
    parser.add_argument('--canonical-policy',required=True,help='Assigned canonical SIP policy; never default/hdd')
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--confirm-source-reader',action='store_true')
    args=parser.parse_args(argv)
    try:
        plan=SourceReadPlan(tuple(args.years),args.canonical_policy)
        if not args.apply:
            print(f'Plan only | {PRINCIPAL} | SELECT {len(plan.grants)} | INSERT 0 | DDL grants 0')
            for _,db,table in sorted(plan.grants):print(f'  SELECT {db}.{table}')
            print('No connection, credential, grant or row changes.')
            return 0
        if not args.confirm_source_reader:raise ValueError('Explicit apply confirmation required')
        if platform.node().upper()!=WORKSTATION_NAME or not SECRET_ROOT.is_dir():raise ValueError('Managed workstation secret root required')
        addresses={r[4][0] for r in socket.getaddrinfo(WORKSTATION_NAME,18123,family=socket.AF_INET,type=socket.SOCK_STREAM)}
        if WORKSTATION_IPV4 not in addresses:raise ValueError('Pinned workstation transport differs')
        from research.mlops.clickhouse import ClickHouseHttpClient
        transport=f'http://{WORKSTATION_IPV4}:18123'
        admin=_admin_client(transport)
        try:
            apply_with_clients(plan,admin=admin,private_credential=credential,
                client_factory=lambda user,password:ClickHouseHttpClient(transport,user,password,
                    timeout_seconds=180,default_query_params={'readonly':1,'max_threads':1}))
        finally:admin.close()
        print('Exact SELECT grants and canonical placement verified; no data rows written.')
        return 0
    except Exception as exc:
        print(f'Canonical source provisioning blocked ({type(exc).__name__}); no automatic retry.',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
