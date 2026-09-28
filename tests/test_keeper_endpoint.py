from subprocess import CompletedProcess

import pytest

from src.trading_runtime import keeper_endpoint


class Socket:
    closed = False

    def close(self):
        self.closed = True


def test_keeper_discovery_uses_only_reachable_wsl_private_address(monkeypatch):
    monkeypatch.setattr(keeper_endpoint.platform, "system", lambda: "Windows")
    monkeypatch.setattr(keeper_endpoint.platform, "node", lambda: "DESKTOP-SAAI85T")
    commands = []
    connections = []
    socket = Socket()

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return CompletedProcess(command, 0, "8.8.8.8 127.0.0.1 172.25.158.41\n", "")

    def connect(address, timeout):
        connections.append((address, timeout))
        return socket

    endpoint = keeper_endpoint.discover_workstation_keeper_endpoint(
        run=run, connect=connect)
    assert (endpoint.host, endpoint.port) == ("172.25.158.41", 9181)
    assert commands[0][0] == ["wsl.exe", "-d", "Ubuntu", "--", "hostname", "-I"]
    assert connections == [(('172.25.158.41', 9181), 2.0)]
    assert socket.closed


def test_keeper_discovery_rejects_other_hosts_and_unreachable_listener(monkeypatch):
    monkeypatch.setattr(keeper_endpoint.platform, "system", lambda: "Windows")
    monkeypatch.setattr(keeper_endpoint.platform, "node", lambda: "LAPTOP")
    with pytest.raises(RuntimeError, match="workstation-local"):
        keeper_endpoint.discover_workstation_keeper_endpoint(
            run=lambda *_a, **_k: (_ for _ in ()).throw(AssertionError()))
    monkeypatch.setattr(keeper_endpoint.platform, "node", lambda: "DESKTOP-SAAI85T")
    with pytest.raises(RuntimeError, match="reachable"):
        keeper_endpoint.discover_workstation_keeper_endpoint(
            run=lambda command, **_kwargs: CompletedProcess(
                command, 0, "172.25.158.41", ""),
            connect=lambda *_a, **_k: (_ for _ in ()).throw(OSError()),
        )


def test_laptop_keeper_requires_private_endpoint_and_complete_mtls(tmp_path, monkeypatch):
    monkeypatch.setattr(keeper_endpoint.platform, "system", lambda: "Windows")
    monkeypatch.setattr(keeper_endpoint.platform, "node", lambda: "LAPTOP")
    monkeypatch.setenv("TRADING_KEEPER_LAN_HOST", "192.168.1.218")
    monkeypatch.setenv("TRADING_KEEPER_LAN_PORT", "9281")
    with pytest.raises(RuntimeError, match="certificate"):
        keeper_endpoint.discover_keeper_endpoint()
    for name in ("CA_FILE", "CLIENT_CERT_FILE", "CLIENT_KEY_FILE"):
        path = tmp_path / name
        path.write_text("test", encoding="utf-8")
        monkeypatch.setenv(f"TRADING_KEEPER_{name}", str(path))
    endpoint = keeper_endpoint.discover_keeper_endpoint()
    assert (endpoint.host, endpoint.port, endpoint.secure) == ("192.168.1.218", 9281, True)
    monkeypatch.setenv("TRADING_KEEPER_LAN_HOST", "8.8.8.8")
    with pytest.raises(RuntimeError, match="private IPv4"):
        keeper_endpoint.discover_keeper_endpoint()
