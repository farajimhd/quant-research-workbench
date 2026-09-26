from datetime import date
import math
import pytest

from research.rl_trading.v1.costs import OrderCosts, VERSION
from research.rl_trading.v1.data import SessionShard
from research.rl_trading.v1.phase3_search import SearchConfig,advance,initial_node,with_id
from research.rl_trading.v1.replay import replay_session
from src.market_engine.level_book_store import read,write
from test_rl_trading_train import _shard
from research.rl_trading.v1.common import digest,file_hash


def _row(time_us,price,can_open=True):
    return dict(time_us=time_us,ticker='A',side='long',can_open=can_open,
        open_value_available=can_open,entry_price=price,capital_per_share=price,
        open_value_per_dollar=.1,can_close=True,close_price=price,
        hold_value_available=True,hold_value_per_share=0.)


def test_ibkr_order_fee_and_budget_include_both_sides():
    costs = OrderCosts()
    quantity,buy_fee = costs.buy_for_budget(10.,2500.)
    assert quantity*10.+buy_fee <= 2500.+1e-8
    assert buy_fee >= .35
    assert costs.fee(quantity,10.,side='sell') > buy_fee
    assert costs.fee(10.,.20,side='buy') >= .02
    assert costs.fee(.5,10.,side='buy') >= .05


def test_phase3_teacher_charges_fees_on_forced_liquidation():
    config = SearchConfig(initial_cash=100.,allocation_step=50.,max_lots=1,
        max_orders_per_second=1,max_candidates=0,beam_width=0,
        order_cost_version=VERSION)
    first,_ = advance([with_id(initial_node(100.),1)],[_row(1,10.)],1,config)
    bought = next(node for node in first if node.lots)
    assert bought.actions[0]['fee'] > 0
    assert bought.cash >= 50.
    second,_ = advance([with_id(bought,2)],[_row(2,12.,False)],2,config,
        terminal=True)
    sold = second[0]
    assert sold.actions[0]['fee'] > 0
    assert sold.cash < 100.+bought.lots[0].quantity*2.
    assert sold.realized_pnl == pytest.approx(sold.cash-100.)


def test_replay_uses_identical_fees_for_terminal_sale(tmp_path):
    root = _shard(tmp_path/'session',date(2026,8,22))
    import numpy as np
    execution = root/'execution.npy'
    prices = np.load(execution,mmap_mode='r+')
    prices[0,0] = (10.,10.,10.)
    prices[0,1] = (12.,12.,12.)
    prices.flush()
    plan = read(root/'plan.json')
    plan['order_costs'] = OrderCosts().plan()
    plan['plan_hash'] = digest({k:v for k,v in plan.items() if k != 'plan_hash'})
    write(root/'plan.json',plan,immutable=False)
    complete = read(root/'complete.json')
    complete['plan_hash'] = plan['plan_hash']
    complete['files']['execution.npy'] = file_hash(execution)
    write(root/'complete.json',complete,immutable=False)
    shard = SessionShard(root)
    def buy(state):
        return lambda step,mask,previous: 1 if state['index'] == 0 else 0
    report = replay_session(shard,buy)
    q,fee = OrderCosts().buy_for_budget(10.,50.)
    expected = 100.-(q*10.+fee)+q*12.-OrderCosts().fee(q,12.,side='sell')
    assert math.isclose(report['terminal_cash'],expected,abs_tol=1e-8)
    assert report['fees_paid'] > 0
