"""The backend must normalize SSL before opening a secure Keeper session."""
from __future__ import annotations

import sys
from types import SimpleNamespace

from scripts import run_backend


def test_backend_keeps_standard_ssl_context(monkeypatch):
    calls = []
    monkeypatch.setattr(run_backend, "ssl", SimpleNamespace(
        SSLContext=type("SSLContext", (), {"__module__": "ssl"})))
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(
        extract_from_ssl=lambda: calls.append("extract")))
    run_backend._restore_stdlib_ssl_for_keeper()
    assert calls == []


def test_backend_restores_injected_ssl_context(monkeypatch):
    calls = []
    monkeypatch.setattr(run_backend, "ssl", SimpleNamespace(
        SSLContext=type("SSLContext", (), {
            "__module__": "pip._vendor.truststore._api"})))
    monkeypatch.setitem(sys.modules, "truststore", SimpleNamespace(
        extract_from_ssl=lambda: calls.append("extract")))
    run_backend._restore_stdlib_ssl_for_keeper()
    assert calls == ["extract"]
