"""Exploratory causal sequence study and Candidate 184 execution audit.

Not a strategy release: overlapping opportunities are reported separately from
one-position-per-family hypothetical trades. No held-out evaluation is claimed.
"""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
from bisect import bisect_left, bisect_right
from collections import Counter, deque
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json
import sqlite3
from zoneinfo import ZoneInfo

from src.backend.replay_run_service import (_stream_historical_bar_derived_frames,
    _STRATEGY_INDICATOR_FIELDS, historical_gateway_base_url)
from src.backend.structural_detector_service import GlobalContext
from src.market_engine.historical_source import QmdHistoricalEventSource
from src.market_engine.structural_detector import StructuralDetector, DetectorSettings, VERSION
from scripts.swing_book_paths import validate_runtime_root

NY = ZoneInfo('America/New_York')
RUN = 'fe44a57f-7508-4386-afbf-59f405305ec6'
POLICY = dict(horizon_seconds=30, latency_ms=100, quote_age_seconds=1,
    maximum_spread_bps=100, minimum_dollars=1e6, minimum_shares=100000,
    minimum_trade_rate=5, tick=.01, shares=100, fee_per_order=1,
    slippage_bps_per_side=5, repeat_level_seconds=30)


def stamp(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()


def write(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, separators=(',',':'), allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def outcome(sample, quotes, times, policy=POLICY):
    """Future quotes are outcomes only; decision features are already frozen."""
    i = bisect_left(times, sample['at']+policy['latency_ms']/1000)
    end = sample['at']+policy['horizon_seconds']
    if i == len(times) or times[i]-sample['at'] > policy['quote_age_seconds']:
        return dict(status='no_timely_entry_quote')
    entry = quotes[i]['ask']*(1+policy['slippage_bps_per_side']/10000)
    if not sample['stop'] < quotes[i]['bid'] or sample['target'] <= entry:
        return dict(status='invalid_at_execution')
    j = bisect_right(times,end)
    future = quotes[i:j]
    horizon_observed = bool(future and end-times[j-1] <= policy['quote_age_seconds'])
    hit = next((q for q in future if q['bid'] <= sample['stop'] or q['bid'] >= sample['target']), None)
    if not horizon_observed and hit is None:
        return dict(status='censored',entry_at=times[i],reserved_until=end)
    exit_quote = hit or future[-1]
    exit_price = exit_quote['bid']*(1-policy['slippage_bps_per_side']/10000)
    return dict(status='resolved',entry=entry,entry_at=times[i],exit_at=exit_quote['at'],
        reason=('stop' if hit['bid'] <= sample['stop'] else 'target') if hit else 'horizon',
        net=(exit_price-entry)*policy['shares']-2*policy['fee_per_order'],
        horizon_observed=horizon_observed,
        mfe_bps=(max(q['bid'] for q in future)/entry-1)*10000 if horizon_observed else None,
        mae_bps=(min(q['bid'] for q in future)/entry-1)*10000 if horizon_observed else None,
        horizon_return_bps=(future[-1]['bid']/entry-1)*10000 if horizon_observed else None)


def evaluate_rows(rows, quotes):
    times=[q['at'] for q in quotes]; samples=[]; last={}; previous=deque(maxlen=3)
    if any(a > b for a,b in zip(times,times[1:])):
        raise ValueError('Quotes must be in event order')
    if any(a['at'] >= b['at'] for a,b in zip(rows,rows[1:])):
        raise ValueError('Decision candles must be strictly chronological')
    for row in rows:
        at=row['at'];close=row['bar']['close']
        evidence=row['levels']+[event['level'] for event in row['events']]
        if any(level.get('confirmed_at_ms',0)>at*1000 for level in evidence):
            raise ValueError('Future level confirmation at decision time')
        candidates=[]
        for event in row['events']:
            if event['state'] in ('breakout','breakout_accepted'):
                candidates.append((event['state'],event['level']))
        # Fixed anticipatory baseline, not a fitted threshold: 3 rising closes
        # approaching a known resistance within 10 bps, still below its top.
        if len(previous)==3 and previous[0] < previous[1] < previous[2] < close:
            nearby=[l for l in row['levels'] if l.get('side') in (-1,'resistance')
                    and close <= l['upper'] <= close*1.001]
            if nearby:candidates.append(('prebreak_pressure',min(nearby,key=lambda l:l['upper'])))
        previous.append(close)
        for family,level in candidates:
            key=(family,str(level.get('unified_level_id') or (level['lower'],level['upper'])))
            if at-last.get(key,0)<POLICY['repeat_level_seconds']:continue
            last[key]=at
            s=dict(family=family,at=at,level=level,regime=row['direction'],progression=row['progression'],
                   sequence=row['sequence'],close=close,stop=level['lower']-POLICY['tick'],target=0)
            qi=bisect_right(times,at)-1
            overhead=[l['lower'] for l in row['levels'] if l.get('side') in (-1,'resistance')
                      and l['lower']>max(close,level['upper'])]
            if overhead:s['target']=min(overhead)
            reasons=[]
            if row['shares']<POLICY['minimum_shares'] or row['dollars']<POLICY['minimum_dollars']:reasons.append('session_volume')
            if min(row['rate10'],row['rate60'])<POLICY['minimum_trade_rate']:reasons.append('activity')
            if qi<0 or at-times[qi]>POLICY['quote_age_seconds']:reasons.append('quote_unavailable')
            else:
                q=quotes[qi];s['quote']=q
                if 2*(q['ask']-q['bid'])/(q['ask']+q['bid'])*10000>POLICY['maximum_spread_bps']:reasons.append('spread')
                if not 0<s['stop']<q['bid'] or s['target']<=q['ask']:reasons.append('room_or_stop')
            s['blocked']=reasons
            # Keep counterfactual outcomes for rejected samples too, where executable.
            s['outcome']=outcome(s,quotes,times)
            samples.append(s)
    summary={}
    for family in ('prebreak_pressure','breakout','breakout_accepted'):
        selected=[s for s in samples if s['family']==family];trades=[];available=0;unresolved=0
        for s in selected:
            o=s['outcome']
            if not s['blocked'] and s['at']>available:
                if o['status']=='resolved':
                    trades.append(o);available=o['exit_at']
                elif o['status']=='censored':
                    # An unresolved open position still blocks later entries.
                    unresolved+=1;available=o['reserved_until']
                else:
                    available=s['at']+POLICY['quote_age_seconds']
        summary[family]=dict(opportunities=len(selected),eligible=sum(not s['blocked'] for s in selected),
            blocked=dict(Counter(x for s in selected for x in s['blocked'])),trades=len(trades),
            net=round(sum(t['net'] for t in trades),2),wins=sum(t['net']>0 for t in trades),unresolved_positions=unresolved,
            exits=dict(Counter(t['reason'] for t in trades)),outcome_status=dict(Counter(s['outcome']['status'] for s in selected)))
    return samples,summary


async def collect(root,ticker,session,hour,book):
    key=f'{ticker}-{session}';folder=root/key;folder.mkdir(exist_ok=True)
    start=datetime.fromisoformat(session).replace(hour=hour,tzinfo=NY);end=start+timedelta(minutes=30)
    if (folder/'inputs.json').exists():
        data=json.loads((folder/'inputs.json').read_text())
        if (data['ticker'],data['session'],data['book']['id'],data['detector']) != (ticker,session,book,VERSION):
            raise ValueError('Input cache identity/version differs; use a new runtime directory')
        return data
    frames=[];authority={}
    async def sink(batch):frames.extend(batch)
    await _stream_historical_bar_derived_frames(ticker=ticker,timeframe='1s',
        start=start.replace(hour=4),end=end,frame_sink=sink,
        authority_sink=lambda k,v:authority.update({k:v}),
        indicator_columns=tuple(k for k in _STRATEGY_INDICATOR_FIELDS if not k.startswith(('qmd_structure_','structure_','flow_structure_'))))
    quotes=[]
    source=QmdHistoricalEventSource(historical_gateway_base_url(),start=start-timedelta(seconds=2),
        end=end+timedelta(seconds=30),tickers=[ticker],event_kinds=('quote',))
    async for batch in source.stream_rows():
        for e in batch:
            if 0<e['bid_price']<=e['ask_price']:
                quotes.append(dict(at=stamp(e['ts']),bid=e['bid_price'],ask=e['ask_price'],sequence=e['sequence']))
    context=GlobalContext(ticker,book);engine=StructuralDetector(DetectorSettings());rows=[]
    dollars=shares=0.;counts=deque()
    for i,f in enumerate(frames):
        b=f.bar;at=stamp(b['bar_end']);bar=dict(time=stamp(b['bar_start']),end=at,
            **{k:b[k] for k in ('open','high','low','close','volume')})
        levels,status=context.at(bar)
        if status!='available':raise RuntimeError(status)
        prior=engine.last;cursor=context.cursor
        proof=cursor.empty_interval(prior['end'],bar['time']) if prior and hasattr(cursor,'empty_interval') else None
        row=engine.observe(bar,levels,status,continuity=proof)
        shares+=b['volume'];dollars+=b['dollar_volume'];counts.append((at,b['trade_count']))
        while counts and counts[0][0]<=at-60:counts.popleft()
        if at>=start.timestamp():
            rows.append(deepcopy(dict(at=at,bar=bar,levels=levels,events=row['global_events'],
                sequence=row['sequence'],direction=row['direction'],progression=row['progression'],
                shares=shares,dollars=dollars,rate10=sum(n for t,n in counts if t>at-10)/10,
                rate60=sum(n for t,n in counts)/60)))
        if i%1000==0:print(f'{key}: detector {i}/{len(frames)}',flush=True)
    data=dict(ticker=ticker,session=session,rows=rows,quotes=quotes,authority=authority,
        event_revision=source.source_revision,book=context.book,detector=VERSION)
    write(folder/'inputs.json',data);return data


def audit_run(root,quotes):
    path=Path('D:/TradingML/runtimes/trading/backtest')/RUN/'journal.sqlite3'
    c=sqlite3.connect(f'file:{path.as_posix()}?mode=ro&immutable=1',uri=True)
    fills=[dict(json.loads(r[0]),event_at=stamp(r[1])) for r in c.execute("SELECT payload_json,event_time FROM journal WHERE category='execution' ORDER BY event_time,sequence")]
    c.close();times=[q['at'] for q in quotes];audit=[]
    for fi,f in enumerate(fills):
        if f['side']!='B':continue
        m=f['canonical_metadata'];at=stamp(m['decision_event_time']);i=bisect_right(times,at)-1
        q=quotes[i] if i>=0 else None
        exits=[]
        for later in fills[fi+1:]:
            if later['side']=='B':break
            exits.append(later)
        exit_at=exits[-1]['event_at'] if exits else None
        ei=bisect_right(times,exit_at)-1 if exit_at else -1
        first_cross=next((q for q in quotes[i+1:ei+1] if q['bid']<=m['active_stop']),None)
        audit.append(dict(at=at,fill=f['price'],decision_bid=m['bid'],decision_ask=m['ask'],
            stop=m['active_stop'],canonical_quote=q,
            stop_distance_from_bid=m['bid']-m['active_stop'],
            duration_seconds=exit_at-f['event_at'] if exit_at else None,
            exit_roles=[e['canonical_metadata']['execution_role'] for e in exits],
            first_stop_cross_quote=first_cross,exit_quote=quotes[ei] if ei>=0 else None,
            decision_quote_matches=bool(q and abs(q['bid']-m['bid'])<1e-8 and abs(q['ask']-m['ask'])<1e-8),
            stop_already_crossed=bool(q and q['bid']<=m['active_stop'])))
    write(root/'execution-audit.json',dict(run_id=RUN,entries=audit))
    return dict(entries=len(audit),quote_mismatches=sum(not a['decision_quote_matches'] for a in audit),
                stop_already_crossed=sum(a['stop_already_crossed'] for a in audit))


async def main(root, cached_only=False):
    root=validate_runtime_root(root);root.mkdir(parents=True,exist_ok=True)
    plan=dict(version=1,policy=POLICY,scope='exploratory development; not a holdout',windows=[
        [t,d,h,b] for d in ('2026-08-20','2026-08-21') for t,h,b in (
            ('SUGP',4,'structure_book_c58ec63aab23'),('JUNS',7,'structure_book_1bc48fe01de7'))])
    plan['source_sha256']=sha256(Path(__file__).read_bytes()).hexdigest()
    write(root/'study-plan.json',plan);results={}
    prior_results=json.loads((root/'summary.json').read_text()) if (root/'summary.json').exists() else {}
    for ticker,session,hour,book in plan['windows']:
        key=f'{ticker}-{session}';print(f'{key}: collecting',flush=True)
        try:
            if cached_only and not (root/key/'inputs.json').exists():
                raise RuntimeError(prior_results.get(key,{}).get('error') or 'No certified input cache; rerun without --cached-only after source certification is repaired')
            data=await collect(root,ticker,session,hour,book)
        except Exception as exc:
            results[key]=dict(status='source_failed',error=str(exc))
            write(root/'summary.json',results)
            print(f'{key}: source unavailable; details in summary.json; retry after source repair',flush=True)
            continue
        samples,summary=evaluate_rows(data['rows'],data['quotes'])
        summary['input_sha256']=sha256((root/key/'inputs.json').read_bytes()).hexdigest()
        write(root/key/'opportunities.json',samples);results[key]=summary
        if ticker=='SUGP' and session=='2026-08-21':results['execution_audit']=audit_run(root,data['quotes'])
        write(root/'summary.json',results)
        print(f'{key}: completed; '+', '.join(f'{name} {summary[name]["trades"]} trades / ${summary[name]["net"]:.2f}'
            for name in ('prebreak_pressure','breakout','breakout_accepted')),flush=True)
    failed=sum(r.get('status')=='source_failed' for r in results.values())
    report(root,results,plan)
    print(f'Finished: completed={len(plan["windows"])-failed} failed={failed} queued=0 active=0; {root}',flush=True)
    return 2 if failed else 0


def report(root,results,plan):
    lines=['# Structural baseline audit', '',
        'Exploratory research; no new candidate or execution rules were deployed.', '',
        '## Completed comparison', '',
        'August 21, 2026: SUGP 04:00–04:30 and JUNS 07:00–07:30 New York time.', '',
        '| Family | SUGP trades / net | JUNS trades / net | Total trades / net |',
        '|---|---:|---:|---:|']
    for family in ('prebreak_pressure','breakout','breakout_accepted'):
        cells=[];count=0;net=0
        for ticker in ('SUGP','JUNS'):
            value=results.get(f'{ticker}-2026-08-21',{}).get(family)
            if value is None:cells.append('unavailable');continue
            cells.append(f'{value["trades"]} / ${value["net"]:.2f}')
            count+=value['trades'];net+=value['net']
        lines.append(f'| {family} | {cells[0]} | {cells[1]} | {count} / ${net:.2f} |')
    lines+=['', 'Net is hypothetical P&L for 100 shares, $1 per order, 5 bps slippage per side, '
        'buying at ask and selling at bid. Entry uses the first quote at least 100 ms after the decision, '
        'within one second. Each family runs independently with at most one open position. '
        'Stop is one cent below the originating level; target is the nearest currently known overhead resistance; '
        'otherwise exit at the 30-second observation horizon. No target means no entry. '
        'This is a fixed research comparator, not the proposed production position manager.', '',
        '## Execution audit', '']
    if 'execution_audit' in results:
        audit=json.loads((root/'execution-audit.json').read_text())
        entries=audit['entries'];fast=[e for e in entries if e['duration_seconds'] is not None
            and e['duration_seconds']<.04 and 'protective_stop' in e['exit_roles']]
        lines += [f'Run `{RUN}`: {len(entries)} entries, '
            f'{sum(not e["decision_quote_matches"] for e in entries)} entry-quote mismatches, '
            f'{sum(e["stop_already_crossed"] for e in entries)} stops already crossed at entry.', '',
            f'{len(fast)} protective exits occurred within 40 ms. Details below use the canonical quote stream; '
            'this checks initial stops, not the full subsequent order-replacement lifecycle.', '',
            '| Entry time (NY) | Bid | Initial stop | Exit delay (ms) | First crossing bid |',
            '|---|---:|---:|---:|---:|']
        for e in fast:
            crossing=e['first_stop_cross_quote']
            lines.append(f'| {datetime.fromtimestamp(e["at"],NY).isoformat()} | {e["decision_bid"]:.2f} | '
                f'{e["stop"]:.2f} | {e["duration_seconds"]*1000:.3f} | '
                f'{crossing["bid"] if crossing else "not observed"} |')
    lines += ['', '## Interpretation and next design', '',
        'The stale-entry-quote hypothesis was not supported in this run. Repeated entries very near the same '
        'stop exposed the position to ordinary bid changes. A price-above-level condition alone does not '
        'establish a new opportunity after a stop-out.', '',
        'Accepted breakouts are the most promising of these three fixed baselines on the completed windows. '
        'They still require evaluation on additional dates and held-out tickers; the comparison is too small '
        'and previously examined to establish an edge. Early pressure was weaker here; do not promote an early '
        'entry merely to match a visually attractive chart.', '',
        'Proposed next executable contract:', '',
        '1. Build a causal encounter state for each important level: approach, break, acceptance, retest, failure. '
        'Use completed candles and levels confirmed by the decision timestamp.',
        '2. Preserve tradability gates. Require overhead room after spread, commissions and slippage; '
        'place the initial stop at structural invalidation and reject entries whose required stop is too close '
        'to quote noise. Freeze and log the entry thesis and relevant level IDs.',
        '3. While holding, distinguish a contained pullback from acceptance back below the broken level. '
        'Use a protective stop throughout; tighten only after a new confirmed higher low. '
        'Test partial profit-taking and runner management separately from entry selection.',
        '4. After a stop-out, require a new reclaim/acceptance or a fresh confirmed breakout before reentry. '
        'Do not repeatedly execute the same persistent price-above-level condition.',
        '5. Compare each change against this frozen baseline on untouched dates/tickers before releasing a candidate.',
        '', '## Causality, scope and limitations', '',
        '- Detector: completed 1s candles from 04:00 session start; V6 cursor snapshots are obtained as of each '
        'candle end. Future quotes are used only for outcome accounting. This is not a 100ms MACD strategy.',
        '- Gates: $1M session dollar volume, 100k shares, at least 5 trades/s over both 10s and 60s, '
        'quote age at most 1s, spread at most 100 bps. Displayed depth, borrow, market impact, queue priority '
        'and partial fills are not modeled; these results are not broker fill guarantees.',
        '- Prebreak pressure requires four increasing observed closes within 10 bps below a resistance. '
        'It warms those closes within the evaluation window; empty seconds are not fabricated. '
        'Regime/sequence fields are captured, but no fitted regime filter is used in this baseline.',
        '- Each family suppresses repeats at the same level for 30s. Selected unresolved positions remain '
        'occupied to their horizon; missing horizon data does not discard a stop/target already observed.',
        '- Input provenance, book fingerprint and detector version are in each inputs.json. Input hashes '
        'are in summary.json; source hash and assumptions are in study-plan.json. Cached inputs are reused.',
        '- No parameter search, predictive model, position-management optimization, full detector-prefix '
        'replay equivalence proof, or untouched holdout evaluation was performed.', '',
        '## Blocked source windows', '']
    for key,value in results.items():
        if value.get('status')=='source_failed':
            lines.append(f'- {key}: {value["error"]}')
    lines += ['', 'Repair historical execution-clock certification before rerunning missing windows. '
        'The study fails closed; it does not use retained SIP flatfiles or bypass certification.', '',
        '## Reproduce', '', 'From the repository, using its Python environment:', '', '```powershell',
        "$env:PYTHONDONTWRITEBYTECODE='1'",
        'python -B scripts/audit_structural_baseline.py --runtime D:/TradingML/runtimes/research/structural-baseline-20260911 --cached-only',
        '```', '', 'Omit `--cached-only` to retry missing source windows; completed input caches are retained. '
        'A partial study returns a nonzero exit status. This command is for the laptop runtime used in this audit.', '',
        f'Source SHA256: `{plan["source_sha256"]}`.']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--cached-only',action='store_true',help='Re-evaluate captured inputs without contacting historical services')
    args=p.parse_args()
    raise SystemExit(asyncio.run(main(args.runtime,args.cached_only)))
