"""Comparison admission and coverage invariants, without running a backtest."""
import pytest
import json
import asyncio
from hashlib import sha256
from pathlib import Path

from src.trading_runtime.optimized_strategy_reference import (
    OptimizationReference, compare_sessions,
)


def reference():
    return OptimizationReference(24, 'sealed-winner', 872, {}, {}, {
        f'2026-08-{day:02d}': {'net_pnl': 100., 'closed_positions': 2,
                             'terminal_valid': True}
        for day in range(1, 31)
    })


def result():
    return dict(winner_sha256='sealed-winner', run_id='actual-app-run',
                strategy_digest='sealed-app-release', full_session=True,
                market_cursor_verified=True, verified_sequence=40,
                metrics={'net_pnl': 92., 'closed_positions': 2,
                         'terminal_valid': True})


def test_partial_comparison_keeps_every_reference_session():
    rows = compare_sessions(reference(), {'2026-08-01': result()})
    assert len(rows) == 30
    assert rows[0]['differences'] == {'net_pnl': -8., 'closed_positions': 0}
    assert rows[0]['status'] == 'compared'
    assert all(row['status'] == 'not_run' and row['app'] is None for row in rows[1:])


@pytest.mark.parametrize('field,value', [
    ('winner_sha256', 'another-winner'), ('full_session', False),
    ('market_cursor_verified', False), ('verified_sequence', 0),
    ('run_id', ''), ('strategy_digest', ''),
])
def test_unverified_app_results_are_not_comparison_authority(field, value):
    actual = result(); actual[field] = value
    with pytest.raises(ValueError, match='Unverified'):
        compare_sessions(reference(), {'2026-08-01': actual})


def test_missing_metrics_remain_visible_and_nan_fails():
    actual = result(); actual['metrics'].pop('closed_positions')
    row = compare_sessions(reference(), {'2026-08-01': actual})[0]
    assert row['status'] == 'incomplete_metrics'
    assert row['missing_metrics'] == ['closed_positions']
    actual['metrics']['net_pnl'] = float('nan')
    with pytest.raises(ValueError, match='Non-finite'):
        compare_sessions(reference(), {'2026-08-01': actual})


def test_non_reference_session_is_rejected():
    with pytest.raises(ValueError, match='non-reference'):
        compare_sessions(reference(), {'2027-01-01': result()})


def frozen_root(monkeypatch, tmp_path):
    from src.trading_runtime import optimized_strategy_reference as module
    winner = json.dumps({"program": "synthetic"}).encode()
    digest = sha256(winner).hexdigest()
    monkeypatch.setattr(module, "WINNER_SHA256", digest)
    days = list(reference().sessions)
    files = {"winner.json": winner, "ranking.json": json.dumps([{"candidate": 0}]).encode(),
        "complete.json": json.dumps(dict(training_days=days, validation_opened=False,
            population_sha256="synthetic-population")).encode()}
    for day in days:
        files[f"{day}/receipt.json"] = json.dumps(dict(day=day, full_session=True,
            validation_opened=False, population_sha256="synthetic-population",
            execution={"entry_cash": 1000, "add_cash": 200},
            metrics={"net_pnl": [100.], "terminal_valid": [True]})).encode()
    complete = json.loads(files["complete.json"])
    complete.update(selection_allowed=True, session_receipts={day:
        sha256(files[f"{day}/receipt.json"]).hexdigest() for day in days})
    files["complete.json"] = json.dumps(complete).encode()
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    manifest = dict(generation=24, training_sessions=30, validation_opened=False,
        winner_sha256=digest, files_sha256={name: sha256(content).hexdigest() for name, content in files.items()})
    (tmp_path / "freeze.json").write_text(json.dumps(manifest))
    return module, manifest


@pytest.mark.parametrize("concurrency", [1, 3])
def test_frozen_reader_uses_verified_bytes_without_reopening(monkeypatch, tmp_path, concurrency):
    module, _ = frozen_root(monkeypatch, tmp_path)
    original = Path.read_bytes
    calls = []
    def read(path):
        content = original(path)
        calls.append(path)
        if path.name == "ranking.json":
            path.write_text(json.dumps([{"candidate": 99}]))
        return content
    monkeypatch.setattr(Path, "read_bytes", read)
    actual = asyncio.run(module.load_reference(tmp_path, concurrency=concurrency))
    assert actual.candidate == 0 and len(actual.sessions) == 30
    assert len(calls) == 33 and len(set(calls)) == 33
    assert all(row["status"] == "not_run" for row in compare_sessions(actual, {}))


@pytest.mark.parametrize("missing", ["ranking.json", "complete.json", "2026-08-01/receipt.json"])
def test_every_comparison_metadata_and_session_file_requires_a_seal(monkeypatch, tmp_path, missing):
    module, manifest = frozen_root(monkeypatch, tmp_path)
    manifest["files_sha256"].pop(missing)
    (tmp_path / "freeze.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="Missing frozen|Missing frozen session"):
        module.read_reference(tmp_path)


def test_unexpected_manifest_files_are_rejected_before_opening(monkeypatch, tmp_path):
    module, manifest = frozen_root(monkeypatch, tmp_path)
    manifest["files_sha256"]["validation/sealed.json"] = "a" * 64
    (tmp_path / "freeze.json").write_text(json.dumps(manifest))
    original, opened = Path.read_bytes, []
    def read(path):
        opened.append(path.name)
        return original(path)
    monkeypatch.setattr(Path, "read_bytes", read)
    with pytest.raises(ValueError, match="outside declared training"):
        asyncio.run(module.load_reference(tmp_path, concurrency=3))
    assert opened == ["complete.json"]


@pytest.mark.parametrize("failure", ["selection", "receipt", "terminal"])
def test_generation_authorization_and_winner_terminal_accounting_are_required(monkeypatch, tmp_path, failure):
    module, manifest = frozen_root(monkeypatch, tmp_path)
    seal = json.loads((tmp_path / "complete.json").read_bytes())
    if failure == "selection":
        seal["selection_allowed"] = False
    elif failure == "receipt":
        seal["session_receipts"]["2026-08-01"] = "a" * 64
    else:
        receipt_path = tmp_path / "2026-08-01/receipt.json"
        receipt = json.loads(receipt_path.read_bytes())
        receipt["metrics"]["terminal_valid"] = [False]
        receipt_path.write_text(json.dumps(receipt))
        digest = sha256(receipt_path.read_bytes()).hexdigest()
        manifest["files_sha256"]["2026-08-01/receipt.json"] = digest
        seal["session_receipts"]["2026-08-01"] = digest
    (tmp_path / "complete.json").write_text(json.dumps(seal))
    manifest["files_sha256"]["complete.json"] = sha256((tmp_path / "complete.json").read_bytes()).hexdigest()
    (tmp_path / "freeze.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="authorize|terminal accounting"):
        module.read_reference(tmp_path, concurrency=3)


@pytest.mark.parametrize("tamper", [False, True])
def test_actual_campaign_diagnostic_inventory_is_hash_verified(monkeypatch, tmp_path, tamper):
    module, manifest = frozen_root(monkeypatch, tmp_path)
    names = ["timings.json", "diversity.json"] + [f"{day}/timings.json" for day in reference().sessions]
    for name in names:
        content = b'{"diagnostic":true}'
        (tmp_path / name).write_bytes(content)
        manifest["files_sha256"][name] = sha256(content).hexdigest()
    (tmp_path / "freeze.json").write_text(json.dumps(manifest))
    if tamper:
        (tmp_path / names[-1]).write_bytes(b'{"diagnostic":false}')
        with pytest.raises(ValueError, match="Frozen reference changed"):
            module.read_reference(tmp_path, concurrency=3)
    else:
        actual = module.read_reference(tmp_path, concurrency=3)
        assert actual.candidate == 0 and len(actual.sessions) == 30
