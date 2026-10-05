"""Study budget and sealed financial-contract migration boundaries."""

import copy
import json
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from research.vectorized_backtest.v3.torch_backtest import prepared_cache
from research.vectorized_backtest.v3.torch_backtest.fixtures import synthetic_tape
from research.vectorized_backtest.v3.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v3.torch_backtest.population_study import (
    BUDGET, POPULATIONS, SEARCH_DATES, SEEDS,
)
from research.vectorized_backtest.v3.torch_backtest.runtime import write_json


def test_matched_budget_grid():
    assert POPULATIONS == (64, 128, 192, 256, 512)
    assert [BUDGET // b for b in POPULATIONS] == [24, 12, 8, 6, 3]
    assert all(BUDGET % b == 0 for b in POPULATIONS)
    assert len(SEEDS) == len(SEARCH_DATES) == 3


def test_import_allows_only_explicit_execution_contract_migration(tmp_path):
    grammar = StrategySpace().manifest()
    previous = copy.deepcopy(grammar)
    previous['version'] = 'old-search-version'
    for name in ('maximum_stop_risk_fraction', 'maximum_position_hold_seconds'):
        previous['fixed_settings'].pop(name)
    origin = tmp_path / 'old' / 'inputs' / 'training_000'
    identity = dict(code_hash='creator', build_id='fixture-build', session='fixture')
    tape = synthetic_tape(seconds=20)
    tape.provenance = dict(tape.provenance,
        preparation_algorithm=sha256(Path(prepared_cache.__file__).with_name('prepare.py').read_bytes()).hexdigest(),
        source_build='fixture-build')
    prepared_cache.save_prepared(origin, tape, identity)
    write_json(origin.parent.parent / 'identity.json', dict(code_hash='creator', grammar=previous))
    consumer = dict(identity, code_hash='new-consumer')
    with pytest.raises(ValueError, match='grammar'):
        prepared_cache.import_prepared(origin, tmp_path / 'strict', consumer, grammar)
    changed = copy.deepcopy(grammar)
    changed['fixed_settings']['participation'] = .5
    with pytest.raises(ValueError, match='grammar'):
        prepared_cache.import_prepared(origin, tmp_path / 'bad', consumer, changed,
                                      execution_contract_only=True)
    loaded = prepared_cache.import_prepared(origin, tmp_path / 'accepted', consumer, grammar,
                                           execution_contract_only=True)
    assert loaded.bytes == tape.bytes
    assert (origin / 'tape.pt').read_bytes() == (tmp_path / 'accepted' / 'tape.pt').read_bytes()
    assert prepared_cache.load_prepared(tmp_path / 'accepted', consumer).bytes == tape.bytes


def test_study_runs_all_population_budgets_and_freezes_before_transfer(tmp_path, monkeypatch):
    """Exercise orchestration cheaply; production grid is separately asserted."""
    from research.vectorized_backtest.v3.torch_backtest import population_study as study
    monkeypatch.setattr(study, 'POPULATIONS', (64, 128))
    monkeypatch.setattr(study, 'SEEDS', (42,))
    monkeypatch.setattr(study, 'BUDGET', 128)
    manifest = tmp_path / 'manifest.json'
    manifest.write_text('{}')
    spec = dict(training=[dict(day=day, manifest=str(manifest), ledger=str(manifest),
        start=day+'T04:00:00+00:00', end=day+'T09:30:00+00:00') for day in study.SEARCH_DATES],
        validation=[dict(day='NEVER_READ')])
    monkeypatch.setattr(study, 'certify_source', lambda _: SimpleNamespace(source=dict(build_id='fixture')))
    monkeypatch.setattr(study, 'load_prepared', lambda *_: SimpleNamespace(bytes=1))
    for name in ('empty_cache', 'reset_peak_memory_stats'):
        monkeypatch.setattr(study.torch.cuda, name, lambda: None)
    for name in ('max_memory_allocated', 'max_memory_reserved'):
        monkeypatch.setattr(study.torch.cuda, name, lambda: 0)

    def result(rows):
        n = len(rows)
        return dict(net_pnl=[10.]*n, drawdown=[0.]*n, positions_opened=[1]*n,
            exposure_seconds=[1.]*n, terminal_valid=[True]*n, filled_batches=[1]*n,
            open_positions=[0]*n, fill_count=[2]*n, stop_risk_dollar_seconds=[0.]*n,
            capital_dollar_seconds=[0.]*n, replay_seconds=.01, compile_seconds=0.)

    calls = []
    class Pool:
        def __init__(self, inputs, space, batch, **kw):
            self.batch = batch
            self.evaluators = {0: SimpleNamespace(runners={0: SimpleNamespace(ledger=torch.zeros(64, 1, 1))})}
        def evaluate(self, index, rows):
            calls.append((self.batch, index, len(rows)))
            return result(rows)
        def objectives(self):
            return [lambda rows, i=i: self.evaluate(i, rows) for i in range(3)]
        def close(self):
            pass
    monkeypatch.setattr(study, 'SessionPool', Pool)
    class Panel:
        def emit(self, value):
            pass
    args = SimpleNamespace(reuse_prepared='fixture-origin', runtime=tmp_path,
        maximum_stop_risk_fraction=.02, maximum_position_hold_seconds=3600,
        stop_risk_weight=.10, capital_time_weight=.002, maximum_tape_gib=12,
        maximum_host_gib=320, maximum_state_gib=8, maximum_fills=100, graph_steps=32)
    assert study.run(spec, args, tmp_path, Panel()) == 0
    for batch in (64, 128):
        folder = tmp_path / f'search_42_{batch}'
        receipts = list(folder.glob('generation_*.json'))
        assert len(receipts) == 128 // batch
        assert sum(len(json.loads(p.read_text())['population']) for p in receipts) == 128
    report = json.loads((tmp_path / 'study_report.json').read_text())
    assert report['validation_read'] is False
    assert len(report['scores']) == 2
    assert (tmp_path / 'frozen_finalists.json').exists()
    before = len(calls)
    assert study.run(spec, args, tmp_path, Panel()) == 0
    assert len(calls) == before  # Resume never replays completed work.
    # A new immutable short study imports B64 and timing without reevaluation,
    # but runs exactly four fresh B128 generations (512 evaluations).
    write_json(tmp_path / 'launch.json', dict(checkout=str(Path.cwd())))
    args.short_study_origin = tmp_path
    shortened = tmp_path / 'shortened'
    shortened.mkdir()
    assert study.run(spec, args, shortened, Panel()) == 0
    leader = json.loads((shortened / 'search_42_64/winner.json').read_text())
    assert leader['candidate_evaluations'] == 128
    assert not list((shortened / 'search_42_64').glob('generation_*.json'))
    assert len(list((shortened / 'search_42_128').glob('generation_*.json'))) == 4
    labels = json.loads((shortened / 'frozen_finalists.json').read_text())['labels']
    assert [v['candidate_evaluations'] for v in labels] == [128, 512]
