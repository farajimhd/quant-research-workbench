"""Executable clocks, episode identity, costs and uncapped teacher behavior."""
from datetime import date

import polars as pl
import pytest

from research.rl_trading.v1.common import bounds
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v1.build_phase2 import publish_market_values
from research.rl_trading.v1.market_values import MarketValues
from src.market_engine.level_book_store import write
from research.rl_trading.v1.phase1_close_labels import targets, decision_values
from research.rl_trading.v1.phase2_close_values import coefficients
from research.rl_trading.v1.phase3_dynamic_teacher import Config,run,run_stream,future_first_scores,session_profit_report
from research.rl_trading.v1.dynamic_supervision import order_labels
from research.rl_trading.v1.publish_dynamic_split import TRAIN,DEVELOPMENT,TEST


DAY=date(2026,8,18)


def test_dynamic_supervision_forward_split_is_strict():
    assert len(TRAIN)==17 and len(DEVELOPMENT)==2 and len(TEST)==1
    assert max(TRAIN)<min(DEVELOPMENT)<max(DEVELOPMENT)<min(TEST)
    assert len(set(TRAIN+DEVELOPMENT+TEST))==20


def test_close_episode_ignores_intrabar_high_and_uses_pre_cross_dip():
    left=bounds(DAY)[0]
    seconds=pl.DataFrame(dict(time_us=[left+i*1_000_000 for i in range(8)],
        resolution_ms=[1000]*8,price_valid=[1]*8,
        close=[2.5,2.0,2.2,2.3,2.35,2.3,2.2,2.1]))
    spikes=pl.DataFrame(dict(time_us=[left+3_500_000],resolution_ms=[100],
        price_valid=[1],close=[2.3]))
    bars=pl.concat((seconds,spikes)).with_columns(
        pl.when(pl.col('resolution_ms')==100).then(235.).otherwise(2.4).alias('high'))
    episodes=pl.DataFrame(dict(start_us=[left+2_000_000],end_us=[left+7_000_000],
        direction=[1],position_number=[42]))
    result=targets(bars,episodes,2)
    assert result['position_count']==1
    position=result['positions'][0]
    assert position['position_number']==1
    assert position['macd_interval_id']==42
    assert position['entry_price']==2.
    assert position['exit_price']==2.35
    assert position['exit_time']-position['entry_time']==pytest.approx(3.)


def test_setup_entry_late_gate_cost_and_episode_uid():
    left,right=bounds(DAY)
    bars=pl.DataFrame(dict(time_us=[left+i*1_000_000 for i in range(8)],
        resolution_ms=[1000]*8,price_valid=[1]*8,
        close=[3.,2.,2.,3.,4.,5.,5.,4.],volume=[30000.]*8,
        trade_count=[20]*8))
    episode=[dict(position_number=1,direction='long',entry_time=(left+1_000_000)/1e6,
        exit_time=(left+5_000_000)/1e6,entry_price=2.,exit_price=5.,
        macd_open=(left+2_000_000)/1e6,macd_close=(left+6_000_000)/1e6,
        label_available_at=(left+6_000_000)/1e6)]
    frame=decision_values(DAY,bars,episode,liquidation_us=right-120_000_000)
    frame=frame.with_columns(pl.lit('X').alias('ticker'),pl.lit('L').alias('listing_id'),
        pl.lit(30000.).alias('volume_60s'),pl.lit(20).alias('trades_60s'))
    result=coefficients(frame,.98,fee_per_share=.005)
    longs=result.filter((pl.col('side')=='long')&pl.col('time_us').is_between(left,left+4_000_000))
    assert longs['episode_stage'].to_list()==[
        'long_setup','long_entry','long_candidate','long_candidate','too_late_to_open']
    assert longs['can_open'].to_list()==[False,True,True,True,False]
    assert longs['episode_key'].to_list()==['X_1']*5
    assert longs['episode_uid'].n_unique()==1
    entry=longs.row(1,named=True)
    assert entry['open_value_per_dollar']==pytest.approx(
        (entry['open_value_per_share']-.01)/(entry['capital_per_share']+.005))


def test_dynamic_teacher_has_no_four_lot_cap_and_reconciles_fees():
    rows=[]
    for second in range(4):
        for index in range(6):
            rows.append(dict(time_us=second*1_000_000,ticker=f'T{index}',
                listing_id=f'L{index}',side='long',episode_uid=f'L{index}:1',
                target_us=2_000_000,close_price=10.+(1. if second>=2 else 0.),can_close=True,
                entry_price=10.,target_price=11.,can_open=second==0,
                open_value_per_share=.8 if second==0 else None,
                open_value_per_dollar=.08 if second==0 else None))
    trajectory,positions,report=run(pl.DataFrame(rows),
        Config(initial_cash=10_000.,window_seconds=0))
    assert report['max_open_lots']==6
    assert report['buys']==report['sells']==6
    assert report['net_profit']>0
    assert report['net_profit']==pytest.approx(positions['net_pnl'].sum())
    assert (positions['entry_fee']>=1.).all()
    assert (positions['exit_fee']>=1.).all()
    assert trajectory['open_lots'][-1]==0
    orders=order_labels(trajectory,positions,10_000.)
    buys=orders.filter(pl.col('action')=='buy')
    assert orders.height==12
    assert buys['allocation_weight'].sum()==pytest.approx(1.)
    assert buys['quantity'].min()>0


def test_future_episode_reserves_cash_in_bounded_window():
    rows=[]
    for second in range(5):
        for ticker,first,target,score in [('A',0,3,.02),('B',1,4,.08)]:
            rows.append(dict(time_us=second*1_000_000,ticker=ticker,
                listing_id=ticker,side='long',episode_uid=ticker+'_1',
                target_us=target*1_000_000,close_price=10.,can_close=True,entry_price=10.,
                target_price=11.,can_open=second==first,
                open_value_per_share=1. if second==first else None,
                open_value_per_dollar=score if second==first else None))
    trajectory,_,_=run(pl.DataFrame(rows),Config(window_seconds=2))
    assert trajectory['reserved_for_future'][0]>0
    assert trajectory['reserved_for_future'][1]==0


def test_v7_sparse_tensor_preserves_episode_join(tmp_path,monkeypatch):
    left,right=bounds(DAY)
    bars=pl.DataFrame(dict(time_us=[left+i*1_000_000 for i in range(8)],
        resolution_ms=[1000]*8,price_valid=[1]*8,
        close=[3.,2.,2.,3.,4.,5.,5.,4.],volume=[30000.]*8,
        trade_count=[20]*8))
    episode=[dict(position_number=1,direction='long',entry_time=(left+1_000_000)/1e6,
        exit_time=(left+5_000_000)/1e6,entry_price=2.,exit_price=5.,
        macd_open=(left+2_000_000)/1e6,macd_close=(left+6_000_000)/1e6,
        label_available_at=(left+6_000_000)/1e6)]
    frame=decision_values(DAY,bars,episode,liquidation_us=right-120_000_000)
    frame=frame.with_columns(pl.lit('X').alias('ticker'),pl.lit('L').alias('listing_id'),
        pl.lit(30000.).alias('volume_60s'),pl.lit(20).alias('trades_60s'))
    values=coefficients(frame,.98,fee_per_share=.005)
    listing=dict(ticker='X',listing_id='L')
    plan=dict(version='hindsight-greedy-close-episodes-v7',date=str(DAY),
        selected=[listing],discount_policy=dict(macd_resolution_seconds=1.),
        tensor_sort=dict(memory_gb=1,threads=2))
    plan['plan_hash']=digest(plan)
    folder=tmp_path/'listings'/digest(listing)[:20]
    folder.mkdir(parents=True)
    values.write_parquet(folder/'coefficients.parquet')
    write(folder/'ready.json',dict(plan_hash=plan['plan_hash'],rows=values.height,
        files={'coefficients.parquet':file_hash(folder/'coefficients.parquet')}))
    tensor=publish_market_values(tmp_path,plan)
    write(tmp_path/'plan.json',plan)
    files={item['file']:item['file_hash'] for item in (tensor['holding'],tensor['opening'])}
    write(tmp_path/'complete.json',dict(plan_hash=plan['plan_hash'],tensor=tensor,files=files))
    with MarketValues(tmp_path) as market:
        snapshot=market.at(left+1_000_000)
        opening=snapshot.filter((pl.col('side')=='long') & pl.col('can_open'))
        assert opening.height==1
        assert opening['episode_uid'][0].endswith(':L:1')
        assert opening['episode_key'][0]=='X_1'
    from research.rl_trading.v1 import build_phase3_dynamic
    monkeypatch.setattr(build_phase3_dynamic,'runtime_root',lambda:tmp_path)
    assert build_phase3_dynamic.main([
        '--phase2',str(tmp_path),'--end-second','7','--window-seconds','2'])==0
    with pytest.raises(ValueError,match='certified prior V7 population'):
        build_phase3_dynamic.main(['--phase2',str(tmp_path),'--end-second','57480'])
    prior_root=tmp_path/'prior-phase3'
    prior_root.mkdir()
    prior=dict(version='hindsight-phase3-long-grid-v3',date=str(DAY),
        phase2_root=str(tmp_path),phase2_plan_hash=plan['plan_hash'],
        phase2_plan_file_hash=file_hash(tmp_path/'plan.json'),
        phase2_complete_file_hash=file_hash(tmp_path/'complete.json'),
        v7_population=dict(contract='nonempty-prior-v7-at-0400-v1',
            included=['X'],excluded={}))
    prior['plan_hash']=digest(prior)
    write(prior_root/'plan.json',prior)
    write(prior_root/'complete.json',dict(plan_hash=prior['plan_hash']))
    assert build_phase3_dynamic.main(['--phase2',str(tmp_path),'--end-second','7',
        '--window-seconds','2','--v7-population-phase3',str(prior_root)])==0
    roots=list((tmp_path/'hindsight-phase3-dynamic'/str(DAY)).iterdir())
    assert len(roots)==2
    for root in roots:
        completed=__import__('json').loads((root/'complete.json').read_text())
        assert completed['trajectory_rows']==8
        assert completed['report']['sells']==completed['report']['buys']


def test_teacher_restart_reproduces_uninterrupted_result():
    rows=[]
    for second in range(65):
        rows.append(dict(time_us=second*1_000_000,ticker='X',listing_id='L',
            side='long',episode_uid='L:1',target_us=62_000_000,
            close_price=11. if second>=62 else 10.,can_close=True,entry_price=10.,
            target_price=11.,can_open=second==0,
            open_value_per_share=.8 if second==0 else None,
            open_value_per_dollar=.08 if second==0 else None))
    market=pl.DataFrame(rows)
    config=Config(window_seconds=0)
    complete=run(market,config)[2]
    saved=[]
    class Stopped(Exception):
        pass
    def stop(state):
        saved.append(state)
        raise Stopped
    times=market['time_us'].to_numpy()
    with pytest.raises(Stopped):
        run_stream(times,market.partition_by('time_us',maintain_order=True),
            future_first_scores(market,config),config,on_checkpoint=stop)
    assert saved[0]['processed']==60
    remaining=market.filter(pl.col('time_us')>=60_000_000)
    resumed=run_stream(times,remaining.partition_by('time_us',maintain_order=True),
        future_first_scores(market,config),config,resume=saved[0])[2]
    assert resumed==complete


def test_session_profit_marks_cross_boundary_hold_in_each_period():
    left=bounds(DAY)[0]
    offsets=[0,19_799,19_800,43_199,43_200,57_480]
    trajectory=pl.DataFrame(dict(time_us=[left+s*1_000_000 for s in offsets],
        equity=[100.,110.,108.,120.,115.,130.],
        bought=[1,0,0,0,0,0],sold=[0,0,0,0,0,1]))
    positions=pl.DataFrame(dict(entry_us=[left],exit_us=[left+57_480_000_000],
        entry_fee=[1.],exit_fee=[1.],net_pnl=[30.]))
    periods=session_profit_report(trajectory,positions,left,100.)
    assert [p['period'] for p in periods]==['premarket','regular','after_hours']
    assert [p['marked_net_profit'] for p in periods]==pytest.approx([10.,10.,10.])
    assert sum(p['marked_net_profit'] for p in periods)==pytest.approx(30.)
    assert [p['entry_fees'] for p in periods]==[1.,0.,0.]
    assert [p['exit_fees'] for p in periods]==[0.,0.,1.]
    assert [p['realized_net_pnl_on_exits'] for p in periods]==[0.,0.,30.]
    assert periods[1]['max_drawdown']>0
