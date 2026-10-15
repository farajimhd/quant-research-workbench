"""Detached V19 evidence enters the same native recorded comparison contract."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.trading_runtime.optimized_strategy_reference import METRICS, read_candidate_reference
from src.backend.recorded_program_comparison import verify_frozen_program_release
from src.trading_runtime.optimized_candidate import restore_candidate
from tests.test_optimized_candidate import envelope


def test_candidate_reference_all_sessions_and_complete_metrics(monkeypatch, tmp_path):
    candidate = envelope()
    metrics = {name: 0. for name in METRICS}
    metrics['terminal_valid'] = True
    evidence = dict(candidate_envelopes=[candidate], sessions={'12': {
        'training': [{'day': '2026-01-01', 'metrics': dict(metrics, episode_profits=[1, 2])}],
        'validation': [{'day': '2026-01-02', 'metrics': metrics}]}})
    monkeypatch.setattr('src.trading_runtime.optimized_candidate_evidence.read_frozen_candidate_evidence',
        lambda root, **kwargs: evidence)
    reference = read_candidate_reference(tmp_path, 12, concurrency=2)
    assert set(reference.sessions) == {'2026-01-01', '2026-01-02'}
    assert reference.winner_sha256 == candidate['program_hash']
    assert reference.candidate_envelope.serial == 12
    assert reference.clock_windows is None
    assert set(reference.sessions['2026-01-01']) == METRICS
    del metrics['drawdown']
    with pytest.raises(ValueError, match='Incomplete'):
        read_candidate_reference(tmp_path, 12)


def test_recorded_release_candidate_branch_requires_native_protected_identity(monkeypatch):
    candidate = restore_candidate(envelope())
    reference = SimpleNamespace(candidate_envelope=candidate, winner_sha256=candidate.program_hash,
        generation=candidate.generation, candidate=candidate.candidate,
        program=candidate.program, execution=candidate.execution)
    calls = []
    def verify(actual, release):
        calls.append((actual, release))
        if release != 'protected-native':
            raise ValueError('Imported release differs from its frozen optimized candidate')
    monkeypatch.setattr('src.backend.optimized_strategy_import.verify_imported_release', verify)
    verify_frozen_program_release(reference, 'protected-native')
    assert len(calls) == 1
    with pytest.raises(ValueError, match='Imported release differs'):
        verify_frozen_program_release(reference, 'unprotected-native')
    reference.generation += 1
    with pytest.raises(ValueError, match='identity differs'):
        verify_frozen_program_release(reference, 'protected-native')


def test_native_frozen_fixture_constructor_bridge(tmp_path):
    import json
    from tests.test_optimized_candidate_evidence import fixture
    from src.trading_runtime.optimized_candidate_evidence import freeze_candidate_evidence, _digest, _bytes_hash
    campaign = fixture(tmp_path)
    raw_candidate = envelope()
    programs = [raw_candidate['program'], raw_candidate['program']]
    token = _digest(programs)
    population_path = campaign / 'training/generation-0001/population.json'
    population_path.write_text(json.dumps({'population': programs, 'ids': [11, 12]}))
    metrics = {name: [0., 0.] for name in METRICS}
    metrics['terminal_valid'] = [True, True]
    for role, day in [('training', '2026-01-01'), ('validation', '2026-01-02')]:
        folder = campaign / role / 'generation-0001'
        receipt_path = folder / day / 'receipt.json'
        receipt = json.loads(receipt_path.read_text())
        receipt.update(population_sha256=token, execution=raw_candidate['execution'], metrics=metrics)
        receipt_path.write_text(json.dumps(receipt))
        seal_path = folder / 'complete.json'
        seal = json.loads(seal_path.read_text())
        seal.update(population_sha256=token, session_receipts={day: _bytes_hash(receipt_path.read_bytes())})
        seal_path.write_text(json.dumps(seal))
    frozen = tmp_path / 'native-frozen'
    freeze_candidate_evidence(campaign, 1, [12], frozen, runtime_root=tmp_path)
    reference = read_candidate_reference(frozen, 12, concurrency=2)
    assert reference.generation == 1
    assert reference.candidate == 2
    assert len(reference.sessions) == 2
    assert reference.candidate_envelope.execution['risk_protection'] is True
