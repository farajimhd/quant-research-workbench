"""Shared as-of V7 book feed for prepared Replay sessions and strategy callers."""
from collections import OrderedDict
from datetime import datetime,time,date
from math import prod
from threading import RLock
from zoneinfo import ZoneInfo
import re
from .level_book_store import ROOT,read,verified_book
from .historical_level_checkpoint import digest
from .streaming_level_book import StreamingLevelBook

_sessions=OrderedDict();_lock=RLock()


def catalog():
    result=[]
    for path in sorted(ROOT.glob('*/*/manifest.json')):
        m=read(path)
        result.append(dict(id=path.parent.parent.name,ticker=m['ticker'],dates=m['dates'],ready=m['ready'],version=m['version']))
    return result


def book_at(book_id,ticker,session_date,time_et):
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',book_id) or not re.fullmatch(r'[A-Z0-9.\-]{1,20}',ticker):raise ValueError('Invalid book identity')
    day=date.fromisoformat(session_date);clock=time.fromisoformat(time_et)
    if clock.tzinfo is not None or not time(4)<=clock<=time(20) or clock.microsecond:raise ValueError('Choose a whole second from 04:00 through 20:00 ET')
    tz=ZoneInfo('America/New_York');start=datetime.combine(day,time(4),tz).timestamp();end=datetime.combine(day,time(20),tz).timestamp();stamp=datetime.combine(day,clock,tz).timestamp()
    root=ROOT/book_id/ticker;m=read(root/'manifest.json')
    if m['ticker']!=ticker or not m['ready'] or session_date not in m['dates']:raise ValueError('Book session is not prepared')
    paths=[root/'manifest.json',root/'books'/f"{m['prior_session']}.json.gz",root/'inputs'/f'{session_date}.json.gz']
    signature=tuple((p.stat().st_size,p.stat().st_mtime_ns) for p in paths)
    key=(str(ROOT),book_id,ticker,session_date)
    with _lock:
        cached=_sessions.get(key)
        if cached is None or cached['signature']!=signature:
            prior=verified_book(paths[1]);inputs=read(paths[2])
            if prior['checkpoint_hash']!=m['prior_hash'] or inputs['content_hash']!=m['test_input_hash'] or inputs['content_hash']!=digest({k:v for k,v in inputs.items() if k!='content_hash'}):raise ValueError('Prepared V7 provenance mismatch')
            actions=[s for s in m['splits'] if prior['session']<s['execution_date']<=session_date]
            kwargs=dict(ticker=ticker,session=session_date,start=start,end=end,split_factor=prod(float(s['split_from'])/float(s['split_to']) for s in actions),split_evidence=actions)
            cached=dict(signature=signature,prior=prior,inputs=inputs,kwargs=kwargs,engine=StreamingLevelBook(prior,**kwargs),index=0)
            if signature!=tuple((p.stat().st_size,p.stat().st_mtime_ns) for p in paths):raise ValueError('V7 artifacts changed during loading')
            _sessions[key]=cached
            while len(_sessions)>2:_sessions.popitem(last=False)
        _sessions.move_to_end(key)
        if stamp<cached['engine'].as_of:
            cached['engine']=StreamingLevelBook(cached['prior'],**cached['kwargs']);cached['index']=0
        bars=cached['inputs']['bars'];engine=cached['engine']
        while cached['index']<len(bars) and bars[cached['index']]['t']<=stamp:
            engine.update(bars[cached['index']],observed_at=stamp);cached['index']+=1
        return dict(engine.snapshot(stamp),book_id=book_id,input_hash=cached['inputs']['content_hash'],book_session=m['prior_session'])
