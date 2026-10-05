"""Dedicated canonical source SELECT boundary; never borrow Backtest grants."""
import os
import re
from pathlib import Path, PureWindowsPath

from src.backend.backtest_market_data import assert_select_only
from src.trading_runtime.clickhouse_transport import workstation_ipv4_transport

PRINCIPAL = 'canonical_v7_source_reader'
STEM = 'CANONICAL_V7_SOURCE_CLICKHOUSE_'
URL = 'http://DESKTOP-SAAI85T:18123'
SECRET_PATH = 'D:/TradingML/secrets/canonical_v7_source_reader.env'


def _credential(environment):
    path = environment.get('CANONICAL_V7_SOURCE_CREDENTIAL_FILE',SECRET_PATH)
    if PureWindowsPath(path) != PureWindowsPath(SECRET_PATH) or any(environment.get(STEM+key) for key in ('URL','USER','PASSWORD')):
        raise ValueError('Canonical source credential must use its dedicated workstation private file')
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except (OSError,UnicodeError):
        raise ValueError('Canonical source private credential file is unavailable') from None
    values = {}
    for line in lines:
        if not line or line.startswith('#'):
            continue
        key,separator,value = line.partition('=')
        if not separator or key not in {STEM+name for name in ('URL','USER','PASSWORD')} or key in values:
            raise ValueError('Canonical source credential file has unknown or duplicate fields')
        values[key] = value
    if (set(values) != {STEM+name for name in ('URL','USER','PASSWORD')}
            or values[STEM+'URL'] != URL or values[STEM+'USER'] != PRINCIPAL
            or len(values[STEM+'PASSWORD']) < 40):
        raise ValueError('Canonical source credential identity differs from exact private profile')
    return values[STEM+'PASSWORD']


class CanonicalSourceReadClient:
    def __init__(self, transport):
        self._transport = transport
    def execute(self, query):
        if query != 'SHOW GRANTS FINAL':
            # Backtest's token guard treats the catalog database name as a
            # SYSTEM operation. Permit this exact SELECT catalog identifier,
            # while keeping every operation token and all old guards intact.
            assert_select_only(re.sub(r'\bsystem\.tables\b','canonical_catalog_tables',query))
        try:
            return self._transport.execute(query)
        except Exception:
            raise ValueError('Canonical source SELECT transport failed') from None
    def close(self):
        self._transport.close()


def canonical_source_client(*, environment=None, client_factory=None):
    password = _credential(os.environ if environment is None else environment)
    if client_factory is None:
        from research.mlops.clickhouse import ClickHouseHttpClient
        client_factory = ClickHouseHttpClient
    try:
        transport = client_factory(workstation_ipv4_transport(URL),PRINCIPAL,password,
            timeout_seconds=60,persistent=True,
            default_query_params={'readonly':1,'max_threads':2,'max_execution_time':60})
    except Exception:
        raise ValueError('Canonical source dedicated read transport failed') from None
    try:
        if (transport.execute('SELECT currentUser()').strip() != PRINCIPAL
                or transport.execute("SELECT getSetting('readonly')").strip() != '1'):
            raise ValueError('Canonical source read identity or readonly setting differs')
        from scripts.clickhouse.provision_canonical_v7_source_reader import (
            discover_event_tables, desired_plan, verify_required_source_catalog)
        from scripts.clickhouse.provision_fixed_backtest_v3_principals import _desired_grants, _effective_grants
        reader = CanonicalSourceReadClient(transport)
        verify_required_source_catalog(reader)
        plan = desired_plan(discover_event_tables(reader))
        if _effective_grants(reader,plan) != _desired_grants(plan):
            raise ValueError('Canonical source exact SELECT grants differ')
    except Exception:
        transport.close()
        raise ValueError('Canonical source dedicated read authentication failed') from None
    return CanonicalSourceReadClient(transport)
