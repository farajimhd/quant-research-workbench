from dataclasses import replace
from datetime import date
import itertools
import math
import random

import polars as pl
import pytest

from src.market_engine.hindsight_oracle import Episode, solve, benchmark, validate
from src.market_engine.hindsight_phase1 import bounds, digest
from src.market_engine.level_book_store import read, write


def episode(key, start, end, entry=10., exit=11., side='long', ticker='A'):
    return Episode(key, ticker, 'listing:'+ticker, side, start, end, entry, exit, end+1)


def exhaustive(trades, cash, cost):
    # Independent exhaustive subset enumeration, not the production recurrence.
    result = cash
    for bits in itertools.product((False, True), repeat=len(trades)):
        selected = sorted((e for e, include in zip(trades, bits) if include), key=lambda e:e.entry_us)
        if any(a.exit_us > b.entry_us for a,b in zip(selected, selected[1:])):
            continue
        equity = cash
        for e in selected:
            shares = equity/(e.entry_price+cost)
            if e.side == 'long':
                equity = shares*(e.exit_price-cost)
            else:
                equity += shares*(e.entry_price-e.exit_price-2*cost)
            if equity <= 0:
                break
        result = max(result, equity)
    return result


@pytest.mark.parametrize('seed', range(20))
def test_exact_matches_independent_exhaustive_search(seed):
    rng = random.Random(seed)
    trades = []
    for i in range(10):
        start = rng.randrange(12)
        trades.append(episode(str(i),start,start+rng.randrange(1,6),exit=rng.uniform(6,15),
                              side=rng.choice(['long','short']),ticker=str(i%3)))
    result = solve(trades, initial_cash=10_000., cost_per_share=.1)
    assert result['terminal_equity'] == pytest.approx(exhaustive(trades,10_000.,.1))
    assert solve(list(reversed(trades)),cost_per_share=.1)['ledger'] == result['ledger']


def test_waiting_switching_and_same_timestamp_capital_reuse():
    trades = [episode('greedy',0,10,exit=15), episode('later',1,3,exit=13,ticker='B'),
              episode('reuse',3,5,entry=10,exit=7,side='short')]
    result = solve(trades)
    assert [r['episode_key'] for r in result['ledger']] == ['later','reuse']
    assert result['terminal_equity'] == pytest.approx(16900)
    assert result['ledger'][1]['cash_before'] == pytest.approx(13000)
    labels = {r['episode_key']:r for r in result['action_labels']}
    assert labels['greedy']['log_regret'] == pytest.approx(math.log(1.69/1.5))
    assert solve(trades,mode='long')['terminal_equity'] == 15000


@pytest.mark.parametrize('seed', range(5))
def test_matches_continuous_multi_position_cash_flow_linear_program(seed):
    from scipy.optimize import linprog
    rng = random.Random(seed)
    trades = [episode(str(i), i//2, i//2+rng.randrange(1,5), exit=rng.uniform(7,14),
                      side=rng.choice(['long','short'])) for i in range(12)]
    # Each variable is independently allocated entry cash. The LP allows all
    # trades to overlap, arbitrary splitting and cash retention, unlike paths.
    gains = [(e.exit_price/e.entry_price-1) if e.side == 'long' else (1-e.exit_price/e.entry_price) for e in trades]
    times = sorted({t for e in trades for t in (e.entry_us,e.exit_us)})
    constraints = [[float(e.entry_us <= t)-(1+g)*float(e.exit_us <= t)
                    for e,g in zip(trades,gains)] for t in times]
    lp = linprog([-g for g in gains],A_ub=constraints,b_ub=[10000]*len(times),bounds=(0,None),method='highs')
    assert lp.success
    assert solve(trades)['terminal_equity'] == pytest.approx(10000-lp.fun)


def test_benchmark_reuses_cash_and_aggregates_simultaneous_events():
    trades = [episode('a',1,2,exit=12),episode('b',2,3,exit=11),episode('c',1,3,exit=9)]
    result = benchmark(trades)
    assert result['total_entry_capital'] == 3000
    assert result['total_profit'] == pytest.approx(200)
    assert result['required_initial_cash'] == 2000
    assert result['scaled_profit'] == pytest.approx(1000)
    assert result['profit_to_required_cash'] == pytest.approx(.1)


def test_short_costs_losses_insolvency_and_wait():
    trade = episode('short',1,2,exit=8,side='short')
    result = solve([trade],cost_per_share=.1)
    assert result['net_profit'] == pytest.approx(10000/10.1*1.8)
    assert result['total_cost'] == pytest.approx(10000/10.1*.2)
    loss = episode('bad',1,2,exit=25,side='short')
    assert solve([loss])['ledger'] == []
    assert not solve([loss])['action_labels'][0]['solvent_at_exit']
    # Funding includes eventual negative settlement as well as entry reserve.
    b = benchmark([loss])
    assert b['required_initial_cash'] == 1500
    assert b['total_profit'] == -1500
    assert solve([])['terminal_equity'] == 10000
    assert benchmark([])['profit_to_required_cash'] is None


def test_ties_duplicates_invalid_bounds_and_cancellation():
    e = episode('a',1,2)
    assert solve([e,replace(e,key='b')])['selected_episodes'] == 1
    with pytest.raises(ValueError,match='Duplicate'): validate([e,e])
    with pytest.raises(ValueError,match='ordering'): validate([replace(e,exit_us=1)])
    with pytest.raises(ValueError): solve([e],cost_per_share=float('nan'))
    def stop(): raise InterruptedError('test stop')
    with pytest.raises(InterruptedError): solve([e],check=stop)


def make_source(tmp_path):
    from scripts.build_hindsight_greedy import file_hash
    root = tmp_path/'source'
    listing = dict(ticker='A',listing_id='listing:A')
    left,right = bounds(date(2026,8,21))
    plan = dict(version='hindsight-phase1-arte-price-action-v3',valuation_basis='price_action',
        date='2026-08-21',scope='explicit_canary',selected=[listing],liquidation_us=right-120_000_000)
    plan['plan_hash'] = digest(plan)
    write(root/'plan.json',plan)
    write(root/'complete.json',dict(plan_hash=plan['plan_hash'],listing_count=1,rows=57601))
    folder = root/'listings'/digest(listing)[:20]
    folder.mkdir(parents=True)
    pl.DataFrame({'time_us':range(left,right+1,1_000_000)}).write_parquet(folder/'opportunities.parquet')
    write(folder/'targets.json',dict(position_count=1,positions=[dict(position_number=1,direction='long',
        entry_time=(left+1_000_000)/1e6,exit_time=(left+2_000_000)/1e6,entry_price=10.,exit_price=11.,
        label_available_at=(left+3_000_000)/1e6)]))
    write(folder/'ready.json',dict(plan_hash=plan['plan_hash'],listing=listing,rows=57601,
        files={name:file_hash(folder/name) for name in ('targets.json','opportunities.parquet')}))
    return root,folder


def test_runnable_resume_stop_and_corruption(tmp_path,monkeypatch):
    from scripts.build_hindsight_oracle import main
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    source,folder = make_source(tmp_path)
    args = ['--phase1',str(source)]
    assert main(args) == 0
    root = next((tmp_path/'hindsight-oracle'/'2026-08-21').iterdir())
    summary = read(root/'summary.json')
    assert len(summary['results']) == 6
    assert summary['results'][-1]['oracle']['terminal_equity'] == 11000
    assert main(args) == 0
    assert read(root/'progress.json')['counts']['reused'] == 6
    (root/'STOP').touch()
    assert main(args) == 2 and not (root/'complete.json').exists()
    (root/'STOP').unlink()
    assert main(args) == 0
    with (folder/'targets.json').open('ab') as stream: stream.write(b' ')
    assert main(args) == 2 and not (root/'complete.json').exists()


def test_episode_limit_fails_without_truncation(tmp_path,monkeypatch):
    from scripts.build_hindsight_oracle import main
    from scripts.build_hindsight_greedy import file_hash
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    source,folder = make_source(tmp_path)
    assert main(['--phase1',str(source),'--max-episodes','0']) == 2
    target = read(folder/'targets.json')
    target['positions'].append(dict(target['positions'][0],position_number=2))
    target['position_count'] = 2
    write(folder/'targets.json',target,immutable=False)
    ready = read(folder/'ready.json')
    ready['files']['targets.json'] = file_hash(folder/'targets.json')
    write(folder/'ready.json',ready,immutable=False)
    assert main(['--phase1',str(source),'--max-episodes','1']) == 2
    assert not list((tmp_path/'hindsight-oracle').rglob('complete.json'))


def test_compact_plain_console_and_persisted_failure(tmp_path,monkeypatch):
    from argparse import Namespace
    from io import StringIO
    from rich.console import Console
    from scripts.build_hindsight_oracle import run
    monkeypatch.setenv('QW_RUNTIME_ROOT',str(tmp_path))
    source,folder = make_source(tmp_path)
    output = StringIO()
    args = Namespace(phase1=source,initial_cash=10000.,allocation=1000.,cost_per_share=0.,max_episodes=100)
    assert run(args,Console(file=output,width=50,color_system=None)) == 0
    rendered = output.getvalue()
    assert 'Complete' in rendered and 'terminal equity' in rendered and '\x1b[' not in rendered
    with (folder/'targets.json').open('ab') as stream: stream.write(b' ')
    with pytest.raises(ValueError,match='integrity'):
        run(args,Console(file=output,width=50,color_system=None))
    root = next((tmp_path/'hindsight-oracle'/'2026-08-21').iterdir())
    assert 'integrity' in read(root/'failure.json')['error']
