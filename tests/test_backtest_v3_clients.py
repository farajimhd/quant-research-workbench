from pathlib import Path

import pytest

from src.backend.backtest_v3_clients import v3_client, v3_clients


def _env():
    result = {}
    for role, user in (("READ", "backtest_v3_reader"),
                       ("RUNNING", "backtest_v3_runner"),
                       ("TERMINAL", "backtest_v3_terminal")):
        prefix = f"BACKTEST_V3_{role}_CLICKHOUSE_"
        result[prefix + "URL"] = "http://workstation:18123"
        result[prefix + "USER"] = user
        result[prefix + "PASSWORD"] = "private-test-value"
    return result


def test_distinct_v3_clients_and_readonly_market_stream():
    calls = []
    def factory(url, user, password, **kwargs):
        calls.append((url, user, password, kwargs))
        return object()
    read, running, terminal = v3_clients(environment=_env(), client_factory=factory)
    assert len({id(read), id(running), id(terminal)}) == 3
    assert [call[1] for call in calls] == [
        "backtest_v3_reader", "backtest_v3_runner", "backtest_v3_terminal"]
    assert calls[0][3]["default_query_params"]["readonly"] == 1
    assert all("readonly" not in call[3]["default_query_params"] for call in calls[1:])
    v3_client("read", environment=_env(), client_factory=factory,
              market_stream=True)
    assert calls[-1][3]["default_query_params"]["max_query_size"] > 0


def test_missing_or_shared_legacy_identity_fails_before_client_creation():
    env = _env()
    env["BACKTEST_V3_RUNNING_CLICKHOUSE_USER"] = "backtest_v3_reader"
    calls = []
    with pytest.raises(ValueError, match="identity"):
        v3_clients(environment=env, client_factory=lambda *a, **k: calls.append(a))
    assert calls == []
    with pytest.raises(ValueError, match="identity"):
        v3_client("read", environment={"BACKTEST_CLICKHOUSE_URL": "http://old"},
                  client_factory=lambda *a, **k: calls.append(a))
    assert calls == []


def test_private_file_is_exclusive_source(tmp_path: Path):
    env = _env()
    path = tmp_path / "read.env"
    path.write_text("\n".join(
        f"{key}={env.pop(key)}" for key in (
            "BACKTEST_V3_READ_CLICKHOUSE_URL",
            "BACKTEST_V3_READ_CLICKHOUSE_USER",
            "BACKTEST_V3_READ_CLICKHOUSE_PASSWORD")), encoding="utf-8")
    env["BACKTEST_V3_READ_CREDENTIAL_FILE"] = str(path)
    observed = []
    v3_client("read", environment=env,
              client_factory=lambda *a, **k: observed.append(a))
    assert observed[0][1] == "backtest_v3_reader"
    env["BACKTEST_V3_READ_CLICKHOUSE_USER"] = "backtest_v3_reader"
    with pytest.raises(ValueError, match="mix"):
        v3_client("read", environment=env,
                  client_factory=lambda *a, **k: observed.append(a))


def test_market_reader_opt_in_uses_only_v3_read_principal(monkeypatch):
    from src.backend import backtest_market_data, backtest_v3_clients
    calls = []
    sentinel = object()
    monkeypatch.setattr(backtest_v3_clients, "v3_client",
                        lambda role, *, market_stream: calls.append(
                            (role, market_stream)) or sentinel)
    assert backtest_market_data.readonly_clickhouse_client(
        market_stream=True, v3_read_principal=True) is sentinel
    assert calls == [("read", True)]


def test_managed_workstation_uses_ipv4_transport_without_changing_credentials(
        monkeypatch):
    from src.backend import backtest_v3_clients
    monkeypatch.setattr(backtest_v3_clients.platform, "node",
                        lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(backtest_v3_clients.socket, "gethostbyname",
                        lambda host: "192.168.1.218")
    env = _env()
    env["BACKTEST_V3_READ_CLICKHOUSE_URL"] = "http://DESKTOP-SAAI85T:18123"
    calls = []
    v3_client("read", environment=env,
              client_factory=lambda *args, **kwargs: calls.append(args))
    assert calls[0][:2] == ("http://192.168.1.218:18123", "backtest_v3_reader")
    assert env["BACKTEST_V3_READ_CLICKHOUSE_URL"] == "http://DESKTOP-SAAI85T:18123"


def test_nonprivate_workstation_resolution_fails_closed(monkeypatch):
    from src.backend import backtest_v3_clients
    monkeypatch.setattr(backtest_v3_clients.platform, "node",
                        lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(backtest_v3_clients.socket, "gethostbyname",
                        lambda host: "8.8.8.8")
    env = _env()
    env["BACKTEST_V3_READ_CLICKHOUSE_URL"] = "http://DESKTOP-SAAI85T:18123"
    with pytest.raises(RuntimeError, match="outside the private network"):
        v3_client("read", environment=env,
                  client_factory=lambda *args, **kwargs: object())
