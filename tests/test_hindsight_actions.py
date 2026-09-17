from datetime import datetime, timezone
from itertools import product

import pytest

from src.market_engine.hindsight_actions import ActionGrid, solve_actions


def grid(prices,spread=0.):
    return [dict(time=i,mark=p,volume_10s=10000,trades_10s=10,
                 quote=dict(at=i,bid=p-spread/2,ask=p+spread/2,bid_size=1000,ask_size=1000))
            for i,p in enumerate(prices)]


@pytest.mark.parametrize('prices',[[10,11,12,11,10],[10,9,8,9,10],[10]*5,[10,12,9,14,8]])
def test_dynamic_program_matches_exhaustive_paths(prices):
    rows=grid(prices,.02);cost=5.;risk=.2
    result=solve_actions(rows,cost_bps=cost,risk_bps_per_second=risk)
    best=float('-inf')
    for actions in product((-1,0,1),repeat=len(rows)):
        if actions[-1]!=0:continue
        previous=0;value=0.;valid=True
        for i,after in enumerate(actions):
            if abs(after-previous)>1:valid=False;break
            delta=after-previous;q=rows[i]['quote'];price=q['ask'] if delta>0 else q['bid']
            value-=delta*price+abs(delta)*price*cost/10000
            if i<len(rows)-1:value-=after**2*prices[i]*risk/10000
            previous=after
        if valid:best=max(best,value)
    assert result['objective']==pytest.approx(best)
    assert result['net_cash']-result['risk_penalty']==pytest.approx(best)
    assert result['path'][-1]['after']==0
    assert sum(m['net_cash'] for m in result['moves'])==pytest.approx(result['net_cash'])


def test_costs_flat_prices_and_missing_quotes_do_not_invent_profit():
    result=solve_actions(grid([10]*8))
    assert not result['moves'] and result['net_cash']==0
    rows=grid([10,20,30,40]);rows[1]['quote']=None;rows[2]['quote']=None
    r=solve_actions(rows,cost_bps=0,risk_bps_per_second=0)
    assert [x['action'] for x in r['path']]==['enter_long','hold','hold','exit_long']


def test_action_values_are_relative_to_hold_and_infeasible_are_null():
    r=solve_actions(grid([10,11,12]),cost_bps=0,risk_bps_per_second=0)
    assert r['values'][0][1][1]==0
    assert r['values'][0][1][2]==pytest.approx(1.)
    assert r['values'][0][1][0]<0
    assert r['values'][0][0][2] is None
    # At the terminal boundary holding nonzero inventory is impossible; values
    # are explicitly marked as forced unwind rather than an infinite benefit.
    assert r['path'][-1]['forced_terminal_unwind']


def test_unit_positions_preserve_entry_activity_gate_and_allow_exit():
    rows=grid([10,11,12,13]);rows[0]['trades_10s']=0
    r=solve_actions(rows,cost_bps=0,risk_bps_per_second=0)
    assert r['path'][0]['after']==0
    assert max(abs(x['after']) for x in r['path'])==1
    assert r['net_cash']==pytest.approx(2.)


def test_sampler_uses_only_asof_quotes_and_trailing_activity():
    s=ActionGrid(10,13)
    def row(t,kind,**kw):return dict(ts=datetime.fromtimestamp(t,timezone.utc).isoformat(),kind=kind,**kw)
    s.observe(row(9,'trade',price=10,size=50))
    s.observe(row(10,'quote',bid_price=10,ask_price=10.02,bid_size=100,ask_size=100))
    s.observe(row(11.5,'quote',bid_price=20,ask_price=20.02,bid_size=100,ask_size=100))
    s.observe(row(12.5,'quote',bid_price=0,ask_price=0,bid_size=0,ask_size=0))
    rows=s.finish()
    assert rows[0]['quote']['bid']==10 and rows[1]['quote']['bid']==10
    assert rows[2]['quote']['bid']==20 and rows[3]['quote'] is None
    assert rows[0]['volume_10s']==50
    with pytest.raises(ValueError,match='Unordered'):s.observe(row(5,'trade',price=1,size=1))


def test_window_contract_is_bounded_and_historical():
    from src.backend.hindsight_action_service import ActionRequest
    with pytest.raises(ValueError):ActionRequest(ticker='SUGP',session_date='2026-08-21',start_time='19:50',window_minutes=30)
    with pytest.raises(ValueError):ActionRequest(ticker='SUGP',session_date='2026-08-21',window_minutes=121)
    with pytest.raises(ValueError):ActionRequest(ticker='SUGP',session_date='2026-08-21',start_time='04:00:01')


def test_all_initial_state_action_values_match_exhaustive_continuations():
    rows=grid([10,11,9,12],.02)
    r=solve_actions(rows,cost_bps=5,risk_bps_per_second=.2)
    for state,before in enumerate((-1,0,1)):
        by_first={}
        for path in product((-1,0,1),repeat=4):
            if path[-1]!=0:continue
            q=before;value=0.;valid=True
            for i,target in enumerate(path):
                if abs(target-q)>1:valid=False;break
                delta=target-q;quote=rows[i]['quote'];p=quote['ask'] if delta>0 else quote['bid']
                value-=delta*p+abs(delta)*p*.0005
                if i<3:value-=target**2*rows[i]['mark']*.2/10000
                q=target
            if valid:by_first[path[0]]=max(by_first.get(path[0],float('-inf')),value)
        for j,target in enumerate((-1,0,1)):
            actual=r['values'][0][state][j]
            if target not in by_first:assert actual is None
            else:assert actual==pytest.approx(by_first[target]-by_first[before])


def test_sizing_is_fixed_to_one_and_requests_reject_sizing_controls():
    from src.backend.hindsight_action_service import ActionRequest
    result=solve_actions(grid([10,11,12,13,14]))
    assert result['inventory']==[-1,0,1] and result['position_size']==1
    assert all(abs(s['before'])<=1 and abs(s['after'])<=1 for s in result['path'])
    assert not any(s['action'].startswith(('add_','reduce_')) for s in result['path'])
    for key in ('lot_shares','inventory_steps','max_notional','participation'):
        with pytest.raises(ValueError):
            ActionRequest(ticker='SUGP',session_date='2026-08-21',**{key:1})


def test_action_runs_keep_the_price_at_each_action_change():
    r=solve_actions(grid([10,11,12,13,14]),cost_bps=0,risk_bps_per_second=0)
    runs=r['action_runs']
    assert [x['action'] for x in runs]==['enter_long','hold','exit_long']
    assert [(x['start_index'],x['end_index']) for x in runs]==[(0,0),(1,3),(4,4)]
    assert [x['price'] for x in runs]==[10,11,14]
    assert runs[1]['end_time']==4
    assert sum(x['end_index']-x['start_index']+1 for x in runs)==len(r['path'])
