from datetime import datetime, timezone
import pytest
from src.market_engine.hindsight_actions import ActionGrid, solve_actions


def grid(prices, spread=0.):
    return [dict(time=i,mark=p,volume_10s=10000,trades_10s=10,
                 quote=dict(at=i,bid=p-spread/2,ask=p+spread/2,bid_size=1000,ask_size=1000))
            for i,p in enumerate(prices)]


def test_user_example_profit_and_65_second_hold_excludes_later_reward():
    prices=[3.5]*181;prices[65]=4.34;prices[100]=8.
    r=solve_actions(grid(prices),decision_end=0)
    label=r['labels'][0]
    assert label['action']=='buy'
    assert label['profit']==pytest.approx(.84)
    assert label['hold_seconds']==65 and label['exit_time']==65
    assert r['position_size']==1 and r['max_hold_seconds']==90


def test_short_profit_and_earliest_equal_best_exit():
    prices=[5.]*91;prices[30]=4.;prices[60]=4.
    label=solve_actions(grid(prices),decision_end=0)['labels'][0]
    assert label['action']=='sell' and label['profit']==1
    assert label['hold_seconds']==30


def test_90_second_boundary_is_included_but_91_is_excluded():
    prices=[10.]*92;prices[90]=12.;prices[91]=20.
    label=solve_actions(grid(prices),decision_end=0)['labels'][0]
    assert label['profit']==2 and label['hold_seconds']==90


def test_spread_and_fees_are_explicit_and_gross_is_not_relative_advantage():
    prices=[10.]*91;prices[5]=11.
    label=solve_actions(grid(prices,.02),decision_end=0,cost_bps=5)['labels'][0]
    assert label['profit']==pytest.approx(.98)
    assert label['long']['net_profit']==pytest.approx(.98-(10.01+10.99)*.0005)
    assert label['long']['entry_price']==10.01
    assert label['long']['exit_price']==10.99


def test_flat_market_waits_and_missing_quotes_are_not_wait_labels():
    rows=grid([10.]*91,.02)
    assert solve_actions(rows,decision_end=0)['labels'][0]['action']=='wait'
    rows[0]['quote']=None
    label=solve_actions(rows,decision_end=0)['labels'][0]
    assert label['action']=='unavailable' and label['profit'] is None


def test_incomplete_horizon_is_explicit_and_missing_exits_are_excluded():
    rows=grid([10.]*91);rows[50]['quote']=None
    rows[51]['quote']['bid']=11;rows[51]['quote']['ask']=11
    r=solve_actions(rows)
    assert r['labels'][0]['hold_seconds']==51
    assert r['labels'][1]['reason']=='incomplete_90s_horizon'
    assert r['labels'][1]['profit'] is None
    for row in rows[1:]:row['quote']=None
    assert solve_actions(rows,decision_end=0)['labels'][0]['reason']=='no_eligible_exit_quote'


def test_overlapping_entries_are_independent_not_one_position_policy():
    rows=grid([10+i*.01 for i in range(95)])
    labels=solve_actions(rows,decision_end=4)['labels']
    assert all(x['action']=='buy' and x['hold_seconds']==90 for x in labels)
    assert all(x['profit']==pytest.approx(.9) for x in labels)


@pytest.mark.parametrize('seed',[2,7,19])
def test_matches_exhaustive_eligible_future_prices(seed):
    import random
    rng=random.Random(seed);rows=grid([10+rng.random() for _ in range(121)],.02)
    for i in (7,23,45,67):rows[i]['quote']=None
    result=solve_actions(rows,decision_end=30,max_spread_bps=1000)
    for i,label in enumerate(result['labels']):
        if not rows[i]['quote']:continue
        eligible=[(j,rows[j]['quote']) for j in range(i+1,i+91) if rows[j]['quote']]
        for side in ('long','short'):
            entry=rows[i]['quote']['ask' if side=='long' else 'bid']
            profits=[((q['bid']-entry if side=='long' else entry-q['ask']),j) for j,q in eligible]
            best=max(p for p,j in profits);first=next(j for p,j in profits if p==best)
            assert label[side]['gross_profit']==pytest.approx(best)
            assert label[side]['hold_seconds']==first-i


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




def test_removed_policy_and_sizing_options_are_rejected():
    from src.backend.hindsight_action_service import ActionRequest
    for key in ('lot_shares','inventory_steps','max_notional','participation','risk_bps_per_second'):
        with pytest.raises(ValueError):ActionRequest(ticker='SUGP',session_date='2026-08-21',**{key:1})
