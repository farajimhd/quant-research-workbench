from datetime import datetime
from types import SimpleNamespace
import pytest
from research.level_book.v7 import campaign as c
from research.level_book.v7.filtered_prefix import worker


def test_prefix_resume_extend_matches_full_campaign(tmp_path, monkeypatch):
    days = ['2026-08-20', '2026-08-21', '2026-08-24']
    metadata = []; raw = {}
    for index, day in enumerate(days):
        start = int(datetime.fromisoformat(day+'T04:00:00-04:00').timestamp())
        prices = [10,10.04,10.08,10.12,10.08,10.04,10]*5
        raw[day] = [dict(t=start+i+1,open=p,high=p,low=p,close=p,volume=100,trades=1,last_count=1,extrema_count=1) for i,p in enumerate(prices)]
        metadata.append(dict(ticker='TEST',source_date=day,event_count=35,next_ordinal=35*(index+1),last_ordinal=35*(index+1)-1,first_sip_timestamp_us=start*1000000,last_sip_timestamp_us=(start+36)*1000000))
    coverage = dict(ticker='TEST',days=3,events=105,first=days[0],last=days[-1],signature='fixture')
    reporting=[dict(source_date=day,status='complete') for day in days]
    plan = dict(plan_hash='plan',start=days[0],end=days[-1],rules=[],reporting_coverage_hash=c.digest(reporting),
                rows=[dict(ticker='TEST',status='queued',coverage=coverage)])
    monkeypatch.setattr(c,'checked_plan',lambda _:plan)
    fetched=[]
    def query(sql,threads=1):
        if sql==c.RULE_SQL:return []
        if 'GROUP BY ticker ORDER BY ticker' in sql:return [coverage]
        if 'market_stock_split' in sql:return []
        if 'historical_trade_reporting_coverage_v1' in sql:
            return reporting
        if 'GROUP BY t ORDER BY t' in sql:
            day=next(d for i,d in enumerate(days) if f'ordinal>={35*i} AND' in sql);fetched.append(day);return raw[day]
        if 'events_ordinal_continuity' in sql:
            return [m for m in metadata if "source_date='" not in sql or m['source_date'] in sql]
        raise AssertionError(sql)
    monkeypatch.setattr(c,'query',query)
    root=tmp_path/'prefix';args=SimpleNamespace(runtime=root,ticker='TEST',threads=1,before=days[1])
    original=c.write
    def interrupt(path,value,**kwargs):
        if path.parent.name=='receipts':raise OSError('interrupted after book')
        return original(path,value,**kwargs)
    monkeypatch.setattr(c,'write',interrupt)
    with pytest.raises(OSError,match='interrupted'):worker(args)
    target=root/'tickers'/'TEST'
    assert not (target/'prefixes').exists()
    first=c.read(target/'books'/f'{days[0]}.json.gz')
    monkeypatch.setattr(c,'write',original)
    worker(args)
    assert fetched==[days[0],days[0]]
    assert c.read(target/'books'/f'{days[0]}.json.gz')==first
    publication=c.read(target/'prefixes'/f'{days[1]}.json')
    worker(args)
    assert fetched==[days[0],days[0]], 'Verified prefix must not refetch bars'
    args.before='2026-08-25';worker(args)
    assert fetched[-2:]==days[1:]
    assert not (target/'ready.json').exists()
    assert c.read(target/'prefixes'/f'{days[1]}.json')==publication
    full=tmp_path/'full';c.worker(SimpleNamespace(runtime=full,ticker='TEST',threads=1))
    for day in days:
        assert c.read(target/'books'/f'{day}.json.gz')==c.read(full/'tickers/TEST/books'/f'{day}.json.gz')
    receipt=target/'receipts'/f'{days[1]}.json';value=c.read(receipt);value['parent_hash']='corrupt';c.write(receipt,value,immutable=False)
    with pytest.raises(ValueError,match='Resume source/parent'):worker(args)
