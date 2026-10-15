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


@pytest.mark.parametrize('authority', ['legacy', 'physical', 'legacy_physical', 'missing', 'extra', 'wrong_parent', 'changed_clock'])
def test_candidate_clock_physical_authorities_keep_exact_scope_and_containment(monkeypatch, tmp_path, authority):
    import json, os, subprocess
    import numpy as np
    from hashlib import sha256
    from datetime import datetime, timedelta, timezone
    candidate=envelope()
    metrics={name:0. for name in METRICS}; metrics['terminal_valid']=True
    selected={}; sources={}; roots={}
    for role,day in [('training','2026-01-01'),('validation','2026-01-02')]:
        physical=tmp_path/(role+'-physical'); target=physical/day; target.mkdir(parents=True)
        start=datetime.fromisoformat(day+'T08:00:00+00:00')
        np.save(target/'clocks.npy', np.arange(int(start.timestamp())+1,int(start.timestamp())+5,dtype=np.int64),allow_pickle=False)
        metadata=dict(status='complete',ready_for_replay=True,validation_opened=False,split=role,clocks=4,
            identity=dict(top_n=10,window=30,session=dict(day=day,start=start.isoformat(),end=(start+timedelta(seconds=4)).isoformat())),
            files={'clocks.npy':sha256((target/'clocks.npy').read_bytes()).hexdigest()})
        certificate=json.dumps(metadata).encode();(target/'complete.json').write_bytes(certificate)
        relative=f"{role}/generation-{candidate['generation']:04d}/{day}/receipt.json"
        receipt=json.dumps({'identity':{'input_sha256':sha256(certificate).hexdigest()}}).encode()
        receipt_path=tmp_path/relative;receipt_path.parent.mkdir(parents=True);receipt_path.write_bytes(receipt)
        sources[relative]=sha256(receipt).hexdigest();selected[role]=[{'day':day,'metrics':metrics}]
        alias=tmp_path/(role+'-alias');alias.mkdir()
        if os.name=='nt':
            subprocess.run(['cmd','/c','mklink','/J',str(alias/day),str(target)],check=True,capture_output=True)
        else: (alias/day).symlink_to(target,target_is_directory=True)
        roots[role]=(str(alias) if authority=='legacy' else str(physical) if authority=='legacy_physical' else {day:str(physical)})
    evidence=dict(candidate_envelopes=[candidate],sessions={'12':selected},sources=sources)
    monkeypatch.setattr('src.trading_runtime.optimized_candidate_evidence.read_frozen_candidate_evidence',lambda *a,**kw:evidence)
    if authority=='missing': roots['training']={}
    if authority=='extra': roots['training']['2026-01-03']=str(tmp_path)
    if authority=='wrong_parent': roots['training']['2026-01-01']=str(tmp_path/'training-alias')
    if authority=='changed_clock': (tmp_path/'training-physical/2026-01-01/clocks.npy').write_bytes(b'changed')
    if authority in ('physical','legacy_physical'):
        result=read_candidate_reference(tmp_path,12,concurrency=2,input_roots=roots)
        assert set(result.clock_windows)=={'2026-01-01','2026-01-02'}
    else:
        with pytest.raises(ValueError,match='exact frozen split days' if authority in ('missing','extra') else 'escaped|clocks changed'):
            read_candidate_reference(tmp_path,12,concurrency=2,input_roots=roots)
