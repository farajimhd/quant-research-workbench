import copy
from dataclasses import asdict
import numpy as np
import pytest
from research.vectorized_backtest.v31 import genome,cpu_work


def test_sampling_and_payload_preserve_legacy_rng(monkeypatch):
    def legacy(rng,stage):
        if rng.random()<.15:return genome.MARKET_INPUTS+int(rng.integers(7 if stage=='entry' else 0,len(genome.STATES)))
        channel=int(rng.choice(3,p=(.45,.45,.10)))
        return channel*len(genome.CATALOG)+int(genome.feature_index(rng))
    for seed in (0,2020,991):
        a=np.random.default_rng(seed);b=np.random.default_rng(seed)
        current=genome.sample(a,12)
        with monkeypatch.context() as m:
            m.setattr(genome,'random_input',legacy)
            old=genome.sample(b,12)
        assert current==old and a.bit_generator.state==b.bit_generator.state
        for member in current:
            for net in member.networks.values():assert net.payload()['nodes']==[asdict(n) for n in net.nodes]


def test_parallel_species_matches_serial(monkeypatch):
    from research.vectorized_backtest.v31.species import Species
    population=genome.sample(np.random.default_rng(2020),64)
    metrics=dict(episode_entry_clocks=[[1,20]]*64,episode_exit_clocks=[[10,25]]*64,
                 episode_listings=[[0,1]]*64,closed_positions=[2]*64,capital_dollar_seconds=[1000]*64,turnover=[2000]*64)
    serial=Species();parallel=Species()
    monkeypatch.setenv('V31_REPRODUCTION_WORKERS','1')
    serial.assign(population,np.arange(64),np.arange(64),[metrics],1)
    monkeypatch.setenv('V31_REPRODUCTION_WORKERS','2')
    monkeypatch.setenv('V31_CPU_RESERVE_GIB','0');monkeypatch.setenv('V31_CPU_ADMISSION_GIB','0')
    try:
        parallel.assign(population,np.arange(64),np.arange(64),[metrics],1)
        assert serial.state==parallel.state
        assert serial.labels==parallel.labels and serial.descriptors==parallel.descriptors
    finally:cpu_work.close()


def test_worker_budget_rejected(monkeypatch):
    monkeypatch.setenv('V31_REPRODUCTION_WORKERS','9')
    with pytest.raises(ValueError,match='workers'):cpu_work.descriptors([])
