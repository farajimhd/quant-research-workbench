import copy
import numpy as np
import torch
from research.vectorized_backtest.v32 import preparation
from research.vectorized_backtest.v32.genome import sample
from research.vectorized_backtest.v32.network import NetworkPlan
from research.vectorized_backtest.v31.network import NetworkPlan as Reference
from test_vector_v32_two_phase import setup


def test_spawned_packing_is_exact_original_arrays(monkeypatch):
    monkeypatch.setenv('V32_CPU_RESERVE_GIB','0');monkeypatch.setenv('V32_CPU_ADMISSION_GIB','0')
    members=sample(np.random.default_rng(2020),12)
    try:
        packed,_=preparation.submit(preparation._pack,members).result(timeout=120)
        a=Reference(members,'cpu');b=NetworkPlan(members,'cpu',packed=packed)
        for key in ('sources','weights','bias','counts','offsets','threshold','size_source','size_weight','size_bias','operations','edge_counts','logarithmic'):
            assert torch.equal(getattr(a,key),getattr(b,key)),key
        assert (a.total_nodes,a.kernel_nodes)==(b.total_nodes,b.kernel_nodes)
    finally:preparation.close()


def test_spawned_initialization_preserves_population_rng_and_report(setup,monkeypatch):
    from research.vectorized_backtest.v32.calibration import load_probe
    from research.vectorized_backtest.v32.initial_population import initialize
    monkeypatch.setenv('V32_CPU_RESERVE_GIB','0');monkeypatch.setenv('V32_CPU_ADMISSION_GIB','0')
    cfg=dict(setup.config,initialization_probe=str(setup.folder/(setup.days[0]+'.npz')))
    a=np.random.default_rng(2020);b=np.random.default_rng(2020)
    x,rx=initialize(a,10,probe=load_probe(cfg['initialization_probe'],cfg['training_manifest']))
    try:y,ry=preparation.initialize(b,10,cfg)
    finally:preparation.close()
    assert [m.payload() for m in x]==[m.payload() for m in y]
    assert a.bit_generator.state==b.bit_generator.state
    rx['calibration'].pop('seconds');ry['calibration'].pop('seconds')
    assert rx==ry


def test_preparation_memory_guard_precedes_submission(monkeypatch):
    import psutil,pytest
    from types import SimpleNamespace
    monkeypatch.setattr(psutil,'virtual_memory',lambda:SimpleNamespace(available=0))
    with pytest.raises(MemoryError,match='memory admission'):
        preparation.submit(preparation._pack,[])
