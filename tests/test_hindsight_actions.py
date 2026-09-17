from datetime import datetime, timezone
import pytest
from src.market_engine.hindsight_actions import ActionGrid, solve_actions


def quote(price, at=0):
    return dict(at=at,bid=price,ask=price,bid_size=100,ask_size=100)


def target(t,side='short'):
    return dict(direction=side,exit_time=t,entry_time=t-3,entry_price=5,exit_price=4,
                macd_open=t-2,macd_close=t+5,label_available_at=t+5,position_number=1)


def rows(n=1):
    return [dict(time=i,quote=quote(5,i),trades_10s=10) for i in range(n)]


def test_first_macd_target_not_later_more_profitable_move():
    label=solve_actions(rows(),targets=[target(4.067),target(60)],
        target_quotes={4.067:quote(4.8),60:quote(3)})['labels'][0]
    assert label['short']['exit_time']==4.067
    assert label['short']['hold_seconds']==4.067
    assert label['short']['gross_profit']==pytest.approx(.2)
    assert label['available_at']==9.067


def test_target_after_90_seconds_is_not_truncated():
    label=solve_actions(rows(),targets=[target(120,'long')],target_quotes={120:quote(6)})['labels'][0]
    assert label['long']['hold_seconds']==120 and label['profit']==1


def test_both_directions_negative_values_fees_and_target_boundary():
    targets=[target(2,'long'),target(3,'short'),target(10,'long')]
    result=solve_actions(rows(3),targets=targets,target_quotes={2:quote(4),3:quote(4.5),10:quote(6)},cost_bps=5)
    assert result['labels'][0]['long']['gross_profit']==-1
    assert result['labels'][0]['short']['gross_profit']==.5
    assert result['labels'][0]['short']['net_profit']==pytest.approx(.5-9.5*.0005)
    assert result['labels'][2]['long']['exit_time']==10


def test_missing_target_quote_does_not_skip_to_later_target():
    label=solve_actions(rows(),targets=[target(2),target(3)],target_quotes={3:quote(4)})['labels'][0]
    assert label['short'] is None
    assert label['action_reasons']['short']=='no_fresh_quote_at_macd_target'
    assert label['targets']['short']['exit_time']==2


def test_no_future_target_is_explicit():
    label=solve_actions(rows(),targets=[],target_quotes={})['labels'][0]
    assert label['action']=='unavailable' and label['profit'] is None
    assert label['action_reasons']['long']=='no_future_macd_target'


def test_exact_target_quote_uses_no_future_quote_and_rejects_staleness():
    sampler=ActionGrid(0,1,target_times=[.25,2.5])
    for t,price in [(0,5),(.5,9)]:
        sampler.observe(dict(ts=datetime.fromtimestamp(t,timezone.utc).isoformat(),kind='quote',
                             bid_price=price,ask_price=price,bid_size=100,ask_size=100))
    sampler.finish()
    assert sampler.target_quotes[.25]['bid']==5
    assert sampler.target_quotes[2.5] is None


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


def test_service_reuses_base_parameters_and_reads_target_beyond_window(monkeypatch):
    import asyncio
    import src.backend.hindsight_action_service as service
    request=service.ActionRequest(ticker='SUGP',session_date='2026-08-21',window_minutes=1,lookback_seconds=7)
    start,end=request.bounds();t=start.timestamp();seen={}
    p=target(t+200,'long')
    async def base(req,progress):
        assert req.lookback_seconds==7
        return dict(positions=[p],end=end.isoformat(),algorithm='base',parameters={},source_revision={},macd_provenance=[])
    class Source:
        def __init__(self,*args,**kwargs):seen.update(kwargs);self.source_revision={}
        async def stream_rows(self):
            events=[]
            for dt in (-3,-2,-1):events.append(dict(ts=datetime.fromtimestamp(t+dt,timezone.utc).isoformat(),kind='trade',price=5,size=100))
            for dt,price in [(0,5),(200,6)]:events.append(dict(ts=datetime.fromtimestamp(t+dt,timezone.utc).isoformat(),kind='quote',bid_price=price,ask_price=price,bid_size=100,ask_size=100))
            yield events
    monkeypatch.setattr(service,'calculate_hindsight',base)
    monkeypatch.setattr(service,'QmdHistoricalEventSource',Source)
    result=asyncio.run(service.calculate_actions(request))
    assert result['counts']['seconds']==61
    assert result['labels'][0]['long']['exit_time']==t+200
    assert result['labels'][0]['profit']==1
    assert seen['end'].timestamp()>t+200
