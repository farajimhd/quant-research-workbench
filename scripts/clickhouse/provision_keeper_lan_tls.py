"""Provision Keeper mTLS without moving private keys between machines.

Run ``client-request`` on the laptop, copy only client.csr to the workstation
runtime directory, then run ``server-sign`` there. Copy only ca.crt and
client.crt back to the laptop runtime directory and run ``client-install``.
This script does not restart ClickHouse or publish a LAN listener.
"""
from __future__ import annotations

import argparse
from ipaddress import IPv4Address, ip_address
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys


sys.dont_write_bytecode = True
RUNTIME = Path(r"D:\TradingML\runtimes\keeper-lan")
LAPTOP_SECRET = Path(r"D:\TradingML\secrets\keeper-lan")
WSL_SECRET = "/etc/clickhouse-server/keeper-tls"


def _run(args: list[str]) -> str:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Certificate command failed: {args[0]} exit={result.returncode}")
    return result.stdout.strip()


def _wsl(*args: str) -> str:
    return _run(["wsl.exe", "-d", "Ubuntu", "-u", "root", "--", *args])


def _wsl_path(path: Path) -> str:
    # WSL's Windows-command transport consumes backslashes as escapes.
    return _wsl("wslpath", "-a", str(path.resolve()).replace("\\", "/"))


def _openssl() -> str:
    binary = Path(sys.prefix) / "Library" / "bin" / "openssl.exe"
    if not binary.is_file():
        raise RuntimeError("The managed Python environment has no OpenSSL binary")
    return str(binary)


def _openssl_config() -> str:
    candidates = (Path(sys.prefix) / "Library" / "ssl" / "openssl.cnf",
                  Path(r"C:\Program Files\Git\usr\ssl\openssl.cnf"))
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("OpenSSL configuration file is unavailable")


def _private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    principal = _run(["whoami"])
    _run(["icacls", str(path), "/inheritance:r", "/grant:r",
          f"{principal}:(OI)(CI)F"])


def client_request() -> None:
    if platform.node().upper() == "DESKTOP-SAAI85T":
        raise RuntimeError("Client private key must be generated on the laptop")
    if not Path(r"D:\TradingML\runtimes").is_dir():
        raise RuntimeError("Laptop runtime root is unavailable")
    _private_directory(LAPTOP_SECRET)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    key = LAPTOP_SECRET / "client.key"
    csr = RUNTIME / "client.csr"
    if key.exists() or csr.exists():
        raise RuntimeError("Client key or request already exists; refusing replacement")
    _run([_openssl(), "req", "-new", "-newkey", "rsa:3072", "-nodes",
          "-keyout", str(key), "-out", str(csr),
          "-subj", "/CN=quant-workbench-laptop-keeper-client",
          "-config", _openssl_config()])
    print(f"Client request ready: {csr}. Transfer this public request only.")


def server_sign(workstation_ip: str) -> None:
    if platform.node().upper() != "DESKTOP-SAAI85T":
        raise RuntimeError("Keeper CA and server keys must remain on the workstation")
    address = ip_address(workstation_ip)
    if (not isinstance(address, IPv4Address) or not address.is_private
            or address.is_loopback or address.is_link_local):
        raise ValueError("Workstation address must be private IPv4")
    csr = RUNTIME / "client.csr"
    if not csr.is_file():
        raise RuntimeError("Public laptop client.csr is missing from workstation runtime")
    _wsl("install", "-d", "-o", "root", "-g", "clickhouse", "-m", "750", WSL_SECRET)
    ca_key, ca_cert = (f"{WSL_SECRET}/{name}" for name in ("ca.key", "ca.crt"))
    server_key, server_csr, server_cert = (
        f"{WSL_SECRET}/{name}" for name in ("server.key", "server.csr", "server.crt"))
    # Never replace an existing CA or server identity. A partial state needs
    # operator inspection rather than an implicit rekey.
    existing = set(_wsl("ls", "-1", WSL_SECRET).splitlines())
    ca_files = {"ca.key", "ca.crt"}
    initial = ca_files | {"server.key", "server.csr"}
    server_signed = initial | {"ca.srl", "server.crt"}
    signed = server_signed | {"client.crt"}
    if existing not in (set(), ca_files, initial, server_signed, signed):
        raise RuntimeError("Keeper TLS identity has an unexpected partial state")
    if not existing:
        _wsl("openssl", "req", "-x509", "-newkey", "rsa:3072", "-nodes",
             "-days", "3650", "-keyout", ca_key, "-out", ca_cert,
             "-subj", "/CN=Quant Workbench Keeper CA",
             "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    if existing in (set(), ca_files):
        _wsl("openssl", "req", "-new", "-newkey", "rsa:3072", "-nodes",
             "-keyout", server_key, "-out", server_csr,
             "-subj", "/CN=DESKTOP-SAAI85T")
    server_ext = RUNTIME / "server.ext"
    server_ext.write_text(
        f"basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
        f"extendedKeyUsage=serverAuth\nsubjectAltName=IP:{address}\n",
        encoding="ascii")
    client_ext = RUNTIME / "client.ext"
    client_ext.write_text(
        "basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
        "extendedKeyUsage=clientAuth\n", encoding="ascii")
    if existing in (set(), ca_files, initial):
        _wsl("openssl", "x509", "-req", "-in", server_csr, "-CA", ca_cert,
             "-CAkey", ca_key, "-CAcreateserial", "-out", server_cert,
             "-days", "825", "-sha256", "-extfile", _wsl_path(server_ext))
    client_cert = f"{WSL_SECRET}/client.crt"
    if existing != signed:
        _wsl("openssl", "x509", "-req", "-in", _wsl_path(csr),
             "-CA", ca_cert, "-CAkey", ca_key, "-CAcreateserial",
             "-out", client_cert, "-days", "825", "-sha256",
             "-extfile", _wsl_path(client_ext))
    _wsl("openssl", "verify", "-CAfile", ca_cert, server_cert, client_cert)
    _wsl("chown", "root:clickhouse", ca_cert, server_cert, server_key)
    _wsl("chmod", "640", ca_cert, server_cert, server_key)
    _wsl("chmod", "600", ca_key)
    for name in ("ca.crt", "client.crt"):
        _wsl("cp", f"{WSL_SECRET}/{name}", _wsl_path(RUNTIME / name))
    print(f"Public CA and client certificate ready under {RUNTIME}.")


def client_install() -> None:
    if platform.node().upper() == "DESKTOP-SAAI85T":
        raise RuntimeError("Client certificate must be installed on the laptop")
    _private_directory(LAPTOP_SECRET)
    for name in ("client.key",):
        if not (LAPTOP_SECRET / name).is_file():
            raise RuntimeError(f"Laptop private key is missing: {name}")
    for name in ("ca.crt", "client.crt"):
        source = RUNTIME / name
        target = LAPTOP_SECRET / name
        if not source.is_file() or target.exists():
            raise RuntimeError(f"Public certificate missing or already installed: {name}")
        shutil.copyfile(source, target)
    _run([_openssl(), "verify", "-CAfile", str(LAPTOP_SECRET / "ca.crt"),
          str(LAPTOP_SECRET / "client.crt")])
    print(f"Keeper client certificate installed under {LAPTOP_SECRET}.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("client-request", "server-sign", "client-install"))
    parser.add_argument("--workstation-ip", default="192.168.1.218")
    args = parser.parse_args()
    if args.action == "client-request":
        client_request()
    elif args.action == "server-sign":
        server_sign(args.workstation_ip)
    else:
        client_install()


if __name__ == "__main__":
    main()
