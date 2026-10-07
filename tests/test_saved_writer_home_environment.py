"""Historical source-only children retain OS home without credential authority."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.backend import backtest_saved_writer_source as writer


def _proof(monkeypatch, capture):
    monkeypatch.setattr(writer.subprocess, "run", capture)
    writer._native_proof.cache_clear()
    try:
        return writer._native_proof("root", "a" * 40, "b" * 64, "c" * 64, 76, "e" * 64)
    finally:
        writer._native_proof.cache_clear()


def _result():
    return subprocess.CompletedProcess([], 0, json.dumps(dict(
        schema="historical-native-writer-source-v1", number=76, commit="a" * 40,
        backend_fingerprint="c" * 64, code_hash="b" * 64,
        certificate="d" * 64)).encode())


def test_resolved_home_preserved_with_closed_environment(monkeypatch):
    expected = str(Path.home())
    observed = {}
    for key in ("BACKTEST_CLICKHOUSE_PASSWORD", "API_KEY", "PYTHONPATH", "PYTHONHOME",
                "BACKTEST_SAVED_WRITER_ROOTS_FILE", "HOMEDRIVE", "HOMEPATH"):
        monkeypatch.setenv(key, "must-not-enter-child")
    def capture(args, **kwargs):
        assert args[1:3] == ["-I", "-B"]
        observed.update(kwargs["env"])
        return _result()
    assert _proof(monkeypatch, capture) == "d" * 64
    assert observed["USERPROFILE" if os.name == "nt" else "HOME"] == expected
    assert set(observed) <= {"SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC",
                             "USERPROFILE" if os.name == "nt" else "HOME",
                             "PYTHONDONTWRITEBYTECODE"}


def test_real_isolated_child_can_resolve_home_without_secret(monkeypatch):
    expected = str(Path.home())
    original = subprocess.run
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_PASSWORD", "never-forward")
    def capture(args, **kwargs):
        child = original([sys.executable, "-I", "-B", "-c",
            "import os,json;from pathlib import Path;"
            "print(json.dumps([str(Path.home()),'BACKTEST_CLICKHOUSE_PASSWORD' in os.environ]))"],
            env=kwargs["env"], capture_output=True, timeout=30, check=True)
        assert json.loads(child.stdout) == [expected, False]
        return _result()
    assert _proof(monkeypatch, capture) == "d" * 64


def test_unresolvable_home_fails_before_subprocess(monkeypatch):
    called = []
    def unavailable(cls):
        raise RuntimeError("Could not determine home directory")
    monkeypatch.setattr(Path, "home", classmethod(unavailable))
    with pytest.raises(RuntimeError, match="home directory"):
        _proof(monkeypatch, lambda *a, **kw: called.append(a))
    assert called == []
