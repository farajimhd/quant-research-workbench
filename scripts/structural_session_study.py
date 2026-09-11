"""Bounded full-session structural research with interned as-of level snapshots."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
from bisect import bisect_left
from collections import Counter,deque
from copy import deepcopy
from datetime import datetime,timedelta
from hashlib import sha256
import json

from scripts.audit_structural_baseline import POLICY,NY,stamp,write,evaluate_rows,outcome
from src.backend.replay_run_service import _stream_historical_bar_derived_frames,historical_gateway_base_url
from src.backend.structural_detector_service import GlobalContext
from src.market_engine.historical_source import QmdHistoricalEventSource
from src.market_engine.structural_detector import StructuralDetector,DetectorSettings,VERSION
from scripts.swing_book_paths import validate_runtime_root

WINDOWS=[(t,d,b) for d in ('2026-08-24','2026-08-25') for t,b in
         [('SUGP','structure_book_c58ec63aab23'),('JUNS','structure_book_1bc48fe01de7')]]
LEVEL_FIELDS=('unified_level_id','price','lower','upper','side','prominence','selection_score',
              'created_at_ms','confirmed_at_ms','lifecycle','book_version','scale','timeframes','member_count')
VARIANTS=('baseline','near_origin','positive_net_target','stronger_target')


class LevelCatalog:
    def __init__(self):self.values=[];self.index={}
    def intern(self,level):
        value={k:deepcopy(level[k]) for k in LEVEL_FIELDS if k in level}
        key=json.dumps(value,sort_keys=True,separators=(',',':'))
        if key not in self.index:
            self.index[key]=len(self.values);self.values.append(value)
        return self.index[key]


def expand(data):
    catalog=data['level_catalog']
    return [dict(r,levels=[catalog[i] for i in r['level_refs']],
                 events=[dict(e,level=catalog[e['level_ref']]) for e in r['events']]) for r in data['rows']]


async def collect_session(root,ticker,day,book):
    start=datetime.fromisoformat(day).replace(hour=4,tzinfo=NY);end=start.replace(hour=16)
    spec=dict(schema='structural-session-compact-1',ticker=ticker,session=day,start=start.isoformat(),
              end=end.isoformat(),timeframe='1s',book_id=book,detector=VERSION)
    folder=root/f'{ticker}-{day}-0400-1600';folder.mkdir(exist_ok=True)
    path=folder/'inputs.json'
    if path.exists():
        data=json.loads(path.read_text())
        if data['spec']!=spec:raise ValueError('Cached window/authority identity mismatch')
        return data,folder
    frames=[];authority={}
    async def sink(batch):frames.extend(batch)
    await _stream_historical_bar_derived_frames(ticker=ticker,timeframe='1s',start=start,end=end,
        frame_sink=sink,authority_sink=lambda k,v:authority.update({k:v}),indicator_columns=('bar_start','close'))
    if len(frames)>=50000:raise ValueError('Prepared request may be truncated')
    quotes=[];rejected=Counter()
    source=QmdHistoricalEventSource(historical_gateway_base_url(),start=start-timedelta(seconds=2),
        end=end+timedelta(seconds=30),tickers=[ticker],event_kinds=('quote',))
    async for batch in source.stream_rows():
        for e in batch:
            if 0<e['bid_price']<=e['ask_price']:
                quotes.append(dict(at=stamp(e['ts']),bid=e['bid_price'],ask=e['ask_price'],sequence=e['sequence']))
            else:rejected['invalid_or_crossed_quote']+=1
        if len(quotes)>2000000:raise ValueError('Session quote cap exceeded; no partial study')
    engine=StructuralDetector(DetectorSettings());context=GlobalContext(ticker,book)
    catalog=LevelCatalog();rows=[];counts=deque();shares=dollars=0.;previous_at=None
    for i,f in enumerate(frames):
        b=f.bar;at=stamp(b['bar_end'])
        if previous_at is not None and at<=previous_at:raise ValueError('Nonchronological prepared bars')
        if not start.timestamp()<=stamp(b['bar_start'])<at<=end.timestamp():raise ValueError('Bar outside frozen window')
        previous_at=at
        bar=dict(time=stamp(b['bar_start']),end=at,**{k:b[k] for k in ('open','high','low','close','volume')})
        levels,status=context.at(bar)
        if status!='available':raise ValueError(status)
        if any(l.get('confirmed_at_ms',0)>at*1000 for l in levels):raise ValueError('Future level snapshot')
        prior=engine.last;cursor=context.cursor
        proof=cursor.empty_interval(prior['end'],bar['time']) if prior and hasattr(cursor,'empty_interval') else None
        state=engine.observe(bar,levels,status,continuity=proof)
        shares+=b['volume'];dollars+=b['dollar_volume'];counts.append((at,b['trade_count']))
        while counts and counts[0][0]<=at-60:counts.popleft()
        events=[dict({k:deepcopy(v) for k,v in e.items() if k!='level'},level_ref=catalog.intern(e['level'])) for e in state['global_events']]
        rows.append(dict(at=at,bar=bar,level_refs=[catalog.intern(l) for l in levels],events=events,
            direction=deepcopy(state['direction']),progression=deepcopy(state['progression']),sequence=deepcopy(state['sequence']),
            shares=shares,dollars=dollars,rate10=sum(n for t,n in counts if t>at-10)/10,rate60=sum(n for t,n in counts)/60))
        if i%1000==0:print(f'{ticker} {day}: detector {i}/{len(frames)}; unique level snapshots={len(catalog.values)}',flush=True)
    data=dict(spec=spec,rows=rows,level_catalog=catalog.values,quotes=quotes,rejected=dict(rejected),
              authority=authority,event_revision=source.source_revision,book=context.book)
    write(path,data)
    return data,folder


def variants(data):
    rows=expand(data);quotes=data['quotes'];times=[q['at'] for q in quotes]
    base,_=evaluate_rows(rows,quotes)
    accepted=[s for s in base if s['family']=='breakout_accepted']
    row_by_at={r['at']:r for r in rows};results={}
    for name in VARIANTS:
        samples=[];trades=[];available=0;unresolved=0
        for original in accepted:
            s=deepcopy(original);q=s.get('quote');level=s['level']
            if name=='near_origin' and q:
                max_distance=max(3*(q['ask']-q['bid']),level['upper']-level['lower'])
                if q['ask']-level['upper']>max_distance:s['blocked'].append('extended_from_origin')
            if name=='positive_net_target' and q:
                estimated=(s['target']*(1-POLICY['slippage_bps_per_side']/10000)-q['ask']*(1+POLICY['slippage_bps_per_side']/10000))*POLICY['shares']-2*POLICY['fee_per_order']
                if estimated<=0:s['blocked'].append('target_net_nonpositive')
            if name=='stronger_target':
                candidates=[l['lower'] for l in row_by_at[s['at']]['levels'] if l.get('side') in (-1,'resistance') and
                    l['lower']>max(s['close'],level['upper']) and l.get('selection_score',0)>=level.get('selection_score',0)]
                if candidates:
                    s['target']=min(candidates)
                    # Original room rejection can be resolved only by the changed target;
                    # preserve the stop test and every tradability gate.
                    if q and 0<s['stop']<q['bid'] and s['target']>q['ask']:
                        s['blocked']=[r for r in s['blocked'] if r!='room_or_stop']
                else:s['blocked'].append('stronger_target_unavailable')
            # Positive-net target quality is also enforced on the actual post-latency ask.
            if name=='positive_net_target' and not s['blocked']:
                qi=bisect_left(times,s['at']+POLICY['latency_ms']/1000)
                if qi<len(quotes) and times[qi]-s['at']<=POLICY['quote_age_seconds']:
                    entry=quotes[qi]['ask']*(1+POLICY['slippage_bps_per_side']/10000)
                    net_at_target=(s['target']*(1-POLICY['slippage_bps_per_side']/10000)-entry)*POLICY['shares']-2*POLICY['fee_per_order']
                    if net_at_target<=0:s['blocked'].append('execution_target_net_nonpositive')
            s['outcome']=outcome(s,quotes,times)
            o=s['outcome']
            if not s['blocked'] and s['at']>available:
                if o['status']=='resolved':
                    trades.append(dict(decision_at=s['at'],level=level,stop=s['stop'],target=s['target'],**o));available=o['exit_at']
                elif o['status']=='censored':unresolved+=1;available=o['reserved_until']
                else:available=s['at']+POLICY['quote_age_seconds']
            samples.append(s)
        results[name]=dict(summary=dict(opportunities=len(samples),eligible=sum(not s['blocked'] for s in samples),
            trades=len(trades),net=round(sum(t['net'] for t in trades),2),wins=sum(t['net']>0 for t in trades),
            unresolved=unresolved,blocked=dict(Counter(r for s in samples for r in s['blocked'])),
            exits=dict(Counter(t['reason'] for t in trades))),trades=trades,opportunities=samples)
    return results


async def main(args):
    root=validate_runtime_root(args.runtime);root.mkdir(parents=True,exist_ok=True)
    dependencies={str(p.relative_to(Path(__file__).resolve().parents[1])):sha256(p.read_bytes()).hexdigest()
                  for p in [Path(__file__).resolve(),Path(__file__).parent/'audit_structural_baseline.py',
                            *sorted((Path(__file__).resolve().parents[1]/'src/market_engine').glob('structural_*.py'))]}
    plan=dict(version=1,windows=WINDOWS,hours='04:00-16:00 America/New_York',policy=POLICY,
              variants=VARIANTS,near_origin_max_spreads=3,stronger_target_minimum='origin_selection_score',
              dependencies=dependencies,scope='expanded forward exploration on known tickers; not sealed holdout')
    if (root/'plan.json').exists() and json.loads((root/'plan.json').read_text())!=json.loads(json.dumps(plan)):
        raise ValueError('Frozen study differs; use new runtime root')
    write(root/'plan.json',plan);summary={}
    for ticker,day,book in WINDOWS:
        key=f'{ticker}-{day}';print(f'{key}: active',flush=True)
        try:
            data,folder=await collect_session(root,ticker,day,book)
            result=variants(data)
            for name,value in result.items():write(folder/f'{name}.json',value)
            summary[key]=dict(status='completed',bars=len(data['rows']),quotes=len(data['quotes']),
                input_sha256=sha256((folder/'inputs.json').read_bytes()).hexdigest(),
                variants={k:v['summary'] for k,v in result.items()})
            print(key,', '.join(f'{k}: {v["summary"]["trades"]} trades / ${v["summary"]["net"]:.2f}' for k,v in result.items()),flush=True)
        except Exception as exc:
            summary[key]=dict(status='failed',error=str(exc));print(f'{key}: failed; see summary.json',flush=True)
        write(root/'summary.json',summary)
    lines=['# Full-session structural comparison','',
        'Fixed August24/25, SUGP/JUNS, 04:00–16:00 New York; 30s outcome tail. '
        'Expanded exploration, not a sealed holdout. 100 shares, $1/order, bid/ask and 5bps slippage per side.', '',
        '| Session | Baseline | Near origin | Positive net target | Stronger target |','|---|---:|---:|---:|---:|']
    for key,r in summary.items():
        cells=[f'{r["variants"][k]["trades"]} trades / ${r["variants"][k]["net"]:.2f}' for k in VARIANTS] if r['status']=='completed' else ['failed']*4
        lines.append(f'| {key} | '+' | '.join(cells)+' |')
    lines+=['','Each variant uses independent one-position occupancy. Stop, gates and 30s horizon remain fixed. '
        'Near-origin rejects an ask more than max(3 spreads, level width) above the origin. Positive-net-target '
        'rejects nonpositive modeled profit at the target before and after entry latency. Stronger-target selects '
        'the nearest overhead level with a selection score at least that of the origin. Scores represent book '
        'evidence, not calibrated probabilities. Variants are separate, not combined or optimized.', '',
        'Input schema interns selected immutable level features and retains every supplied level reference. '
        'The detector receives full original as-of levels; book fingerprints and source revisions are retained. '
        'Display prose is omitted from the research projection. Source requests exceeding bounds fail closed.', '',
        'This is quote-based research, not executable fill/capacity proof. Failed/censored opportunities and '
        'blocked reasons remain in per-variant files. No new candidate or live strategy was deployed.']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    failed=sum(r['status']=='failed' for r in summary.values())
    print(f'Finished: completed={len(summary)-failed} failed={failed} active=0 queued=0; {root}',flush=True)
    return 2 if failed else 0


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    raise SystemExit(asyncio.run(main(p.parse_args())))
