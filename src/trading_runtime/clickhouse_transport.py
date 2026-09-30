"""Resolve the managed workstation's ClickHouse HTTP listener efficiently.

Only the workstation's named, private-network HTTP endpoint is rewritten.
Credentials, database authority, and all other endpoints remain unchanged.
"""
from __future__ import annotations

from ipaddress import IPv4Address
import platform
import socket
from threading import Lock
from time import monotonic
from urllib.parse import urlsplit


_probe_lock = Lock()
_verified_listener: tuple[str, float] | None = None
_LISTENER_TTL_SECONDS = 5.0


def workstation_ipv4_transport(url: str) -> str:
    """Use the reachable private IPv4 listener, not a WSL adapter alias."""
    parsed = urlsplit(url)
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or parsed.scheme != "http"
            or parsed.hostname != "desktop-saai85t" or parsed.port != 18123
            or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        return url
    # Client factories fan out across read workers. Simultaneous short TCP
    # probes can overflow the listener backlog even when HTTP is healthy.
    # Share only a recent successful probe; real requests still fail closed.
    global _verified_listener
    with _probe_lock:
        if _verified_listener is not None:
            endpoint, expires = _verified_listener
            if monotonic() < expires:
                return endpoint
        _verified_listener = None
        endpoint = _probe_workstation_listener(parsed.port)
        _verified_listener = (endpoint, monotonic() + _LISTENER_TTL_SECONDS)
        return endpoint


def _probe_workstation_listener(port: int) -> str:
    try:
        resolved = socket.getaddrinfo(
            "DESKTOP-SAAI85T", port, family=socket.AF_INET,
            type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RuntimeError("Workstation ClickHouse IPv4 resolution failed") from exc
    addresses = tuple(dict.fromkeys(IPv4Address(item[4][0]) for item in resolved))
    private_addresses = tuple(address for address in addresses
                              if address.is_private and not address.is_loopback
                              and not address.is_link_local)
    if not private_addresses:
        raise RuntimeError("Workstation ClickHouse resolved outside the private LAN")
    for address in private_addresses:
        try:
            with socket.create_connection((str(address), port), timeout=0.25):
                return f"http://{address}:{port}"
        except OSError:
            continue
    raise RuntimeError("Workstation ClickHouse has no reachable private IPv4 listener")
