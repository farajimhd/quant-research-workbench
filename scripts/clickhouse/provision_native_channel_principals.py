"""Install native-channel tables and two narrow principals; dry-run by default."""
import argparse
from hashlib import sha256
import os
from pathlib import Path
import platform
import re
import secrets
import sys
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True

from scripts.clickhouse.provision_trading_journal import SECRET_ROOT, _admin_client, _restrict_secret_file
from src.backend.native_channel_clients import PRINCIPALS
from src.market_engine.native_causal_channel_contract import FEATURE_TABLE, COVERAGE_TABLE
from src.trading_runtime.clickhouse_transport import workstation_ipv4_transport
from pipelines.market_sip.events.native_channel_campaign import install
from research.mlops.clickhouse import ClickHouseHttpClient

URL = 'http://DESKTOP-SAAI85T:18123'
READ_TABLES = (FEATURE_TABLE, COVERAGE_TABLE, 'arte.bars_v1', 'arte.indicators_v1',
               'arte.liquidity_100ms_v1', 'system.storage_policies', 'system.tables',
               'system.parts', 'system.columns')
GRANTS = {'read': frozenset(('SELECT', t) for t in READ_TABLES),
          'producer': frozenset(('INSERT', t) for t in (FEATURE_TABLE, COVERAGE_TABLE))}


class ProvisioningError(RuntimeError):
    """Safe operator message, constructed without credentials or driver output."""


def _credential(role, *, account_exists):
    prefix = f'NATIVE_CHANNEL_{role.upper()}_CLICKHOUSE_'
    path = SECRET_ROOT / f'native_causal_channel_{role}.env'
    if not path.exists():
        if account_exists:
            raise ProvisioningError('Existing native principal lacks its private credential; no rotation')
        with path.open('x', encoding='utf-8'):
            pass
    _restrict_secret_file(path)
    values = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or key in values or key not in {prefix+n for n in ('URL','USER','PASSWORD')}:
            raise ProvisioningError('Native private credential is malformed; no replacement')
        values[key] = value
    if values:
        if (set(values) != {prefix+n for n in ('URL','USER','PASSWORD')} or
                values[prefix+'URL'] != URL or values[prefix+'USER'] != PRINCIPALS[role] or
                len(values[prefix+'PASSWORD']) < 40):
            raise ProvisioningError('Native private credential identity differs; no replacement')
        return values[prefix+'PASSWORD']
    if account_exists:
        raise ProvisioningError('Existing native principal has an empty private credential; no rotation')
    password = secrets.token_urlsafe(48)
    path.write_text(f'{prefix}URL={URL}\n{prefix}USER={PRINCIPALS[role]}\n{prefix}PASSWORD={password}\n', encoding='utf-8')
    return password


def _grant_set(client, role):
    principal = PRINCIPALS[role]
    if client.execute('SELECT currentUser()').strip() != principal:
        raise ProvisioningError('Native credential authenticates as another principal')
    pattern = re.compile(rf'GRANT ([A-Z ,]+) ON ((?:arte|system)\.[A-Za-z_][A-Za-z0-9_]*) TO {principal}\Z')
    grants = set()
    for line in client.execute('SHOW GRANTS FINAL').splitlines():
        match = pattern.fullmatch(line.strip())
        if not match:
            raise ProvisioningError('Native principal has broad or unrecognized grants; held')
        grants.update((p.strip(), match.group(2)) for p in match.group(1).split(','))
    if not grants <= GRANTS[role]:
        raise ProvisioningError('Native principal has grants outside its exact product/source scope; held')
    return frozenset(grants)


def provision(admin, *, credential=_credential, client_factory):
    """Keep existing Backtest roles untouched; install only the new product family."""
    install(admin)
    result = []
    for role, principal in PRINCIPALS.items():
        count = admin.execute(f"SELECT count() FROM system.users WHERE name='{principal}' FORMAT TabSeparated").strip()
        if count not in {'0','1'}:
            raise ProvisioningError('Native principal inventory is inconsistent')
        password = credential(role, account_exists=count == '1')
        if type(password) is not str or len(password) < 40:
            raise ProvisioningError('Native private password is incomplete')
        if count == '0':
            settings = 'readonly=1,max_threads=2,max_execution_time=60' if role == 'read' else 'max_threads=2,max_execution_time=60'
            digest = sha256(password.encode()).hexdigest()
            admin.execute(f"CREATE USER {principal} IDENTIFIED WITH sha256_hash BY '{digest}' "
                f"HOST IP '172.16.0.0/12', IP '127.0.0.1', IP '::1' SETTINGS {settings}")
        client = client_factory(role, principal, password)
        try:
            before = _grant_set(client, role)
            for privilege, table in sorted(GRANTS[role] - before):
                admin.execute(f'GRANT {privilege} ON {table} TO {principal}')
            if _grant_set(client, role) != GRANTS[role]:
                raise ProvisioningError('Native principal exact grants did not reconcile')
            result.append(dict(role=role, principal=principal, created=count == '0', grants=len(GRANTS[role])))
        finally:
            client.close()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='install only absent feature tables and scoped principals')
    args = parser.parse_args(argv)
    if not args.apply:
        print('Plan: 2 SSD feature tables; reader SELECT on 9 tables; producer INSERT on 2.')
        print('No changes made. Apply on DESKTOP-SAAI85T with --apply.')
        return 0
    if platform.node().upper() != 'DESKTOP-SAAI85T' or not SECRET_ROOT.is_dir():
        print('Blocked: managed workstation and private secret root are required.', file=sys.stderr)
        return 1
    admin = None
    try:
        endpoint = workstation_ipv4_transport(URL)
        admin = _admin_client(endpoint)
        result = provision(admin, client_factory=lambda role, user, password: ClickHouseHttpClient(
            endpoint, user, password, timeout_seconds=20, persistent=True,
            default_query_params={'readonly':1} if role == 'read' else {}))
    except Exception as error:
        # Driver errors may embed SQL or credentials. Only our safe messages are displayed.
        reason = str(error) if type(error) is ProvisioningError else type(error).__name__
        stage = next((f.name for f in reversed(traceback.extract_tb(error.__traceback__))
                      if f.filename == __file__), 'operator_dependency')
        print(f'Native provisioning stopped at {stage}: {reason}.', file=sys.stderr)
        print('Partial setup may remain; rerun verifies existing state.', file=sys.stderr)
        return 1
    finally:
        if admin is not None:
            admin.close()
    print(f'Complete: 2 SSD tables and {len(result)} narrowly scoped native principals verified.')
    print('Existing Backtest roles unchanged. Feature rows have not been published.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
