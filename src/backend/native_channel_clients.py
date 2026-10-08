"""Private, distinct principals for native feature SELECT and INSERT transports."""
import os
from pathlib import Path
from urllib.parse import urlsplit

from src.trading_runtime.clickhouse_transport import workstation_ipv4_transport

PRINCIPALS = {'read': 'native_causal_channel_reader', 'producer': 'native_causal_channel_producer'}


def native_channel_client(role, *, environment=None, client_factory=None):
    if role not in PRINCIPALS:
        raise ValueError('Unknown native channel principal role')
    environment = os.environ if environment is None else environment
    prefix = f'NATIVE_CHANNEL_{role.upper()}_CLICKHOUSE_'
    path = environment.get(f'NATIVE_CHANNEL_{role.upper()}_CREDENTIAL_FILE', '')
    if not path or any(environment.get(prefix+k) for k in ('URL','USER','PASSWORD')):
        raise ValueError('Explicit private native credential file required; no inline mixing')
    allowed = {prefix+k for k in ('URL','USER','PASSWORD')}
    values = {}
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or key not in allowed or key in values:
            raise ValueError('Native channel credential has foreign or duplicate fields')
        values[key] = value
    if set(values) != allowed or values[prefix+'USER'] != PRINCIPALS[role] or len(values[prefix+'PASSWORD']) < 40:
        raise ValueError('Native channel credential identity is incomplete')
    url = values[prefix+'URL']
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or
            parsed.path not in {'','/'} or parsed.query or parsed.fragment):
        raise ValueError('Native channel endpoint identity is invalid')
    if client_factory is None:
        from research.mlops.clickhouse import ClickHouseHttpClient
        client_factory = ClickHouseHttpClient
    params = {'readonly':1,'max_threads':2,'max_execution_time':60} if role == 'read' else {}
    return client_factory(workstation_ipv4_transport(url), values[prefix+'USER'], values[prefix+'PASSWORD'],
        timeout_seconds=60, persistent=True, default_query_params=params)
