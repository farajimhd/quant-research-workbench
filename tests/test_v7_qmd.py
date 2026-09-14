from copy import deepcopy
from datetime import datetime,timezone
import pytest

from src.market_engine.v7_qmd import Service,completed_seconds
from src.market_engine.historical_level_checkpoint import digest
from src.backend.swing_book_source import session_bounds
from tests.test_streaming_level_book import engine,bars


class Catalog:
    fingerprint='fixture'
    def __init__(self,root):
        self.root=root
        stream=engine(historical=True)
        self.prior=stream.historical_checkpoint('fixture')
        self.prior.update(session='2026-08-20',available_at=session_bounds('2026-08-20')[1].timestamp())
        self.prior['checkpoint_hash']=digest({k:v for k,v in self.prior.items() if k!='checkpoint_hash'})
    def select(self,ticker,day):
        return deepcopy(self.prior),dict(catalog_hash=self.fingerprint,checkpoint_session='2026-08-20',
            last_source_session='2026-08-21',source_plan={'days':[{'source_date':'2026-08-20'}]})


class Source:
    def __init__(self):self.bars=bars(engine(historical=True));self.calls=[]
    def splits(self,*args):return []
    def seconds(self,ticker,day,start,end,mode):
        self.calls.append((start,end))
        return [b for b in self.bars if start<b['t']<=end],{}


def make(tmp_path):
    source=Source();return Service(Catalog(tmp_path),source),source


def test_coverage_does_not_consume_current_day_and_excludes_only_missing_books(tmp_path, monkeypatch):
    from src.market_engine.v7_catalog import CoverageUnavailable
    service, source = make(tmp_path)
    original = service.catalog.select
    def select(ticker, day):
        if ticker == 'MISSING':
            raise CoverageUnavailable('ambiguous published ticker identity')
        return original(ticker, day)
    monkeypatch.setattr(service.catalog, 'select', select)
    packet = service.coverage(['TEST', 'MISSING'], '2026-08-21T04:00:00-04:00')
    assert {r['ticker']: r['eligible'] for r in packet['rows']} == {'TEST': True, 'MISSING': False}
    assert source.calls == []
    assert service.sessions == {}
    def corrupt(*args):
        raise ValueError('V7 checkpoint hash mismatch')
    monkeypatch.setattr(service.catalog, 'select', corrupt)
    with pytest.raises(ValueError, match='hash mismatch'):
        service.coverage(['TEST'], '2026-08-21T04:00:00-04:00')


def test_coverage_rejects_empty_and_future_seed(tmp_path):
    service, _ = make(tmp_path)
    service.catalog.prior['levels'] = []
    assert not service.coverage(['TEST'], '2026-08-21T04:00:00-04:00')['rows'][0]['eligible']
    service.catalog.prior['available_at'] = datetime(2026, 8, 22, tzinfo=timezone.utc).timestamp()
    assert 'not yet available' in service.coverage(['TEST'], '2026-08-21T04:00:00-04:00')['rows'][0]['reason']


def at(t):return datetime.fromtimestamp(t,timezone.utc)


def test_resistance_transition_projection_preserves_origin_and_causal_availability():
    from src.market_engine.v7_qmd import projection
    from src.trading_runtime.structure_level_contract import strategy_snapshot
    stream=engine(historical=True)
    row=stream.rows[0];row.update(role='resistance',qualified=True)
    stamp=stream.start+1
    stream._resolve(0,dict(at=stamp,role='resistance'), 'breakout',stamp)
    assert row['role']=='transition' and row['transition_from']=='resistance'
    result=projection(stream,stamp,{},False)
    projected=strategy_snapshot(result,at(stamp))
    item=next(r for r in projected['unified_levels'] if r['unified_level_id']==row['id'])
    assert item['side']==0 and item['transition_from']=='resistance'
    assert item['confirmed_at_ms']==stamp*1000
    from src.trading_runtime.structural_recovery import observe_market
    market=observe_market(dict(time=stamp-1,end=stamp,open=row['price'],close=row['price'],
        low=row['lower'],high=row['upper'],volume=1000),projected['unified_levels'],
        dict(id='v7-test',fingerprint='fixture',version=item['book_version']),{})
    assert market['row']['effective_at']==stamp
    assert not any(r['unified_level_id']==row['id'] for r in strategy_snapshot(result,at(stamp-1))['unified_levels'])


def test_prefix_incremental_rewind_and_strategy_bands(tmp_path):
    service,source=make(tmp_path);first=source.bars[7]['t'];last=source.bars[-1]['t']
    prefix=service.snapshot('TEST',at(first))
    final=service.snapshot('TEST',at(last))
    assert service.snapshot('TEST',at(first))==prefix
    other,_=make(tmp_path)
    assert other.snapshot('TEST',at(last))==final
    from src.trading_runtime.structure_level_contract import strategy_snapshot
    projected=strategy_snapshot(final,at(last))
    assert projected['unified_levels']
    assert all(r['lower']<r['upper'] and r['fit']['status']=='estimated' for r in projected['unified_levels'])
    assert all(r['confirmed_at_ms']<=last*1000 for r in projected['unified_levels'])
    assert all(s['valid_to']<=first for s in prefix['segments'])


def test_close_digest_does_not_depend_on_polling_and_rollover_uses_close(tmp_path):
    from src.market_engine.level_book_store import verified_book
    service,source=make(tmp_path/'a');last=source.bars[-1]['t']
    service.snapshot('TEST',at(source.bars[5]['t']),mode='live')
    service.snapshot('TEST',at(last),mode='live')
    close=session_bounds('2026-08-21')[1]
    service.snapshot('TEST',close,mode='live')
    checkpoint=verified_book(service.closing_root/'TEST'/'2026-08-21.json.gz')
    other,_=make(tmp_path/'b');other.snapshot('TEST',close,mode='live')
    assert verified_book(other.closing_root/'TEST'/'2026-08-21.json.gz')==checkpoint
    nextday=service.snapshot('TEST',session_bounds('2026-08-24')[0],mode='live')
    assert nextday['book_hash']==checkpoint['checkpoint_hash']


def test_failed_fit_evicts_mutated_session(tmp_path,monkeypatch):
    service,source=make(tmp_path)
    from src.market_engine.streaming_level_book import StreamingLevelBook
    def fail(*args,**kwargs):raise ValueError('MLE failed')
    monkeypatch.setattr(StreamingLevelBook,'update',fail)
    with pytest.raises(ValueError,match='MLE failed'):service.snapshot('TEST',at(source.bars[-1]['t']))
    assert not service.sessions


def test_completed_seconds_validate_identity_nan_and_duplicates():
    row=dict(sym='TEST',timeframe='1s',bar_end='2026-08-21T08:00:01+00:00',is_closed=True,
        open=10.,high=10.1,low=9.9,close=10.,volume=100.)
    t=datetime.fromisoformat(row['bar_end']).timestamp()
    assert len(completed_seconds([row,row,dict(row,is_closed=False)],'TEST',t-1,t)[0])==1
    assert not completed_seconds([row],'TEST',t-2,t-1)[0]
    with pytest.raises(ValueError,match='Conflicting'):completed_seconds([row,dict(row,volume=101)],'TEST',t-1,t)
    with pytest.raises(ValueError,match='Invalid'):completed_seconds([dict(row,close=float('nan'))],'TEST',t-1,t)
    with pytest.raises(ValueError,match='identity'):completed_seconds([dict(row,sym='OTHER')],'TEST',t-1,t)


def test_no_historical_fallback_on_missing_checkpoint(tmp_path):
    service,source=make(tmp_path)
    def missing(*args):raise ValueError('missing checkpoint')
    service.catalog.select=missing
    with pytest.raises(ValueError,match='missing checkpoint'):service.snapshot('TEST',at(source.bars[-1]['t']))
    assert not service.sessions


def test_restart_recovers_previous_live_close_from_durable_qmd_seconds(tmp_path):
    baseline,source=make(tmp_path/'baseline')
    baseline.snapshot('TEST',session_bounds('2026-08-21')[1],mode='live')
    recovered,_=make(tmp_path/'recovered')
    select=recovered.catalog.select
    def previous(*args):
        book,provenance=select(*args)
        return book,dict(provenance,last_source_session='2026-08-20')
    recovered.catalog.select=previous
    value=recovered.snapshot('TEST',session_bounds('2026-08-24')[0],mode='live')
    from src.market_engine.level_book_store import verified_book
    expected=verified_book(baseline.closing_root/'TEST'/'2026-08-21.json.gz')
    assert value['book_hash']==expected['checkpoint_hash']
    assert verified_book(recovered.closing_root/'TEST'/'2026-08-21.json.gz')==expected


def test_chart_checkpoint_preserves_prior_day_geometry_without_advancing_strategy(tmp_path):
    service,source=make(tmp_path)
    start,end=session_bounds('2026-08-20')
    row=service.catalog.prior['levels'][0]
    row['role_segments']=[dict(row['role_segments'][0],start=start.timestamp()+60),
        dict(row['role_segments'][0],start=start.timestamp()+120,role='resistance',price=10.1,lower=10.09,upper=10.11)]
    result=service.chart_checkpoint('TEST',session_bounds('2026-08-21')[0])
    assert result['purpose']=='historical_chart_only' and result['retrospective']
    assert result['next_before']=='2026-08-20' and 'unified_levels' not in result
    assert result['segments'][0]['valid_from']==start.timestamp()+60
    assert result['segments'][0]['valid_to']==start.timestamp()+120
    assert result['segments'][1]['price']==10.1 and result['segments'][1]['valid_to']==end.timestamp()
    assert not service.sessions and not source.calls
    assert all(not s['model_input'] for s in result['segments'])


def test_chart_checkpoint_rejects_unavailable_or_unfitted_geometry(tmp_path):
    service,_=make(tmp_path)
    service.catalog.prior['available_at']=session_bounds('2026-08-22')[0].timestamp()
    with pytest.raises(ValueError,match='not yet available'):
        service.chart_checkpoint('TEST',session_bounds('2026-08-21')[0])
    service.catalog.prior['available_at']=session_bounds('2026-08-20')[1].timestamp()
    row=service.catalog.prior['levels'][0]
    row['role_segments'][0]['start']=session_bounds('2026-08-20')[0].timestamp()
    row['role_segments'][0].pop('fit')
    with pytest.raises(ValueError,match='lacks fitted geometry'):
        service.chart_checkpoint('TEST',session_bounds('2026-08-21')[0])
