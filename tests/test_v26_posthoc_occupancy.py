"""CPU-only exactness and fail-closed deferred-analysis contracts."""
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import psutil
import pytest

from research.vectorized_backtest.posthoc.v26 import contracts as c
from research.vectorized_backtest.posthoc.v26.occupancy import intervals, intersection_matrix, metrics
from research.vectorized_backtest.posthoc.v26 import run
from research.vectorized_backtest.posthoc.v26.validation_trace import exact_parity


def test_trace_parity_all_saved_fields():
    import copy
    saved=dict(identity={'data':'certified'},execution={'fee':1},population_sha256='token',
        listing_ids=[1],full_session=True,split='validation',validation_opened=True,
        metrics={'total_pnl':[1.5], 'episode_profits':[[1.5]], 'result_reused':[True]})
    actual=copy.deepcopy(saved)
    actual['metrics'].update(episode_entry_clocks=[[1]],episode_exit_clocks=[[2]],result_reused=[False])
    exact_parity(actual,saved)
    actual['metrics']['total_pnl']=[1.50000000001]
    with pytest.raises(ValueError,match='parity'):exact_parity(actual,saved)
    actual=copy.deepcopy(saved)
    with pytest.raises(ValueError,match='clocks'):exact_parity(actual,saved)


def test_real_cpu_replay_trace_preserves_financial_fields():
    import torch
    from dataclasses import replace
    from research.vectorized_backtest.v26.genome import sample
    from research.vectorized_backtest.v26.composition import routed
    from research.vectorized_backtest.v26.evaluator import replay_cohort
    from research.vectorized_backtest.v7.qualify import synthetic
    a,b=sample(np.random.default_rng(12),2)
    a=replace(a,policy=replace(a.policy,swing_left=0,swing_right=0,stop_fraction=.1,target_fraction=.9,cooldown=1,maximum_adds=0))
    member=routed('priority',a,b)
    gates=torch.tensor([1,0,0,0,0,0],dtype=torch.int32)[None,:,None]
    gates=gates|(gates<<5)
    item=synthetic('cpu')
    plain=replay_cohort([item],[member],[gates],trace=False)[0]
    traced=replay_cohort([item],[member],[gates],trace=True)[0]
    for key,value in plain.items():
        if isinstance(value,torch.Tensor):assert torch.equal(value,traced[key]),key
        else:assert value==traced[key],key
    assert traced['episode_entry_clocks']==[[1]]


def test_grid_parity_random_and_chunked():
    rng = np.random.default_rng(42)
    patterns = []
    grids = []
    for _ in range(40):
        grid = rng.integers(0, 2, (3, 80), dtype=np.int64)
        lanes, starts, ends = [], [], []
        for lane, row in enumerate(grid):
            edges = np.diff(np.r_[0, row, 0])
            a, b = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
            lanes.extend([lane] * len(a)); starts.extend(a); ends.extend(b)
        patterns.append(intervals(lanes, starts, ends, listing_count=3))
        grids.append(grid.ravel())
    patterns += [patterns[0], intervals([], [], [], listing_count=3)]
    grids += [grids[0], np.zeros(240, dtype=np.int64)]
    grid = np.stack(grids)
    shared, duration, detail = intersection_matrix(patterns, scratch_mib=.01)
    np.testing.assert_array_equal(shared, grid @ grid.T)
    np.testing.assert_array_equal(duration, grid.sum(1))
    assert detail['unique_patterns'] == 41
    scores = metrics(shared, duration)
    assert scores['jaccard'][0, 40] == scores['cosine'][0, 40] == 1
    assert np.isnan(scores['jaccard'][-1]).all()


def test_subsets_namespaces_touching_and_zero_duration():
    a = intervals([0, 0, 1], [0, 5, 0], [5, 10, 0], listing_count=2)
    np.testing.assert_array_equal(a[0], [0, 86400])
    np.testing.assert_array_equal(a[1], [10, 86401])
    b = intervals([0], [0], [5], listing_count=2)
    d = intervals([1], [5], [10], listing_count=2)
    shared, duration, _ = intersection_matrix([a, b, d])
    scores = metrics(shared, duration)
    assert shared[0, 1] == 5 and shared[0, 2] == 0
    assert scores['coverage'][1, 0] == 1
    assert scores['jaccard'][0, 1] == 5 / 11
    empty, lengths, _ = intersection_matrix([])
    assert empty.shape == (0, 0) and not len(lengths)


@pytest.mark.parametrize('args', [([0], [1.5], [2]), ([0], [3], [2]),
    ([2], [0], [1]), ([0, 0], [0, 1], [2, 3]), ([0], [0], [86400]),
    ([0], [float('nan')], [2]), ([0, 0], [0], [1])])
def test_bad_ledger_fails(args):
    with pytest.raises(ValueError): intervals(*args, listing_count=2)


@pytest.fixture
def campaign(tmp_path):
    root = tmp_path / 'campaign'; root.mkdir()
    pointer = tmp_path / 'active.json'
    owner = dict(pid=99999999, creation_time=0, campaign=str(root), source_sha256='source', code='unused')
    c.write(pointer, owner)
    contract = dict(version='v26-global-search-v1', generations=50, validation_feedback=False,
        source_sha256='source', session_workers=2)
    payload = [{'test': 1}, {'test': 2}, {'test': 3}]
    state = dict(completed=50, population=payload, ids=[100, 101, 102],
        leaders=[dict(serial=100+i, rank=i+1, metrics=dict(total_pnl=10-i)) for i in range(3)],
        validation_ranking=[dict(candidate=2, serial=101, metrics=dict(total_pnl=4)),
                            dict(candidate=1, serial=100, metrics=dict(total_pnl=3))])
    for role, count in [('training', 24), ('validation', 12)]:
        days = [f'day-{i:02}' for i in range(count)]
        contract[role] = dict(days=days)
        folder = root / 'global' / role / 'generation-0050'; folder.mkdir(parents=True)
        hashes = {}
        for day in days:
            selected = 3 if role == 'training' else 2
            record = dict(listing_ids=[0], metrics=dict(episode_listings=[[0]]*selected,
                episode_entry_clocks=[[0], [5], [15]][:selected], episode_exit_clocks=[[10], [15], [20]][:selected]))
            c.write(folder/day/'receipt.json', record); hashes[day] = c.file_hash(folder/day/'receipt.json')
        (folder/'session-panel.npz').write_bytes(b'panel')
        c.write(folder/'complete.json', dict(session_receipts=hashes, validation_feedback=False,
            display_only=role=='validation', population_sha256=c.digest(payload if role=='training' else payload[:2]),
            session_panel_sha256=c.file_hash(folder/'session-panel.npz')))
        if role == 'training': c.write(folder/'population.json', dict(population=payload, ids=state['ids']))
    c.write(root/'global/checkpoint.json', dict(contract=contract, state=state, state_sha256=c.digest(state)))
    c.write(root/'exit.json', dict(exit_code=0))
    return pointer, root


def test_completion_and_tamper(campaign):
    pointer, root = campaign
    assert c.completed(pointer)['saved']['state']['completed'] == 50
    path = root/'global/training/generation-0050/day-00/receipt.json'
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='receipt'): c.completed(pointer)


@pytest.mark.parametrize('condition', ['active', 'lock', 'failed', 'incomplete', 'state', 'panel'])
def test_gate_conditions(campaign, condition):
    pointer, root = campaign
    if condition == 'active':
        owner=c.read(pointer); owner.update(pid=os.getpid(), creation_time=psutil.Process().create_time());c.write(pointer,owner)
        (root/'global/checkpoint.json').write_text('must never be read')
    elif condition == 'lock': (root/'owner.lock').write_text('owned')
    elif condition == 'failed': c.write(root/'exit.json', dict(exit_code=1))
    elif condition in ('incomplete', 'state'):
        saved=c.read(root/'global/checkpoint.json');saved['state']['completed']=49 if condition=='incomplete' else 51
        if condition=='state': saved['state']['completed']=50;saved['state']['ids']=[1]
        c.write(root/'global/checkpoint.json', saved)
    else: (root/'global/validation/generation-0050/session-panel.npz').write_bytes(b'tamper')
    if condition in ('state', 'panel'):
        with pytest.raises(ValueError): c.completed(pointer)
    else: assert c.completed(pointer) is None


def test_owner_lock(tmp_path):
    with c.analysis_owner(tmp_path):
        with pytest.raises(InterruptedError):
            with c.analysis_owner(tmp_path): pass
    assert not (tmp_path/'analysis.lock').exists()
    c.write(tmp_path/'analysis.lock', dict(pid=99999999, creation_time=0))
    with c.analysis_owner(tmp_path): pass
    assert len(list(tmp_path.glob('stale-lock-*'))) == 1


def test_end_to_end_and_resume(campaign, tmp_path):
    pointer, root = campaign
    output=tmp_path/'analysis';proof=c.completed(pointer)
    with patch.object(run, 'memory_admission'):
        run.analyze(proof,pointer,output,Path('unused'),Path('unused'),Path('unused'))
        with patch('research.vectorized_backtest.posthoc.v26.occupancy.intersection_matrix', side_effect=AssertionError('recomputed')):
            run.analyze(proof,pointer,output,Path('unused'),Path('unused'),Path('unused'))
    result=c.read(output/'result.json')
    assert [r['serial'] for r in result['candidates']] == [100,101]
    assert result['splits']['training']['summary']['most_similar'][0]['jaccard'] == 1/3
    with np.load(output/'training.npz') as data:
        np.testing.assert_array_equal(data['intersection_seconds'], [[240,120],[120,240]])
    assert (output/'complete.json').exists() and 'Validation' in (output/'report.html').read_text()
    (output/'training.npz').write_bytes(b'tamper')
    with patch.object(run, 'memory_admission'), pytest.raises(ValueError,match='resume'):
        run.analyze(proof,pointer,output,Path('unused'),Path('unused'),Path('unused'))


def test_waiting_does_not_import_engine_or_numpy(tmp_path):
    import subprocess
    output=Path('D:/TradingML/runtimes/vectorized_backtest/torch_backtest_v26/diagnostics/occupancy-tests') / tmp_path.name
    script = "from research.vectorized_backtest.posthoc.v26.run import main; import sys; main(['--pointer',sys.argv[1],'--output',sys.argv[2],'--check-only']); assert 'numpy' not in sys.modules; assert 'torch' not in sys.modules"
    subprocess.run([sys.executable,'-B','-c',script,str(tmp_path/'missing.json'),str(output)],check=True,
        env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1'))
