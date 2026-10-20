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
