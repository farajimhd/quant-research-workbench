from datetime import date
from pathlib import Path
import math
import numpy as np
import pytest
from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.session_data import PackedSession
from research.rl_trading.v6.training import TeacherDecision
from research.rl_trading.v6.prepare_training import coverage, choose_rank
from research.rl_trading.v6.teacher_trajectory import Intent, compile_trajectory
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.features import SCALAR_NAMES


def fixture():
    clocks=np.array([1_000_000,2_000_000,3_000_000,4_000_000]*2,dtype=np.int64)
    scalar=np.zeros((8,37),dtype=np.float32)
    scalar[:,3]=math.log(10.)
    scalar[:,35]=1
    scalar[:4,8]=math.log1p(100.)
    scalar[4:,8]=math.log1p(1.)
    bank=SessionBank(Path('unused'),{'offsets':{'A':[0,4],'B':[4,8]}},clocks,scalar,np.zeros((8,2,5,11),dtype=np.float32))
    return PackedSession(date(2026,7,31),'train',Path('unused'),'hash',bank,None,('A','B'))


def test_rank_coverage_and_rank_choice_are_per_day_not_pooled():
    session=fixture()
    def decision(token):
        return TeacherDecision(1_000_000,token-1,token,np.array([10000.,10000.,0.,0.,0.,0.,0.]),
            np.empty(0,dtype=np.int64),np.empty((0,9)),np.ones(2,dtype=bool),
            np.empty(0,dtype=bool),np.empty(0,dtype=bool),np.empty(0,dtype=bool),.2)
    report=coverage(session,(decision(1),decision(2)),[1,2],1)
    assert report['coverage']['1']['ignored']==1
    assert choose_rank([report],[1,2],.99)==2
    with pytest.raises(ValueError,match='No candidate'):
        choose_rank([report],[1],.99)


def test_rank_exclusion_rebuilds_holdings_cash_and_later_actions():
    session=fixture()
    intents=tuple(Intent(t,i,t+':1',1_000_000,3_000_000,10.,11.,1000.,0.,.1,9.,11.1,3_000_000)
                  for i,t in enumerate(('A','B')))
    decisions,outcomes,report=compile_trajectory(session,intents,ranking_config=MarketAttentionConfig(top_r=1))
    assert report['ignored_outside_rank_entries']==1
    assert report['completed_positions']==1
    assert all(1 not in d.held_index for d in decisions)
    assert all(o.listing_index==0 for o in outcomes)
    assert report['pending_entries']==report['unresolved_positions']==0
    assert report['ledger'][0]['ticker']=='A'
