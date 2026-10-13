"""Names for isolated shared programme authorities; contains no credentials."""
from types import MappingProxyType

PROGRAM_APP_PRINCIPALS = MappingProxyType({
    'market_input_reader': 'program_market_input_reader',
    'history_publisher': 'program_history_publisher',
    'opening_reference_publisher': 'program_opening_reference_publisher',
    'liquidity_publisher': 'program_liquidity_publisher',
    'release_reader': 'program_release_reader',
    'release_publisher': 'program_release_publisher',
    'selection_reader': 'program_selection_reader',
    'selection_publisher': 'program_selection_publisher',
})

PROGRAM_APP_CREDENTIAL_FILES = MappingProxyType({
    principal + '.env': tuple(principal.upper() + '_CLICKHOUSE_' + suffix
        for suffix in ('URL', 'USER', 'PASSWORD'))
    for principal in PROGRAM_APP_PRINCIPALS.values()
})

_READ_PROFILES = MappingProxyType({
    **{role: PROGRAM_APP_PRINCIPALS[role] for role in
        ('market_input_reader', 'release_reader', 'selection_reader')},
    'journal_reader': 'backtest_v4_program_runner',
})

_WRITE_ROLES = frozenset(('history_publisher', 'opening_reference_publisher',
    'liquidity_publisher', 'release_publisher', 'selection_publisher'))


def _open_managed_client(principal, *, timeout_seconds, settings):
    from urllib.parse import urlsplit
    from src.trading_runtime.arte_journal_writer import _dedicated_clickhouse_credentials
    from src.trading_runtime.clickhouse_transport import workstation_ipv4_transport
    from research.mlops.clickhouse import ClickHouseHttpClient
    stem = principal.upper()
    url, user, password = _dedicated_clickhouse_credentials(stem + '_CLICKHOUSE_', stem + '_CREDENTIAL_FILE')
    try:
        parsed = urlsplit(url)
        valid = (user == principal and bool(password) and parsed.scheme == 'http'
            and parsed.hostname == 'desktop-saai85t' and parsed.port == 18123
            and not parsed.username and not parsed.password and parsed.path in ('', '/')
            and not parsed.query and not parsed.fragment)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('Programme credentials differ from the managed profile')
    return ClickHouseHttpClient(workstation_ipv4_transport(url), user, password,
        timeout_seconds=timeout_seconds, default_query_params=dict(settings))


def _bounded_query_settings(query_limits):
    from collections.abc import Mapping
    required = {'max_threads', 'max_memory_usage', 'max_execution_time',
        'max_result_rows', 'max_result_bytes'}
    if (not isinstance(query_limits, Mapping) or set(query_limits) != required
            or any(type(value) is not int or value <= 0 for value in query_limits.values())):
        raise ValueError('Programme connection requires positive explicit bounded limits')
    return dict(query_limits, max_insert_threads=1, result_overflow_mode='throw',
        max_bytes_before_external_group_by=0, max_bytes_before_external_sort=0)


def program_app_writer_factory(role, *, query_limits):
    """Dedicated publication clients with explicit bounded operator settings.

    No connection or secret is opened at construction. Publication contracts
    still check exact grants, storage, parent evidence and producer ownership.
    This provides neither DDL authority nor an approval to execute a producer.
    """
    if type(role) is not str or role not in _WRITE_ROLES:
        raise ValueError('Programme writer requires an explicit publication role')
    settings = _bounded_query_settings(query_limits)
    principal = PROGRAM_APP_PRINCIPALS[role]
    timeout = settings['max_execution_time'] + 30
    def owned():
        return _open_managed_client(principal, timeout_seconds=timeout, settings=settings)
    return owned


def program_app_reader_factory(role, *, query_limits=None):
    """Lazy owned connections for the existing off-loop native readers.

    Creating the factory performs no I/O. Each invocation reads its explicit
    private credentials in the caller's owned worker and creates one connection
    that the native reader closes. There is no shared connection or fallback.
    """
    if type(role) is not str or role not in _READ_PROFILES:
        raise ValueError('Programme reader factory requires an explicit read-only role')
    principal = _READ_PROFILES[role]
    settings = {'readonly': '1'} if query_limits is None else dict(
        _bounded_query_settings(query_limits), readonly='1')
    timeout = 60 if query_limits is None else settings['max_execution_time'] + 30
    def owned():
        client = _open_managed_client(principal, timeout_seconds=timeout, settings=settings)
        if role == 'journal_reader':
            client.program_decision_profile = True
        return client
    return owned
