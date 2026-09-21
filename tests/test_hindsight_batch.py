from datetime import date
from io import StringIO
import threading
import time

import polars as pl
from polars.testing import assert_frame_equal
import pytest
from rich.console import Console

from src.market_engine.hindsight_batch import ordered_jobs,worker_budget
from scripts.build_hindsight_greedy import summarize,summarize_listing
from scripts.build_hindsight_dataset import table
from tests.test_hindsight_greedy import phase1_rows
from src.market_engine.hindsight_greedy import coefficients


def test_fast_summary_exactly_matches_reference_including_missing_and_negative():
    frame=coefficients(phase1_rows())
    for changed in (frame,frame.with_columns(pl.lit(None,dtype=pl.Float64).alias('open_value_per_dollar')),
                    frame.with_columns(pl.lit(-.4).alias('open_value_per_dollar'))):
        for mode in ('long','short','long_short'):
            assert_frame_equal(summarize(changed,mode),summarize_listing(changed,mode))


def test_bounded_scheduler_order_error_and_stop():
    active=0;peak=0;lock=threading.Lock();stop=threading.Event()
    def work(i):
        nonlocal active,peak
        with lock:active+=1;peak=max(peak,active)
        time.sleep(.005)
        with lock:active-=1
        if i==2:raise ValueError('expected')
        return i*2
    got=list(ordered_jobs(range(8),work,3))
    assert [i for i,_ in got]==list(range(8))
    assert isinstance(got[2][1],ValueError) and peak<=3
    stream=ordered_jobs(range(20),work,2,stop.is_set)
    next(stream);stop.set()
    assert len(list(stream))<=1


def test_resource_admission_rejects_overcommit(monkeypatch):
    import src.market_engine.hindsight_batch as batch
    from types import SimpleNamespace
    monkeypatch.setattr(batch.os,'cpu_count',lambda:16)
    monkeypatch.setattr(batch.psutil,'virtual_memory',lambda:SimpleNamespace(available=16*1024**3))
    assert worker_budget(None)==3
    with pytest.raises(ValueError):worker_budget(8)


def test_campaign_terminal_fits_compact_and_has_no_escape_sequences():
    stream=StringIO();console=Console(file=stream,width=80,color_system=None)
    console.print(table(dict(completed=1,failed=1,interrupted=0),{'2026-08-21':'Phase 1'},4,12))
    assert 'Queued' in stream.getvalue() and 'Phase 1' in stream.getvalue()
    assert '\x1b[' not in stream.getvalue()


def test_macd_page_boundaries_do_not_change_intervals(monkeypatch):
    from datetime import datetime,timedelta
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo
    from src.backend import hindsight_service as service
    start=datetime(2026,8,21,4,tzinfo=ZoneInfo('America/New_York'));end=start+timedelta(hours=16)
    def source(request):
        a=datetime.fromisoformat(request.start);b=datetime.fromisoformat(request.end)
        rows=[]
        for n in range(1,17):
            stamp=start+timedelta(hours=n)
            if a<stamp<=b:rows.append(dict(bar_end=stamp.isoformat(),macd_line=1 if n%3 else -1,macd_signal=0))
        return SimpleNamespace(payload=dict(indicators=rows,indicators_available=True,indicator_provenance={'complete':True}))
    monkeypatch.setattr(service,'qmd_product_request',source)
    one=service.load_macd_intervals('A',start,end,lambda **kw:None,time.monotonic()+60)[0]
    eight=service.load_macd_intervals('A',start,end,lambda **kw:None,time.monotonic()+60,window_hours=8,workers=1)[0]
    assert one==eight
    with pytest.raises(ValueError):service.load_macd_intervals('A',start,end,None,0,window_hours=16)


def test_quote_columnar_input_matches_dict_input():
    from src.market_engine.hindsight_phase1 import opportunities,bounds
    day=date(2026,8,21);left,right=bounds(day)
    q=pl.DataFrame({'time_us':range(left,right+1,1000000)}).with_columns(
        pl.col('time_us').alias('quote_us'),pl.lit(4.).alias('ask'),pl.lit(3.).alias('bid'),
        pl.lit(1.).alias('ask_size'),pl.lit(1.).alias('bid_size'))
    assert_frame_equal(opportunities(day,[],q,[]),opportunities(day,[],q.to_dicts(),[]))


def test_campaign_two_dates_reuses_phases_and_stops_at_failure(tmp_path,monkeypatch):
    import scripts.build_hindsight_dataset as campaign
    from src.market_engine.level_book_store import read,write
    from types import SimpleNamespace
    monkeypatch.setattr(campaign,'preflight',lambda args:(tmp_path,'test','fingerprint',2))
    monkeypatch.setattr(campaign,'client',lambda _:SimpleNamespace(close=lambda:None))
    monkeypatch.setattr(campaign,'query',lambda *_:[dict(ticker='A',listing_id='A')])
    monkeypatch.setattr(campaign,'market_sessions',lambda a,b:[date(2026,8,20),date(2026,8,21)])
    def day_job(day,root,plan,args,slots,publish):
        publish('mock verified');assert slots==1
        return dict(date=str(day),status='completed')
    monkeypatch.setattr(campaign,'execute_day',day_job)
    assert campaign.main(['run','--start','2026-08-20','--end','2026-08-21','--day-workers','2'])==0
    root=next((tmp_path/'hindsight-campaigns').iterdir())
    assert read(root/'complete.json')['days']==2
    (root/'STOP').touch()
    assert campaign.main(['run','--start','2026-08-20','--end','2026-08-21','--day-workers','2'])==2
    assert not (root/'complete.json').exists()
