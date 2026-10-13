"""Comparison admission and coverage invariants, without running a backtest."""
import pytest
import json
import asyncio
from hashlib import sha256
from pathlib import Path
from decimal import Decimal

from src.trading_runtime.optimized_strategy_reference import (
    OptimizationReference, compare_sessions, _read_reference_clock_window,
    ReferenceClockWindow,
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


@pytest.mark.parametrize('failure', [None, 'input_hash', 'clock_hash', 'gap', 'dtype', 'count', 'window', 'naive', 'validation'])
def test_reference_window_is_bound_to_input_receipt_and_dense_clock_bytes(tmp_path, failure):
    import numpy as np
    from datetime import datetime, timedelta, timezone
    day = '2026-08-03'
    target = tmp_path / day
    target.mkdir()
    start = datetime(2026, 8, 3, 8, tzinfo=timezone.utc)
    clocks = np.arange(int(start.timestamp()) + 1, int(start.timestamp()) + 5, dtype=np.int64)
    if failure == 'gap': clocks[2] += 1
    elif failure == 'dtype': clocks = clocks.astype(np.float64)
    np.save(target / 'clocks.npy', clocks, allow_pickle=False)
    stop = start + timedelta(seconds=4 if failure != 'window' else 5)
    metadata = dict(status='complete', ready_for_replay=True, validation_opened=failure == 'validation',
        clocks=4 if failure != 'count' else 5,
        identity=dict(top_n=10, window=30, session=dict(day=day,
            start=(start.replace(tzinfo=None) if failure == 'naive' else start).isoformat(), end=stop.isoformat())),
        files={'clocks.npy': sha256((target / 'clocks.npy').read_bytes()).hexdigest()})
    if failure == 'clock_hash': metadata['files']['clocks.npy'] = '0' * 64
    content = json.dumps(metadata).encode()
    (target / 'complete.json').write_bytes(content)
    checksum = '0' * 64 if failure == 'input_hash' else sha256(content).hexdigest()
    if failure:
        with pytest.raises(ValueError): _read_reference_clock_window(tmp_path, day, checksum)
    else:
        window = _read_reference_clock_window(tmp_path, day, checksum)
        assert window.requested_start == start and window.first_clock == start + timedelta(seconds=1)
        assert window.last_clock == stop and window.clocks == 4 and window.metric_intervals == 3


@pytest.mark.parametrize('failure', [None, 'missing', 'start', 'origin', 'end', 'samples', 'bool_count', 'interval', 'naive'])
def test_bound_reference_cannot_compare_a_different_or_shifted_app_clock_window(failure):
    from datetime import datetime, timedelta, timezone
    from dataclasses import replace
    start = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)
    end = start + timedelta(seconds=4)
    frozen = replace(reference(), clock_windows={'2026-08-01': ReferenceClockWindow(start,
        start + timedelta(seconds=1), end, 4, 'a' * 64, 'b' * 64)})
    actual = result()
    actual.update(execution_window=dict(start_at=start, end_at=end),
        measurement_window=dict(origin_at=start + timedelta(seconds=1), end_at=end, samples=3, interval_ms=1000))
    if failure == 'missing': actual.pop('measurement_window')
    elif failure == 'start': actual['execution_window']['start_at'] += timedelta(seconds=1)
    elif failure == 'origin': actual['measurement_window']['origin_at'] = start
    elif failure == 'end': actual['execution_window']['end_at'] += timedelta(seconds=1)
    elif failure == 'samples': actual['measurement_window']['samples'] = 4
    elif failure == 'bool_count': actual['measurement_window']['samples'] = True
    elif failure == 'interval': actual['measurement_window']['interval_ms'] = 100
    elif failure == 'naive': actual['execution_window']['start_at'] = start.replace(tzinfo=None).isoformat()
    if failure:
        with pytest.raises(ValueError): compare_sessions(frozen, {'2026-08-01': actual})
    else:
        assert compare_sessions(frozen, {'2026-08-01': actual})[0]['status'] == 'compared'


def test_native_decimal_money_is_compared_without_float_rounding():
    actual = result()
    amount = Decimal('100.000000000000000000000000000001')
    actual['metrics']['net_pnl'] = amount
    row = compare_sessions(reference(), {'2026-08-01': actual})[0]
    assert row['app']['metrics']['net_pnl'] is amount
    assert row['differences']['net_pnl'] == Decimal('1e-30')
    for invalid in (Decimal('NaN'), Decimal('Infinity')):
        actual['metrics']['net_pnl'] = invalid
        with pytest.raises(ValueError, match='Non-finite'):
            compare_sessions(reference(), {'2026-08-01': actual})


@pytest.mark.parametrize('field,value', [
    ('winner_sha256', 'another-winner'), ('full_session', False),
    ('full_session', 'true'), ('market_cursor_verified', 1),
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


@pytest.mark.parametrize('name,value', [
    ('net_pnl', True), ('closed_positions', True), ('closed_positions', -.1),
    ('closed_positions', 1.5), ('drawdown', -1), ('capital_dollar_seconds', -1),
    ('stop_risk_dollar_seconds', -1), ('inactivity_fraction', -.01),
    ('inactivity_fraction', 1.01), ('winning_positions', 3),
    ('unknown_metric', 0),
])
def test_metric_semantics_are_checked_for_app_and_reference(name, value):
    actual = result()
    actual['metrics'][name] = value
    with pytest.raises(ValueError):
        compare_sessions(reference(), {'2026-08-01': actual})
    expected = reference()
    expected.sessions['2026-08-01'][name] = value
    with pytest.raises(ValueError):
        compare_sessions(expected, {})


@pytest.mark.parametrize('count', [2, 2., Decimal('2.000')])
def test_integral_search_floats_and_native_decimal_counts_remain_comparable(count):
    actual = result()
    actual['metrics'].update(closed_positions=count, winning_positions=count)
    row = compare_sessions(reference(), {'2026-08-01': actual})[0]
    assert row['status'] == 'compared' and row['differences']['closed_positions'] == 0


@pytest.mark.parametrize('terminal', [False, 0, 1, 'true'])
def test_invalid_terminal_accounting_is_never_a_completed_comparison(terminal):
    actual = result()
    actual['metrics']['terminal_valid'] = terminal
    with pytest.raises(ValueError, match='terminal accounting|Non-finite'):
        compare_sessions(reference(), {'2026-08-01': actual})


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
            metrics={name: [value] for name, value in dict(net_pnl=100., drawdown=0.,
                capital_dollar_seconds=1000., stop_risk_dollar_seconds=20.,
                filled_batches=2., terminal_valid=True, inactivity_fraction=.5,
                add_count=0., reduce_count=0., worst_position_pnl=40.,
                closed_positions=2., winning_positions=2.).items()})).encode()
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


def test_hash_valid_freeze_cannot_silently_omit_an_optimizer_metric(monkeypatch, tmp_path):
    module, manifest = frozen_root(monkeypatch, tmp_path)
    name = '2026-08-01/receipt.json'
    receipt = json.loads((tmp_path / name).read_bytes())
    receipt['metrics'].pop('stop_risk_dollar_seconds')
    content = json.dumps(receipt).encode()
    (tmp_path / name).write_bytes(content)
    manifest['files_sha256'][name] = sha256(content).hexdigest()
    seal = json.loads((tmp_path / 'complete.json').read_bytes())
    seal['session_receipts']['2026-08-01'] = manifest['files_sha256'][name]
    content = json.dumps(seal).encode()
    (tmp_path / 'complete.json').write_bytes(content)
    manifest['files_sha256']['complete.json'] = sha256(content).hexdigest()
    (tmp_path / 'freeze.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Incomplete frozen metric contract'):
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
