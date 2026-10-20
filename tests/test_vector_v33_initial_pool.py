import numpy as np
from research.vectorized_backtest.v33.initial_pool import choose


def test_balanced_disjoint_repeatable_sampling():
    metrics=dict(net_pnl=np.arange(-100,100,dtype=float),drawdown=np.arange(200,dtype=float),closed_positions=np.ones(200),terminal_valid=np.ones(200,bool))
    a=choose(metrics,(22,11,11),np.random.default_rng(2020))
    assert a==choose(metrics,(22,11,11),np.random.default_rng(2020))
    indices,labels=a
    assert len(set(indices))==44
    assert labels.count('profitable')==22 and labels.count('promising_nonprofitable')==11 and labels.count('random')==11
    assert (metrics['net_pnl'][indices[:22]]>0).all()
    assert (metrics['net_pnl'][indices[22:33]]<=0).all()


def test_category_shortfalls_fill_from_remaining_including_inactive():
    metrics=dict(net_pnl=np.zeros(100),drawdown=np.zeros(100),closed_positions=np.zeros(100),terminal_valid=np.ones(100,bool))
    indices,labels=choose(metrics,(22,11,11),np.random.default_rng(1))
    assert len(set(indices))==44 and set(labels)=={'random'}


def test_quotas_total_exactly_1024():
    quotas=np.asarray([[n//24+int(i<n%24) for n in (512,256,256)] for i in range(24)])
    assert quotas.sum(0).tolist()==[512,256,256]
    assert quotas.sum()==1024


def test_global_engine_is_exact_v32_apart_from_version_names():
    import ast
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'research/vectorized_backtest'
    for name in ('evaluator.py','stream_sessions.py','session_lanes.py','global_pipeline.py','lineage.py','species.py','species_distance.py',
                 'objectives.py','network.py','network_cuda.py','network_pack.py','replay_graph.py','host_store.py','preparation.py',
                 'prefetch.py','io_pipeline.py','cpu_work.py','result_processing.py','publication.py','protected_transition.py'):
        old=(root/'v32'/name).read_text()
        new=(root/'v33'/name).read_text().replace('v33','v32').replace('V33','V32')
        assert ast.dump(ast.parse(old))==ast.dump(ast.parse(new)),name


def test_genotype_and_sampling_rng_match_v32_exactly():
    from research.vectorized_backtest.v32.genome import sample as old
    from research.vectorized_backtest.v33.genome import sample as new
    a=np.random.default_rng(2020);b=np.random.default_rng(2020)
    assert [p.payload() for p in old(a,12)]==[p.payload() for p in new(b,12)]
    assert a.bit_generator.state==b.bit_generator.state


def test_scoped_identity_keeps_original_v32_keys():
    from research.vectorized_backtest.v33.genome import sample
    from research.vectorized_backtest.v33.lineage import program_key
    from research.vectorized_backtest.v33.validation_cache import validation_scope
    members=sample(np.random.default_rng(20),12)
    expected=[program_key(m) for m in members]
    with validation_scope():assert [program_key(m) for m in members]==expected
