import json
import numpy as np
import pytest
from research.vectorized_backtest.v6.torch_backtest import capture_seed
from research.vectorized_backtest.v6.torch_backtest.evolution import sample
from research.vectorized_backtest.v6.torch_backtest.genome import StrategySpace
from research.vectorized_backtest.v6.torch_backtest.training_pass import population_hash


def test_initializer_survives_new_population_and_fails_closed_on_changed_bytes(tmp_path,monkeypatch):
    monkeypatch.setattr(capture_seed,'require_runtime',lambda p:p)
    first=sample(np.random.default_rng(1),StrategySpace(),2)
    second=sample(np.random.default_rng(2),StrategySpace(),2)
    key=(2,16,(15,(-1,)*5,(32,5,5,12,12),(False,False)))
    initialized=capture_seed.seed_population(tmp_path,key,first)
    assert population_hash(initialized)==population_hash(first)
    assert population_hash(capture_seed.seed_population(tmp_path,key,second))==population_hash(first)
    assert json.loads((tmp_path/'order.json').read_text())['families']==[capture_seed.family_token(key)]
    path=tmp_path/(capture_seed.family_token(key)+'.json');record=json.loads(path.read_text())
    record['population_sha256']='invalid';path.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='authoritative order'):
        capture_seed.seed_population(tmp_path,key,second)


def test_authoritative_order_reconstructs_missing_derived_seed(tmp_path,monkeypatch):
    monkeypatch.setattr(capture_seed,'require_runtime',lambda p:p)
    members=sample(np.random.default_rng(3),StrategySpace(),2)
    key=(2,16,'bounded-test')
    before=capture_seed.seed_population(tmp_path,key,members)
    path=tmp_path/(capture_seed.family_token(key)+'.json');bytes_before=path.read_bytes();path.unlink()
    assert population_hash(capture_seed.seed_population(tmp_path,key,members))==population_hash(before)
    assert path.read_bytes()==bytes_before


def test_context_restoration_uses_recorded_order_without_financial_replay(tmp_path,monkeypatch):
    from research.vectorized_backtest.v6.torch_backtest import resident_evaluator
    from research.vectorized_backtest.v6.torch_backtest.resident_evaluator import ResidentSessionEvaluator
    from research.vectorized_backtest.v6.torch_backtest.compact_runner import CompactProgramRunner
    def mkdir(p):p.mkdir(parents=True,exist_ok=True);return p
    monkeypatch.setattr(capture_seed,'require_runtime',mkdir)
    monkeypatch.setattr(resident_evaluator,'require_runtime',mkdir)
    members=sample(np.random.default_rng(4),StrategySpace(),2)
    seeds=mkdir(tmp_path/'seeds')
    key=(2,16,CompactProgramRunner.specialization_key(members,StrategySpace()))
    capture_seed.seed_population(seeds,key,members)
    evaluate=ResidentSessionEvaluator.__new__(ResidentSessionEvaluator)
    evaluate.capture_seed_root=seeds;evaluate._resident_runners={}
    evaluate.compiler_specialization_budget=128;evaluate.graph_steps=16
    calls=[]
    evaluate.prepare_pass=lambda training,population,output,**kwargs:calls.append((population_hash(population),kwargs))
    evaluate.restore_capture_context([dict(day='a'),dict(day='b')],tmp_path/'result',workers=2)
    assert calls==[(population_hash(members),dict(workers=2,prime_only=True))]
    record=json.loads((tmp_path/'result/compiler-priming/complete.json').read_text())
    assert record['full_session'] is False and record['selection_allowed'] is False
