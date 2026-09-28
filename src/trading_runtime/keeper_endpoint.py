"""Discover the workstation-local WSL Keeper endpoint without exposing 9181.

This module opens a TCP probe only. A reachable port is not proof of a Keeper
protocol peer. It never creates a Keeper session or node; ownership clients
must use a separate session-validated, fenced protocol implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Address, ip_address
import os
from pathlib import Path
import platform
import socket
import subprocess
from typing import Callable


@dataclass(frozen=True, slots=True)
class KeeperEndpoint:
    host: str
    port: int = 9181
    ca_file: Path | None = None
    cert_file: Path | None = None
    key_file: Path | None = None

    @property
    def secure(self) -> bool:
        return all((self.ca_file, self.cert_file, self.key_file))


def discover_keeper_endpoint() -> KeeperEndpoint:
    """Use private WSL Keeper on the workstation, or strict mTLS on laptop.

    No plaintext LAN fallback is permitted. Client certificate material lives
    outside the repository and its paths must be configured explicitly.
    """
    if platform.system() != "Windows":
        raise RuntimeError("Managed Keeper requires a Windows host")
    if platform.node().upper() == "DESKTOP-SAAI85T":
        return discover_workstation_keeper_endpoint()
    host = os.environ.get("TRADING_KEEPER_LAN_HOST", "").strip()
    raw_port = os.environ.get("TRADING_KEEPER_LAN_PORT", "").strip()
    try:
        address = ip_address(host)
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("Secure LAN Keeper endpoint is not configured") from exc
    if (not isinstance(address, IPv4Address) or not address.is_private
            or address.is_loopback or address.is_link_local or not 1 <= port <= 65535):
        raise RuntimeError("Secure LAN Keeper endpoint must be private IPv4")
    paths = tuple(Path(os.environ.get(name, "")).expanduser() for name in (
        "TRADING_KEEPER_CA_FILE", "TRADING_KEEPER_CLIENT_CERT_FILE",
        "TRADING_KEEPER_CLIENT_KEY_FILE"))
    if any(str(path) in {"", "."} or not path.is_file() for path in paths):
        raise RuntimeError("Secure LAN Keeper requires CA, client certificate and key files")
    return KeeperEndpoint(str(address), port, *paths)


def discover_workstation_keeper_endpoint(
    *, distro: str = "Ubuntu", timeout_seconds: float = 2.0,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    connect: Callable[..., socket.socket] = socket.create_connection,
) -> KeeperEndpoint:
    """Find a reachable WSL-private listener from the native workstation.

    WSL's private address can change at restart, so callers must re-discover
    after a broken connection. The Windows firewall continues to block remote
    inbound 9181; this function must never publish a LAN port proxy.
    """
    if platform.system() != "Windows" or platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Keeper endpoint discovery is workstation-local only")
    if (not distro or any(char.isspace() for char in distro)
            or not 0 < timeout_seconds <= 10):
        raise ValueError("Keeper WSL distro or probe timeout is invalid")
    result = run(
        ["wsl.exe", "-d", distro, "--", "hostname", "-I"],
        check=False, capture_output=True, text=True,
        timeout=timeout_seconds,
    )
    if result.returncode:
        raise RuntimeError("Keeper WSL distribution is unavailable")
    for candidate in result.stdout.split():
        try:
            address = ip_address(candidate)
        except ValueError:
            continue
        if (not isinstance(address, IPv4Address) or not address.is_private
                or address.is_loopback or address.is_link_local):
            continue
        endpoint = KeeperEndpoint(str(address))
        try:
            connection = connect((endpoint.host, endpoint.port), timeout_seconds)
        except OSError:
            continue
        connection.close()
        return endpoint
    raise RuntimeError("No workstation-local WSL Keeper endpoint is reachable")
