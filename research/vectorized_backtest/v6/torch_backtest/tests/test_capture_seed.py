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
    path=next(tmp_path.glob('*.json'));record=json.loads(path.read_text())
    record['population_sha256']='invalid';path.write_text(json.dumps(record))
    with pytest.raises(ValueError,match='population changed'):
        capture_seed.seed_population(tmp_path,key,second)
