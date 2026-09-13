"""QMD-owned V7 state: preceding-session seed plus causal completed seconds."""
from collections import OrderedDict
from bisect import bisect_right
from datetime import datetime,time,timedelta,timezone
from math import prod,isfinite
import hashlib
import json
from pathlib import Path
import urllib.parse

from .v7_catalog import Catalog,BOOK_ID
from .streaming_level_book import StreamingLevelBook,VERSION
from .historical_level_checkpoint import digest
from .level_book_store import verified_book
from src.backend.swing_book_source import NY,session_bounds


def stamp(value):
    d=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('V7 timestamps require timezone offsets')
    return d


def completed_seconds(rows,ticker,start,end):
    by_time={};unavailable=0
    for row in rows:
        if row.get('is_closed') is False:continue
        if row.get('sym',row.get('ticker',ticker))!=ticker or row.get('timeframe','1s')!='1s':
            raise ValueError('V7 completed-bar identity mismatch')
        t=stamp(row['bar_end']).timestamp()
        if not start<t<=end:continue
        if t!=int(t):raise ValueError('V7 source must publish whole completed seconds')
        values={k:float(row[k]) for k in ('open','high','low','close','volume')}
        if all(values[k]==0 for k in ('open','high','low','close')):
            unavailable+=1;continue
        if not all(isfinite(v) for v in values.values()) or values['low']<=0 or values['volume']<0 or values['low']>min(values['open'],values['close']) or values['high']<max(values['open'],values['close']):
            raise ValueError('Invalid QMD completed OHLCV')
        bar=dict(t=t,**values)
        if t in by_time and by_time[t]!=bar:raise ValueError('Conflicting completed seconds; V7 reload required')
        by_time[t]=bar
    return [by_time[t] for t in sorted(by_time)],unavailable


class QmdSource:
    def __init__(self):self.history=OrderedDict()

    def seconds(self,ticker,day,start,end,mode):
        from src.backend.qmd_gateway_client import qmd_history_get_json,qmd_bars,qmd_intraday_bar_history
        if end<=start:return [],dict(unavailable_seconds=0)
        if mode=='history':
            begin,close=session_bounds(day)
            key=(ticker,day)
            # Historical source prefetch is separate from causal inference:
            # only completed bars <= the requested cutoff enter the MLE engine.
            # Closed-day data are immutable canonical inputs. Never cache an
            # in-progress day as complete.
            closed=close<=datetime.now(timezone.utc)
            cached=self.history.get(key) if closed else None
            if cached is not None:
                self.history.move_to_end(key)
                times,bars,audit=cached
                return bars[bisect_right(times,start):bisect_right(times,end)],dict(audit,consumed_through=end)
            request_start=begin.timestamp() if closed else start
            request_end=close.timestamp() if closed else end
            result=qmd_history_get_json('/level-book-v7/seconds/'+urllib.parse.quote(ticker),dict(
                start=datetime.fromtimestamp(request_start,timezone.utc).isoformat(),end=datetime.fromtimestamp(request_end,timezone.utc).isoformat()),timeout=180)
            if result.get('complete') is not True:raise ValueError('Incomplete QMD historical seconds')
            rows=result['bars']
            if closed:
                bars,unavailable=completed_seconds(rows,ticker,request_start,request_end)
                audit=dict(unavailable_seconds=unavailable,source='qmd-history',source_revision=result['source_revision'],late_trade_policy='qmd-completed-seconds-excludes-delayed-reports')
                times=[b['t'] for b in bars]
                self.history[key]=(times,bars,audit)
                while len(self.history)>4:self.history.popitem(last=False)
                return bars[bisect_right(times,start):bisect_right(times,end)],dict(audit,consumed_through=end)
        else:
            recent=qmd_bars(ticker,timeframe='1s',row_limit=500)
            rows=recent.get('history',[])
            if not rows or min(stamp(b['bar_end']).timestamp() for b in rows)>start+1:
                before=int(end*1e6)+1;rows=list(rows)
                for _ in range(8):
                    page=qmd_intraday_bar_history(ticker,timeframe='1s',start_date=day,end_date=day,
                        before_event_timestamp_us=before,row_limit=10000)
                    if page.get('complete') is not True:raise ValueError('Incomplete QMD live seconds')
                    rows.extend(page.get('bars',[]))
                    following=page.get('next_before_event_timestamp_us')
                    if not page.get('has_more') or (isinstance(following,int) and following<=int(start*1e6)):break
                    if not isinstance(following,int) or following>=before:raise ValueError('Invalid QMD live history cursor')
                    before=following
                else:raise ValueError('V7 live bootstrap exceeds one session')
        bars,unavailable=completed_seconds(rows,ticker,start,end)
        return bars,dict(unavailable_seconds=unavailable,source='qmd-'+mode,late_trade_policy='qmd-completed-seconds-excludes-delayed-reports')

    def splits(self,ticker,first,last,as_of):
        from research.level_book.v7.campaign import literal
        from research.mlops.clickhouse import ClickHouseHttpClient,default_clickhouse_url,default_clickhouse_user,default_clickhouse_password
        from scripts.build_structure_book_clickhouse import canonical_splits
        # Session initialization is infrequent. Do not borrow the campaign's
        # persistent socket, which can expire while the streaming engine runs.
        client=ClickHouseHttpClient(default_clickhouse_url(),default_clickhouse_user(),default_clickhouse_password(),
            timeout_seconds=30,default_query_params=dict(readonly=1,max_threads=1,max_result_rows=10000,result_overflow_mode='throw'))
        try:
            sql=f"SELECT execution_date,split_from,split_to,inserted_at FROM q_live.market_stock_split_v1 FINAL WHERE provider_ticker={literal(ticker)} AND execution_date>{literal(first)} AND execution_date<={literal(last)} AND inserted_at<=parseDateTime64BestEffort({literal(as_of.isoformat())}) ORDER BY execution_date FORMAT JSONEachRow"
            return canonical_splits([json.loads(line) for line in client.execute(sql).splitlines() if line])
        finally:client.close()


def projection(engine,as_of,provenance,include_segments):
    snapshot=engine.snapshot(as_of,include_segments=include_segments)
    levels=[]
    for row in engine.rows:
        if not row['qualified'] or row['role'] not in ('support','resistance'):continue
        last=row['segments'][-1]
        origin=datetime.combine(datetime.fromisoformat(row['origin_session']).date(),time(4),NY).timestamp()*1000
        levels.append(dict(unified_level_id=row['id'],price=row['price'],lower=row['lower'],upper=row['upper'],
            side=1 if row['role']=='support' else -1,role=row['role'],historical=row['historical'],
            origin_session=row['origin_session'],book_version=VERSION,lifecycle='active',timeframes=['1s'],
            created_at_ms=row['created_at']*1000,confirmed_at_ms=last['start']*1000,
            oldest_member_confirmed_at_ms=origin if row['historical'] else row['created_at']*1000,
            available_at_ms=last['start']*1000,fit=row['fit'],observation_count=row['fit']['count'],
            prominence=None,confidence=None))
    snapshot.update(unified_levels=levels,qmd_structure_unified_levels=levels,
        qmd_level_book_version=VERSION,provenance=provenance,book_id=BOOK_ID)
    if not include_segments:snapshot.pop('segments',None)
    return snapshot


class Service:
    def __init__(self,catalog=None,source=None,closing_root=None,max_sessions=16):
        self.catalog=catalog or Catalog();self.source=source or QmdSource()
        self.sessions=OrderedDict();self.max_sessions=max_sessions
        self.closing_root=Path(closing_root) if closing_root else self.catalog.root/'qmd-live-closing-v7'

    def _closing(self,engine,input_hash):
        book=engine.historical_checkpoint(input_hash)
        book['catalog_hash']=self.catalog.fingerprint
        book['checkpoint_hash']=digest({k:v for k,v in book.items() if k!='checkpoint_hash'})
        from research.level_book.v7.campaign_store import write as immutable_write
        immutable_write(self.closing_root/engine.ticker/(engine.session+'.json.gz'),book)
        return book

    def _seed(self,ticker,day,start,mode):
        prior,provenance=self.catalog.select(ticker,day)
        # Live session closes remain a separate, source-pinned continuation.
        if mode=='live':
            folder=self.closing_root/ticker
            for path in sorted(folder.glob('*.json.gz')) if folder.exists() else []:
                if prior['session']<path.name[:10]<day:
                    value=verified_book(path)
                    if value.get('catalog_hash')!=self.catalog.fingerprint:raise ValueError('V7 live close catalog changed')
                    if (value.get('ticker')!=ticker or value.get('session')!=path.name[:10]
                            or value.get('prior_checkpoint_hash')!=prior['checkpoint_hash']):
                        raise ValueError('V7 live closing checkpoint lineage mismatch')
                    prior=value
        from src.data_provider.calendar import market_sessions
        first=datetime.fromisoformat(prior['session']).date()+timedelta(days=1)
        last=datetime.fromisoformat(day).date()-timedelta(days=1)
        missing=market_sessions(first,last) if first<=last else []
        if mode=='live':
            # Recover an unflushed previous live close after a process restart
            # from QMD's durable completed-bar history, using the same engine.
            recover=[str(d)[:10] for d in missing if str(d)[:10]>provenance['last_source_session']]
            if len(recover)>5:raise ValueError('V7 live continuation needs historical catch-up beyond five sessions')
            for session in recover:
                begin,end=session_bounds(session)
                splits=self.source.splits(ticker,prior['session'],session,end)
                engine=StreamingLevelBook(prior,ticker=ticker,session=session,start=begin.timestamp(),end=end.timestamp(),
                    split_factor=prod(float(s['split_from'])/float(s['split_to']) for s in splits),split_evidence=splits)
                bars,_=self.source.seconds(ticker,session,begin.timestamp(),end.timestamp(),'live')
                if not bars:raise ValueError('No completed QMD bars to recover the previous live session')
                inputs=hashlib.sha256()
                for bar in bars:
                    engine.update(bar,observed_at=end.timestamp())
                    inputs.update((json.dumps(bar,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())
                prior=self._closing(engine,inputs.hexdigest())
            missing=[d for d in missing if str(d)[:10]>prior['session']]
        # Symbols may have no prints on a trading day. The frozen source plan
        # proves such absence within its range, not beyond the campaign end.
        known={r['source_date'] for r in provenance['source_plan']['days']}-set(provenance.get('verified_empty_sessions',[]))
        if any(str(d)[:10]>provenance['last_source_session'] or str(d)[:10] in known for d in missing):
            raise ValueError('V7 seed has missing intervening sessions; complete historical/closing checkpoints first')
        return prior,dict(provenance,checkpoint_session=prior['session'],checkpoint_hash=prior['checkpoint_hash'])

    def snapshot(self,ticker,as_of,mode='history',include_segments=True,cursor_id=''):
        if mode not in ('history','live'):raise ValueError('V7 source mode must be history or live')
        at=stamp(as_of) if isinstance(as_of,str) else as_of
        if at.tzinfo is None:raise ValueError('V7 as-of requires a timezone')
        day=at.astimezone(NY).date().isoformat();begin,close=session_bounds(day)
        cutoff=min(int(at.timestamp()),int(close.timestamp()))
        if cutoff<int(begin.timestamp()):raise ValueError('V7 session begins at 04:00 ET')
        if mode=='live' and at>datetime.now(timezone.utc):raise ValueError('Future live V7 request')
        if mode=='live':
            for old_key in list(self.sessions):
                if old_key[0]=='live' and old_key[1]==ticker and old_key[2]<day:
                    self.snapshot(ticker,session_bounds(old_key[2])[1],mode='live',include_segments=False)
                    self.sessions.pop(old_key,None)
        key=(mode,ticker,day,cursor_id if mode=='history' else '')
        state=self.sessions.get(key)
        if state is None or cutoff<state['cutoff']:
            prior,provenance=self._seed(ticker,day,begin.timestamp(),mode)
            splits=self.source.splits(ticker,prior['session'],day,at)
            state=dict(engine=StreamingLevelBook(prior,ticker=ticker,session=day,start=begin.timestamp(),end=close.timestamp(),
                split_factor=prod(float(s['split_from'])/float(s['split_to']) for s in splits),split_evidence=splits),
                cutoff=begin.timestamp(),provenance={k:v for k,v in provenance.items() if k!='source_plan'},audit={},input_digest=hashlib.sha256())
            self.sessions[key]=state
        self.sessions.move_to_end(key)
        while len(self.sessions)>self.max_sessions:self.sessions.popitem(last=False)
        try:
            if cutoff>state['cutoff']:
                bars,audit=self.source.seconds(ticker,day,state['cutoff'],cutoff,mode)
                for bar in bars:
                    state['engine'].update(bar,observed_at=cutoff)
                    state['input_digest'].update((json.dumps(bar,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode())
                state['cutoff']=cutoff;state['audit']=audit
            result=projection(state['engine'],cutoff,state['provenance'],include_segments)
            result.update(source_mode=mode,source_audit=state['audit'])
            if mode=='live' and cutoff==int(close.timestamp()) and not state.get('closed'):
                self._closing(state['engine'],state['input_digest'].hexdigest())
                state['closed']=True
            return result
        except Exception:
            self.sessions.pop(key,None)
            raise
