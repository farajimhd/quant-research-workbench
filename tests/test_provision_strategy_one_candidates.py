from __future__ import annotations

import pytest

from scripts.clickhouse import provision_strategy_one_candidates as command


def test_dry_run_never_connects(capsys, monkeypatch):
    monkeypatch.setattr(command, "_admin_client", lambda _url:
                        pytest.fail("dry-run connected"))
    assert command.main([]) == 0
    output = capsys.readouterr()
    assert "DRY RUN" in output.out
    assert "no connection or database change" in output.out
    assert output.err == ""


def test_apply_requires_workstation_and_installs_exactly_once(capsys, monkeypatch):
    class Client:
        closed = False
        def close(self):
            self.closed = True
    client = Client()
    installed = []
    renamed = []
    monkeypatch.setattr(command.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(command.socket, "getaddrinfo", lambda *_a, **_k:
                        [(None, None, None, None, (command.WORKSTATION_IPV4, 18123))])
    monkeypatch.setattr(command, "_admin_client", lambda url:
                        client if url == "http://192.168.1.218:18123" else
                        pytest.fail("wrong endpoint"))
    monkeypatch.setattr(command, "install_tables", lambda value: installed.append(value))
    monkeypatch.setattr(command, "install_pivot_tables",
                        lambda value: installed.append(value))
    monkeypatch.setattr(command, "install_hod_tables",
                        lambda value: installed.append(value))
    monkeypatch.setattr(command, "rename_empty_legacy_rule_column",
                        lambda value: renamed.append(value))
    assert command.main(["--apply", "--confirm-strategy-one-candidates",
                         "--rename-empty-rule-column"]) == 0
    assert installed == [client, client, client] and client.closed
    assert renamed == [client]
    assert "verified" in capsys.readouterr().out


def test_failed_install_closes_client_without_leaking_sql(capsys, monkeypatch):
    class Client:
        closed = False
        def close(self):
            self.closed = True
    client = Client()
    monkeypatch.setattr(command.platform, "node", lambda: "DESKTOP-SAAI85T")
    monkeypatch.setattr(command.socket, "getaddrinfo", lambda *_a, **_k:
                        [(None, None, None, None, (command.WORKSTATION_IPV4, 18123))])
    monkeypatch.setattr(command, "_admin_client", lambda _url: client)
    monkeypatch.setattr(command, "install_tables", lambda _value:
                        (_ for _ in ()).throw(RuntimeError("secret SQL")))
    assert command.main(["--apply", "--confirm-strategy-one-candidates"]) == 1
    output = capsys.readouterr()
    assert client.closed and "secret SQL" not in output.err
