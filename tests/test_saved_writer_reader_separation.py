"""Registered historical writer and current reader keep distinct authorities."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from src.backend import backtest_saved_writer_source as writer
from src.backend import backtest_saved_reader_certification as reader
from src.backend import historical_runtime_versions as versions


def row(root):
    return dict(root=str(root), execution_commit="a" * 40, code_hash="b" * 64,
                backend_fingerprint="c" * 64, published_commit="d" * 40,
                published_fingerprint="e" * 64)


def registry(monkeypatch, tmp_path, rows):
    path = tmp_path / "approved-frozen-writers.json"
    payload = dict(schema="saved-writer-root-registry-v1",
                   reader_fingerprint=versions.LOADED_BACKEND_FINGERPRINT, writers=rows)
    raw = json.dumps(payload).encode()
    path.write_bytes(raw)
    monkeypatch.setenv("BACKTEST_SAVED_WRITER_ROOTS_FILE", str(path))
    monkeypatch.setenv("BACKTEST_SAVED_WRITER_ROOTS_SHA256", sha256(raw).hexdigest())
    return payload, path


def selection(r):
    return writer.registered_writer({"code_hash": r["code_hash"]},
                                    {"approved_code_commit": r["published_commit"],
                                     "approved_code_fingerprint": r["published_fingerprint"]})


def test_no_registry_keeps_existing_route_without_error_fallback(monkeypatch):
    monkeypatch.delenv("BACKTEST_SAVED_WRITER_ROOTS_FILE", raising=False)
    monkeypatch.delenv("BACKTEST_SAVED_WRITER_ROOTS_SHA256", raising=False)
    assert writer.registered_writer({}, {}) is None


def test_two_published_configs_can_share_one_exact_execution_source(monkeypatch, tmp_path):
    one = row(tmp_path)
    two = {**one, "published_commit": "f" * 40, "published_fingerprint": "0" * 64}
    registry(monkeypatch, tmp_path, [one, two])
    assert selection(one)[0] == one
    assert selection(two)[0] == two


@pytest.mark.parametrize("defect", ["duplicate", "ambiguous_root", "ambiguous_commit", "ambiguous_backend",
                                   "unknown_key", "bad_code_hash", "relative_root", "bad_commit"])
def test_closed_registry_rejects_ambiguous_or_foreign_rows(monkeypatch, tmp_path, defect):
    one = row(tmp_path)
    two = {**one, "published_commit": "f" * 40}
    rows = [one, two]
    if defect == "duplicate":
        rows = [one, deepcopy(one)]
    elif defect.startswith("ambiguous_"):
        field = {"ambiguous_root": "root", "ambiguous_commit": "execution_commit",
                 "ambiguous_backend": "backend_fingerprint"}[defect]
        two[field] = str(tmp_path / "foreign") if field == "root" else "0" * len(two[field])
    elif defect == "unknown_key":
        one["approved"] = True
    elif defect == "bad_code_hash":
        one["code_hash"] = "b" * 63
    elif defect == "relative_root":
        one["root"] = "relative"
    else:
        one["execution_commit"] = "HEAD"
    registry(monkeypatch, tmp_path, rows)
    with pytest.raises(ValueError):
        selection(one)


def test_known_execution_crossed_manifest_rejects_instead_of_fallback(monkeypatch, tmp_path):
    one = row(tmp_path)
    registry(monkeypatch, tmp_path, [one])
    bad = {**one, "published_fingerprint": "0" * 64}
    with pytest.raises(ValueError, match="published source identity"):
        selection(bad)
    assert writer.registered_writer({"code_hash": "1" * 64}, {}) is None


def test_registry_byte_change_requires_new_operator_approval(monkeypatch, tmp_path):
    one = row(tmp_path)
    _, path = registry(monkeypatch, tmp_path, [one])
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="bytes differ"):
        selection(one)


@pytest.mark.parametrize("field", ["number", "commit", "backend_fingerprint", "code_hash", "certificate", "extra"])
def test_historical_subprocess_cannot_return_unbound_proof(monkeypatch, field):
    payload = dict(schema="historical-native-writer-source-v1", number=66, commit="a" * 40,
                   backend_fingerprint="c" * 64, code_hash="b" * 64, certificate="d" * 64)
    payload[field] = 67 if field == "number" else "foreign"
    class Result:
        returncode = 0
        stdout = json.dumps(payload).encode()
    monkeypatch.setattr(writer.subprocess, "run", lambda *a, **k: Result())
    writer._native_proof.cache_clear()
    with pytest.raises(ValueError, match="crosses its source binding"):
        writer._native_proof("root", "a" * 40, "b" * 64, "c" * 64, 66, "e" * 64)


def test_historical_process_environment_has_no_credentials_or_python_injection(monkeypatch):
    observed = {}
    payload = dict(schema="historical-native-writer-source-v1", number=66, commit="a" * 40,
                   backend_fingerprint="c" * 64, code_hash="b" * 64, certificate="d" * 64)
    class Result:
        returncode = 0
        stdout = json.dumps(payload).encode()
    def capture(args, **kwargs):
        observed.update(kwargs)
        assert args[1:3] == ["-I", "-B"]
        return Result()
    monkeypatch.setenv("BACKTEST_CLICKHOUSE_PASSWORD", "never-forward")
    monkeypatch.setenv("PYTHONPATH", "foreign")
    monkeypatch.setattr(writer.subprocess, "run", capture)
    writer._native_proof.cache_clear()
    assert writer._native_proof("root", "a" * 40, "b" * 64, "c" * 64, 66, "e" * 64) == "d" * 64
    assert "BACKTEST_CLICKHOUSE_PASSWORD" not in observed["env"]
    assert "PYTHONPATH" not in observed["env"]
    assert observed["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


def test_actual_reader_closure_passes_its_exact_loaded_source():
    result = reader.certify_saved_reader_source(versions.LOADED_BACKEND_FINGERPRINT)
    assert len(result) == 64


@pytest.mark.parametrize("defect", ["missing", "extra", "order", "type"])
def test_reader_closure_keyset_order_and_types_are_closed(monkeypatch, defect):
    changed = dict(reader.SAVED_READER_SOURCE_AST)
    if defect == "missing":
        changed.pop(next(iter(changed)))
    elif defect == "extra":
        changed["src/backend/unknown.py"] = "a" * 64
    elif defect == "order":
        changed = dict(reversed(list(changed.items())))
    else:
        changed[next(iter(changed))] = {}
    monkeypatch.setattr(reader, "SAVED_READER_SOURCE_AST", changed)
    with pytest.raises(ValueError, match="closed declaration"):
        reader.certify_saved_reader_source(versions.LOADED_BACKEND_FINGERPRINT)


@pytest.mark.parametrize("relative", [
    "src/trading_runtime/arte_oms_projection.py",
    "src/trading_runtime/arte_journal_commit_v4.py",
    "src/trading_runtime/arte_original_risk_diagnostic_v4.py",
    "src/trading_runtime/original_risk_checkpoint.py",
    "src/backend/backtest_saved_source_authority.py",
    "src/backend/backtest_v4_saved_review.py",
    "scripts/clickhouse/report_strategy_one_trades.py",
    "research/mlops/clickhouse.py",
])
def test_actual_read_consumer_source_mutations_reject(monkeypatch, relative):
    original = Path.read_text
    target = versions.ROOT / relative
    def changed(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        return value + "\nUNREVIEWED_SAVED_READ_GLOBAL = True\n" if path == target else value
    monkeypatch.setattr(Path, "read_text", changed)
    # The disk-fingerprint guard would also reject internal byte changes. Keep
    # that prerequisite fixed to exercise the separate AST dependency fence.
    monkeypatch.setattr(versions, "backend_source_fingerprint", lambda: versions.LOADED_BACKEND_FINGERPRINT)
    with pytest.raises(ValueError, match="pinned source changed: " + relative):
        reader.certify_saved_reader_source(versions.LOADED_BACKEND_FINGERPRINT)


def test_unapproved_reader_fingerprint_cannot_use_historical_writer():
    with pytest.raises(ValueError, match="approved loaded identity"):
        reader.certify_saved_reader_source("0" * 64)

@pytest.mark.parametrize("defect", ["global", "import", "docstring", "duplicate_function", "loaded_function", "literal"])
def test_certifier_envelope_and_loaded_source_are_closed(monkeypatch, defect):
    original = Path.read_text
    target = Path(reader.__file__).resolve()
    def changed(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if path.resolve() != target:
            return value
        if defect == "global":
            return value + "\nUNREVIEWED_GLOBAL = True\n"
        if defect == "import":
            return value + "\nimport subprocess\n"
        if defect == "docstring":
            return value.replace("Independent current-source", "Changed current-source", 1)
        if defect == "duplicate_function":
            return value + "\ndef certify_saved_reader_source(value):\n    return value\n"
        if defect == "literal":
            return value.replace(next(iter(reader.SAVED_READER_SOURCE_AST.values())), "0" * 64, 1)
        return value.replace('    actual = {}', '    actual = dict()', 1)
    monkeypatch.setattr(Path, "read_text", changed)
    monkeypatch.setattr(versions, "backend_source_fingerprint", lambda: versions.LOADED_BACKEND_FINGERPRINT)
    with pytest.raises(ValueError, match="module envelope|loaded declaration|loaded certifier"):
        reader.certify_saved_reader_source(versions.LOADED_BACKEND_FINGERPRINT)
