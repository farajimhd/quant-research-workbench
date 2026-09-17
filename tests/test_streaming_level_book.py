import json
import os
import pytest
from src.market_engine.historical_level_checkpoint import digest
from src.market_engine.streaming_level_book import StreamingLevelBook


def engine(*,historical=False):
    from tests.test_reaction_band import empty
    s=empty()
    if historical:
        for i,p in enumerate([10,10.001,9.999]):s._proposal(p,1100+i*10,'support',{'t':1102+i*10},.1)
    prior=s.historical_checkpoint('fixture');prior['session']='2026-08-20'
    prior['checkpoint_hash']=digest({k:v for k,v in prior.items() if k!='checkpoint_hash'})
    from src.backend.swing_book_source import session_bounds
    start,end=session_bounds('2026-08-21')
    return StreamingLevelBook(prior,ticker='TEST',session='2026-08-21',start=start.timestamp(),end=end.timestamp())


def bars(stream):
    prices=[10,10.04,10.08,10.12,10.08,10.04,10,10.04,10.08,10.12,10.08,10.04,10]*2
    return [dict(t=stream.start+300+i+1,open=p,high=p,low=p,close=p,volume=100) for i,p in enumerate(prices)]


def test_new_levels_wait_for_repeated_confirmations_and_start_at_available_time():
    stream=engine();sequence=bars(stream)
    for b in sequence[:7]:stream.update(b,observed_at=b['t'])
    assert stream.snapshot()['current_day_count']==0
    for b in sequence[7:]:stream.update(b,observed_at=b['t'])
    result=stream.snapshot();assert result['current_day_count']>=1
    assert all(s['valid_from']>=sequence[7]['t'] for s in result['segments'])
    assert all(s['valid_to']<=sequence[-1]['t'] for s in result['segments'])


def test_checkpoint_roundtrip_and_prefix_replay_are_identical():
    stream=engine(historical=True);sequence=bars(stream)
    for b in sequence[:8]:stream.update(b)
    original=stream.snapshot();checkpoint=json.loads(json.dumps(stream.checkpoint()))
    resumed=StreamingLevelBook.restore(checkpoint)
    assert resumed.snapshot()==original
    for b in sequence[8:]:stream.update(b);resumed.update(b)
    assert stream.snapshot()==resumed.snapshot()
    prefix=engine(historical=True)
    for b in sequence[:8]:prefix.update(b)
    assert prefix.snapshot()==original
    checkpoint['state']['rows'][0]['lower']=1
    with pytest.raises(ValueError,match='integrity'):StreamingLevelBook.restore(checkpoint)


def test_future_invalid_and_out_of_order_bars_fail_before_mutation():
    stream=engine();b=bars(stream)[0];saved=stream.checkpoint()
    with pytest.raises(ValueError):stream.update(b,observed_at=b['t']-1)
    assert stream.checkpoint()==saved
    stream.update(b)
    with pytest.raises(ValueError):stream.update(b)
    with pytest.raises(ValueError):stream.update(dict(b,t=b['t']+1,close=float('nan')))


def test_existing_historical_identity_survives_adaptive_refits():
    stream=engine(historical=True);geometry=[(r['id'],r['lower'],r['upper']) for r in stream.rows]
    sequence=bars(stream)
    for b in sequence:stream.update(b)
    for ident,lo,hi in geometry:
        row=next(r for r in stream.rows if r['id']==ident)
        assert row['historical'] and row['fit']['status']=='estimated'
    # A proposal at the exact historical center cannot create a day identity.
    before=len(stream.rows);r=stream.rows[0]
    stream._proposal(r['price'],sequence[-1]['t'],'support',sequence[-1],.06)
    assert len(stream.rows)==before


@pytest.mark.skipif(not os.environ.get('V7_REAL_TEST'),reason='Prepared JUNS/SUGP campaign required')
@pytest.mark.parametrize('ticker',['JUNS','SUGP'])
def test_real_day_prefix_rewind_restore_and_full_day_capacity(ticker):
    from src.market_engine import level_book_feed as feed
    from src.market_engine.level_book_store import read,verified_book
    from src.backend.swing_book_source import session_bounds
    from math import prod
    book_id='jan2025-aug2026-v2-mle';day='2026-08-21';root=feed.ROOT/book_id/ticker;m=read(root/'manifest.json')
    first=feed.book_at(book_id,ticker,day,'07:10:46')
    later=feed.book_at(book_id,ticker,day,'07:30:00')
    assert feed.book_at(book_id,ticker,day,'07:10:46')==first
    prior=verified_book(root/'books'/f"{m['prior_session']}.json.gz");inputs=read(root/'inputs'/f'{day}.json.gz')
    actions=[s for s in m['splits'] if prior['session']<s['execution_date']<=day]
    start,end=session_bounds(day)
    stream=StreamingLevelBook(prior,ticker=ticker,session=day,start=start.timestamp(),end=end.timestamp(),split_factor=prod(float(s['split_from'])/float(s['split_to']) for s in actions),split_evidence=actions)
    sequence=inputs['bars'];cut=next(i for i,b in enumerate(sequence) if b['t']>first['as_of'])
    for b in sequence[:cut]:stream.update(b)
    assert all(first[k]==v for k,v in stream.snapshot(first['as_of']).items())
    restored=StreamingLevelBook.restore(json.loads(json.dumps(stream.checkpoint())))
    for b in sequence[cut:]:stream.update(b);restored.update(b)
    assert stream.snapshot()==restored.snapshot()
    # Advancing through the whole day must not redraw the earlier chart prefix.
    def prefix_segments(snapshot):
        return [dict(s,valid_to=min(s['valid_to'],first['as_of'])) for s in snapshot['segments'] if s['valid_from']<first['as_of']]
    assert prefix_segments(stream.snapshot())==prefix_segments(first)
    assert stream.bars_processed==len(sequence)
    assert later['max_input_timestamp']<=later['as_of']
