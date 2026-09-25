"""Explicit, separate fixed-Backtest V3 ClickHouse credential boundary."""
from __future__ import annotations

import os
from ipaddress import IPv4Address
from pathlib import Path
import platform
import socket
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit


_USERS = {
    "read": "backtest_v3_reader",
    "running": "backtest_v3_runner",
    "terminal": "backtest_v3_terminal",
}


def _credential(role: str, environment: Mapping[str, str]) -> tuple[str, str, str]:
    if role not in _USERS:
        raise ValueError("Unknown V3 ClickHouse role")
    prefix = f"BACKTEST_V3_{role.upper()}_CLICKHOUSE_"
    path_key = f"BACKTEST_V3_{role.upper()}_CREDENTIAL_FILE"
    inline = {key: environment.get(prefix + key, "") for key in ("URL", "USER", "PASSWORD")}
    path = environment.get(path_key, "").strip()
    if path and any(inline.values()):
        raise ValueError("V3 credentials cannot mix file and inline values")
    if path:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        allowed = {prefix + key for key in inline}
        values: dict[str, str] = {}
        for line in lines:
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or key not in allowed or key in values:
                raise ValueError("V3 credential file has unknown or duplicate fields")
            values[key] = value
        inline = {key: values.get(prefix + key, "") for key in inline}
    url, user, password = (inline[key] for key in ("URL", "USER", "PASSWORD"))
    parsed = urlsplit(url)
    if (user != _USERS[role] or not password or parsed.scheme not in {"http", "https"}
            or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("V3 ClickHouse credential identity is invalid")
    return url, user, password


def v3_client(
    role: str, *, environment: Mapping[str, str] | None = None,
    client_factory: Callable[..., Any] | None = None,
    market_stream: bool = False,
) -> Any:
    """Construct one role client; never consult legacy market/journal credentials."""
    if client_factory is None:
        from research.mlops.clickhouse import ClickHouseHttpClient
        client_factory = ClickHouseHttpClient
    url, user, password = _credential(role, os.environ if environment is None else environment)
    url = _workstation_ipv4_transport(url)
    params = {"readonly": 1, "max_threads": 4, "max_execution_time": 60} if role == "read" else {}
    if market_stream:
        if role != "read":
            raise ValueError("Only V3 reader can stream market data")
        params.update(max_query_size=16 * 1024 * 1024,
                      max_ast_elements=500_000, max_execution_time=21_600)
    return client_factory(url, user, password, timeout_seconds=60,
                          persistent=True, default_query_params=params)


def _workstation_ipv4_transport(url: str) -> str:
    """Avoid the workstation's unreachable self-resolved IPv6 endpoint.

    The credential identity remains unchanged. This applies only on the
    managed workstation and only to its named ClickHouse listener; other
    endpoints are never rewritten.
    """
    parsed = urlsplit(url)
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or parsed.hostname != "desktop-saai85t" or parsed.port != 18123):
        return url
    address = IPv4Address(socket.gethostbyname("DESKTOP-SAAI85T"))
    if not address.is_private:
        raise RuntimeError("Workstation ClickHouse resolved outside the private network")
    return f"{parsed.scheme}://{address}:{parsed.port}"


def v3_clients(*, environment: Mapping[str, str] | None = None,
               client_factory: Callable[..., Any] | None = None) -> tuple[Any, Any, Any]:
    """Validate every role before constructing any client; close partial setup."""
    source = os.environ if environment is None else environment
    identities = [_credential(role, source) for role in _USERS]
    if len({url for url, _, _ in identities}) != 1:
        raise ValueError("V3 principals must target the same ClickHouse endpoint")
    clients = []
    try:
        for role in _USERS:
            clients.append(v3_client(role, environment=source, client_factory=client_factory))
    except BaseException:
        for client in clients:
            client.close()
        raise
    return tuple(clients)
