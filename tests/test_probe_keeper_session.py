"""Bounded Keeper lifecycle diagnostics never claim or mutate a znode."""
from types import SimpleNamespace

import pytest

from scripts.clickhouse import probe_keeper_session as probe


def test_probe_reads_root_and_closes_each_session(monkeypatch, capsys):
    calls = []

    def open_session():
        calls.append("open")
        client = SimpleNamespace(exists=lambda path: calls.append(path) or object())

        def close():
            calls.append("close")

        return SimpleNamespace(writable=True, client=client, close=close)

    monkeypatch.setattr(probe, "open_workstation_keeper_session", open_session)
    probe.measure(cycles=2)
    assert calls == ["open", "/", "close"] * 2
    output = capsys.readouterr().out
    assert "Keeper cycle 1/2: connected, root readable" in output
    assert "Keeper cycle 2/2: connected, root readable" in output


def test_probe_fails_closed_and_still_closes(monkeypatch):
    calls = []
    monkeypatch.setattr(probe, "open_workstation_keeper_session", lambda:
        SimpleNamespace(writable=False,
                        client=SimpleNamespace(exists=lambda _: calls.append("read")),
                        close=lambda: calls.append("close")))
    with pytest.raises(RuntimeError, match="cannot read"):
        probe.measure(cycles=1)
    assert calls == ["close"]
    with pytest.raises(ValueError, match="one to three"):
        probe.measure(cycles=4)
