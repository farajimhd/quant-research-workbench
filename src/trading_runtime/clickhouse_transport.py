"""Resolve the managed workstation's ClickHouse HTTP listener efficiently.

Only the workstation's named, private-network HTTP endpoint is rewritten.
Credentials, database authority, and all other endpoints remain unchanged.
"""
from __future__ import annotations

from ipaddress import IPv4Address
import platform
import socket
from urllib.parse import urlsplit


def workstation_ipv4_transport(url: str) -> str:
    """Avoid slow, unreachable IPv6 self-addresses before the IPv4 listener."""
    parsed = urlsplit(url)
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or parsed.scheme != "http"
            or parsed.hostname != "desktop-saai85t" or parsed.port != 18123
            or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        return url
    address = IPv4Address(socket.gethostbyname("DESKTOP-SAAI85T"))
    if not address.is_private or address.is_loopback or address.is_link_local:
        raise RuntimeError("Workstation ClickHouse resolved outside the private LAN")
    return f"http://{address}:{parsed.port}"
