"""Build hindsight supervision, screen rules, and replay a bounded finalist set.

Screening is a static feasibility experiment, not a substitute for the stateful
strategy/OMS backtest. Future quotes appear only in labels, never in features.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from collections import Counter
from datetime import datetime
import hashlib
from itertools import product
import json
import sqlite3
import subprocess
import numpy as np

RUNTIME=Path('D:/TradingML/runtimes')
STUDY=RUNTIME/'analysis/strategy-222-parameter-study-20260914'
REPLAYS=RUNTIME/'analysis/strategy-222-refinement'
FEATURES=('price','session_dollars','session_shares','spread_bps','rate10','rate60',
          'body_bps','macd_histogram','fresh_quote','detector_fresh','liquidity_facts_fresh','post_exit','prior_bar_count',
          'prior_range_pct','maximum_gap_seconds','risk_pct')


def flat_entry_state(metadata):
    return metadata.get('status') in ('watching','reentry_cooldown')


def save(path,data):
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data,indent=2,allow_nan=False),encoding='utf-8')
    temporary.replace(path)


def digest(path):
    value=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def causal_features(metadata,current,prior,at):
    """Only a completed current bar, earlier bars, and as-of decision facts."""
    if current['at']!=at or any(b['at']>=at for b in prior):
        raise ValueError('Feature construction crossed the decision clock')
    macd=metadata.get('macd') or {}
    if any(type(macd.get(k)) not in (int,float) for k in ('observed_at','line','signal')):return None
    if float(macd['observed_at'])>at:raise ValueError('Future MACD feature')
    liquidity=metadata.get('liquidity_admission') or {}
    facts=liquidity.get('facts') or {};checks=liquidity.get('checks') or {}
    required=('session_dollar_volume','session_share_volume','spread_bps','trade_rate_10s','trade_rate_60s')
    if any(type(facts.get(k)) not in (int,float) for k in required) or not prior:return None
    price=current['close'];low=min(b['low'] for b in prior);high=max(b['high'] for b in prior)
    stop=low-.01
    if not 0<stop<price or not current['open']>0:return None
    result=dict(price=price,session_dollars=facts['session_dollar_volume'],session_shares=facts['session_share_volume'],
        spread_bps=facts['spread_bps'],rate10=facts['trade_rate_10s'],rate60=facts['trade_rate_60s'],
        body_bps=(price/current['open']-1)*10000,
        macd_histogram=float(macd.get('line',0))-float(macd.get('signal',0)),
        fresh_quote=bool(checks.get('fresh_uncrossed_quote')),detector_fresh=bool(checks.get('detector_fresh')),
        liquidity_facts_fresh=all(checks.get(k) for k in ('market.session_dollar_volume_fresh','market.trade_rate_10s_fresh','market.trade_rate_60s_fresh')),
        post_exit=metadata.get('status')=='reentry_cooldown',
        prior_bar_count=len(prior),prior_range_pct=(high/low-1)*100,
        maximum_gap_seconds=max([0,*[b['at']-a['at']-1 for a,b in zip(prior,prior[1:])]]),
        risk_pct=(price-stop)/price*100)
    if not all(np.isfinite(v) for v in result.values()):return None
    return result,stop


def hindsight_label(quotes,at,stop,*,horizon=300.,latency=.1,quantity=100.,slippage_bps=5.):
    times=quotes[:,0]/1e6
    entry_index=int(np.searchsorted(times,at+latency))
    if entry_index==len(times) or times[entry_index]>at+latency+1:
        return dict(valid=False,reason='entry_quote_unavailable')
    entry=quotes[entry_index]
    if not np.isfinite(entry[:5]).all() or not 0<entry[1]<=entry[2] or min(entry[3:5])<quantity:
        return dict(valid=False,reason='entry_quote_or_depth')
    ask=float(entry[2])*(1+slippage_bps/10000)
    risk=ask-stop
    if risk<=0:return dict(valid=False,reason='invalid_stop')
    target=ask+2*risk
    last=int(np.searchsorted(times,at+horizon,side='right'))
    future=quotes[entry_index:last]
    valid=(future[:,1]>0)&(future[:,2]>=future[:,1])
    hits=np.flatnonzero(valid&((future[:,1]<=stop)|(future[:,1]>=target)))
    complete=bool(times[-1]>=at+horizon)
    if not len(hits) and not complete:return dict(valid=False,reason='right_censored')
    exit_row=future[int(hits[0])] if len(hits) else future[-1]
    if not np.isfinite(exit_row[:5]).all() or not 0<exit_row[1]<=exit_row[2] or exit_row[3]<quantity:
        return dict(valid=False,reason='exit_quote_or_depth')
    bid=float(exit_row[1])*(1-slippage_bps/10000)
    fee=max(1.,.005*quantity)
    net=quantity*(bid-ask)-2*fee
    favorable=float(np.max(future[valid,1]))/ask-1 if valid.any() else 0.
    # A truncated window cannot certify that a small move was not a major move.
    if net>0 and favorable<.10 and not complete:
        return dict(valid=False,reason='major_move_right_censored')
    return dict(valid=True,net=net,profitable=net>0,major_good=net>0 and favorable>=.10,
        entry_ask=ask,exit_bid=bid,exit_at=float(exit_row[0]/1e6),favorable_return=favorable,
        target_hit=bool(len(hits) and exit_row[1]>=target),complete_horizon=complete)


def build(root):
    if (root/'features.json').exists():raise ValueError('Dataset already frozen; use a new runtime directory to expand coverage')
    source=root/'builder-source.py';source.write_bytes(Path(__file__).read_bytes())
    ledger=json.loads((STUDY/'quote-ledger.json').read_text())
    bars=json.loads((STUDY/'bars.json').read_text())
    selected={}
    for path in sorted(REPLAYS.rglob('manifest.json')):
        state=json.loads(path.read_text());identity=state.get('identity',{})
        for trial in state.get('trials',[]):
            if trial['name']!='corrected-baseline' or trial['status']!='completed':continue
            symbol=identity['symbol']
            if symbol=='PORTFOLIO':continue
            if symbol not in selected or identity['end']>selected[symbol][0]:
                selected[symbol]=(identity['end'],trial['run_id'],path)
    rows=[];labels=[];excluded=Counter();inputs={str(source):digest(source)};coverage=[];run_sources={}
    for number,(key,item) in enumerate(ledger.items(),1):
        symbol=item['window']['symbol'];coverage.append(dict(window=key,status='pending'))
        if symbol not in selected:excluded['awaiting_corrected_replay']+=1;continue
        _,run_id,manifest=selected[symbol]
        journal=RUNTIME/'trading/backtest'/run_id/'journal.sqlite3'
        wal=Path(str(journal)+'-wal')
        if wal.exists() and wal.stat().st_size:excluded['awaiting_journal_close']+=1;continue
        if str(journal) not in inputs:inputs[str(journal)]=digest(journal)
        quote_path=STUDY/f'quotes-{key}.npz'
        actual_hash=digest(quote_path)
        if item['status']!='completed' or actual_hash!=item['sha256']:
            raise ValueError(f'Canonical quote provenance mismatch: {key}')
        quotes=np.load(quote_path)['data']
        if len(quotes)==0 or np.any(np.diff(quotes[:,0])<0):raise ValueError('Unordered or empty quotes')
        inputs[str(quote_path)]=actual_hash
        source=json.loads(manifest.read_text())
        run_sources[run_id]=dict(identity=source['identity'],trial=next(t for t in source['trials'] if t['run_id']==run_id))
        series=sorted([b for b in bars[symbol] if b['timeframe']=='1s'],key=lambda b:b['at'])
        times=np.asarray([b['at'] for b in series]);by_time={b['at']:b for b in series}
        start=datetime.fromisoformat(item['window']['start']).timestamp()
        end=datetime.fromisoformat(item['window']['end']).timestamp()
        connection=sqlite3.connect(journal.as_uri()+'?mode=ro&immutable=1',uri=True)
        seen=set();before=len(rows)
        try:
            for sequence,stamp,raw in connection.execute("select sequence,event_time,payload_json from journal where category='strategy_decision' order by sequence"):
                at=datetime.fromisoformat(stamp).timestamp()
                if not start<=at<=end or at in seen:continue
                decision=json.loads(raw)
                if decision.get('ticker')!=symbol or not any(':1s:' in str(s) for s in decision.get('source_signal_ids',[])):continue
                seen.add(at);metadata=decision.get('metadata') or {}
                if not flat_entry_state(metadata):
                    excluded['not_flat_entry_state']+=1;continue
                current=by_time.get(at)
                if current is None:excluded['completed_bar_missing']+=1;continue
                if abs(current['close']-float(metadata.get('reference_price',current['close'])))>1e-6:
                    excluded['bar_decision_price_mismatch']+=1;continue
                prior=series[int(np.searchsorted(times,at-31,side='right')):int(np.searchsorted(times,at))]
                built=causal_features(metadata,current,prior,at)
                if built is None:excluded['causal_features_incomplete']+=1;continue
                features,stop=built
                label=hindsight_label(quotes,at,stop)
                if not label['valid']:excluded[label['reason']]+=1;continue
                row_id=f'{run_id}:{sequence}'
                rows.append(dict(id=row_id,symbol=symbol,window=key,at=at,run_id=run_id,
                    first_blocker=decision['reason'],features=features))
                labels.append(dict(id=row_id,**label))
        finally:connection.close()
        coverage[-1].update(status='built',rows=len(rows)-before,run_id=run_id)
        print(f'Dataset {number}/{len(ledger)} windows: {key}, {len(rows)-before} labeled decisions',flush=True)
    inputs[str(STUDY/'bars.json')]=digest(STUDY/'bars.json')
    inputs[str(STUDY/'quote-ledger.json')]=digest(STUDY/'quote-ledger.json')
    label_opportunities(rows,labels)
    save(root/'features.json',rows);save(root/'labels.json',labels)
    save(root/'dataset-manifest.json',dict(status='screenable',inputs=inputs,run_sources=run_sources,coverage=coverage,excluded=dict(excluded),
        feature_names=FEATURES,rows=len(rows),label_policy=dict(horizon_seconds=300,latency_seconds=.1,
        quantity=100,slippage_bps=5,minimum_fee_per_side=1,target_r=2,major_return=.10),
        limitations='Frozen position-selected development windows. Labels use a prior-observed-low research stop, not a certified executable strategy stop. Full strategy replay and unseen-session validation remain required.'))
    print(f'Dataset complete: {len(rows)} rows; exclusions={dict(excluded)}',flush=True)


def label_opportunities(rows,labels):
    """Future-derived episode identities/quality stay exclusively in labels."""
    groups={}
    for i,(row,label) in enumerate(zip(rows,labels)):
        if label['major_good']:groups.setdefault(row['window'],[]).append(i)
    for window,indices in groups.items():
        episodes=[]
        for i in sorted(indices,key=lambda i:rows[i]['at']):
            if not episodes or rows[i]['at']-rows[episodes[-1][-1]]['at']>60:episodes.append([])
            episodes[-1].append(i)
        for episode in episodes:
            identity=f"{window}:{rows[episode[0]]['at']}"
            best=min(labels[i]['entry_ask'] for i in episode)
            peak=max(labels[i]['entry_ask']*(1+labels[i]['favorable_return']) for i in episode)
            for i in episode:
                labels[i].update(opportunity_id=identity,timing_quality=max(0.,min(1.,(peak-labels[i]['entry_ask'])/(peak-best))))


def metrics(mask,indices,labels,rows):
    chosen=np.flatnonzero(mask&indices);population=np.flatnonzero(indices)
    major_windows={labels[i]['opportunity_id'] for i in population if labels[i]['major_good']}
    first={}
    for i in sorted(chosen,key=lambda i:rows[i]['at']):
        if labels[i]['major_good']:first.setdefault(labels[i]['opportunity_id'],i)
    return dict(admitted_rows=len(chosen),profitable_fraction=float(np.mean([labels[i]['profitable'] for i in chosen])) if len(chosen) else 0.,
        major_opportunity_coverage=len(first)/len(major_windows) if major_windows else 0.,
        entry_timing_quality=float(np.mean([labels[i]['timing_quality'] for i in first.values()])) if first else 0.,
        major_opportunities=len(major_windows),harmful_rows=sum(not labels[i]['profitable'] for i in chosen))


def screen(root,top_k):
    if json.loads((root/'dataset-manifest.json').read_text()).get('status')!='screenable':raise ValueError('Dataset is not accepted for screening')
    plan_path=root/'replay-plan.json'
    if plan_path.exists() and any(c['status']!='pending' for c in json.loads(plan_path.read_text())['commands']):
        raise ValueError('Replay plan already started; preserve it and use a new research directory')
    rows=json.loads((root/'features.json').read_text());labels=json.loads((root/'labels.json').read_text())
    if not rows or [r['id'] for r in rows]!=[r['id'] for r in labels]:raise ValueError('Missing or misaligned supervision')
    x={key:np.asarray([r['features'][key] for r in rows]) for key in FEATURES}
    symbols=sorted({r['symbol'] for r in rows})
    # Fix the split before scoring; never scatter adjacent labels across folds.
    held=set(sorted(symbols,key=lambda s:hashlib.sha256(s.encode()).hexdigest())[:max(1,len(symbols)//4)])
    validation=np.asarray([r['symbol'] in held for r in rows]);training=~validation
    base=(x['fresh_quote']&x['detector_fresh']&x['liquidity_facts_fresh']&(x['macd_histogram']>0)&(x['rate10']>=5))
    results=[]
    for dollars,shares,spread,rate60,body in product((100000,200000,500000,1000000),(10000,25000,50000,100000),(100,150,200),(3,5,10),(5,15,30)):
        mask=base&(x['session_dollars']>=dollars)&(x['session_shares']>=shares)&(x['spread_bps']<=spread)&(x['rate60']>=rate60)&(x['body_bps']>=body)
        train=metrics(mask,training,labels,rows)
        if train['admitted_rows']<10:continue
        # Prefer broad major-move coverage, then profitable feasible decisions.
        score=2*train['major_opportunity_coverage']+train['entry_timing_quality']+train['profitable_fraction']-.0001*train['harmful_rows']
        patch=dict(liquidity_admission=dict(minimum_session_dollar_volume=dollars,minimum_session_share_volume=shares,
            maximum_admission_spread_bps=spread,maximum_current_spread_bps=spread,maximum_spread_bps=spread,
            minimum_current_trade_rate_60s=rate60),historical_hod=dict(setup_minimum_body_bps=body))
        results.append(dict(parameters=patch,training=train,validation=metrics(mask,validation,labels,rows),score=score,
            behavior_hash=hashlib.sha256(np.packbits(mask).tobytes()).hexdigest()))
    results.sort(key=lambda r:(-r['score'],json.dumps(r['parameters'],sort_keys=True)))
    chosen=[];seen=set()
    for result in results:
        signature=result['behavior_hash']
        if signature in seen:continue
        seen.add(signature);chosen.append(result)
        if len(chosen)>=top_k:break
    blockers=Counter(r['first_blocker'] for r,y in zip(rows,labels) if y['major_good'])
    save(root/'screening.json',dict(grid_size=432,ranked=results,finalists=chosen,validation_symbols=sorted(held),
        training_symbols=sorted(set(symbols)-held),major_positive_first_blockers=dict(blockers.most_common()),
        limitations='Static admission feasibility, not trades, win rate, or portfolio profit. The symbol split is a development check on already inspected data, not an untouched holdout. Admission history, certified stops, recovery, order fills and capital competition require full replay.'))
    commands=[]
    for index,result in enumerate(chosen):
        recipe=root/f'recipe-{index+1}.json';save(recipe,result)
        commands.append(dict(rank=index+1,status='pending',recipe_sha256=digest(recipe),argv=replay_argv(root,index+1,symbols)))
    save(root/'replay-plan.json',dict(commands=commands,symbols=symbols,features_sha256=digest(root/'features.json'),
        policy='Sequential finalists only; existing shared QMD lease applies. Draft candidate parameters only; no live activation or arbitrary source edits.'))
    text=['# Supervised Strategy 222 research','',f'{len(rows)} labeled decisions; 432 inexpensive rule combinations; {len(chosen)} finalists.','',
        '**These are feasibility scores, not backtest profits or strategy win rates.**','',
        'Hindsight is stored separately from the causal feature whitelist. Entire symbols are kept together in the development split. Finalists must pass the real strategy, portfolio and OMS replay.','',
        '## Recurring blockers before hindsight-positive major moves','']
    text += [f'- {reason}: {count} decisions' for reason,count in blockers.most_common()]
    text += ['', '## Draft recipes for full replay','',
        '| Recipe | Session dollars | Shares | Spread bps | 60s trade rate | Body bps | Training major coverage | Development major coverage |',
        '|---|---:|---:|---:|---:|---:|---:|---:|']
    for index,result in enumerate(chosen,1):
        p=result['parameters'];l=p['liquidity_admission'];h=p['historical_hod']
        text.append(f"| {index} | {l['minimum_session_dollar_volume']} | {l['minimum_session_share_volume']} | {l['maximum_current_spread_bps']} | {l['minimum_current_trade_rate_60s']} | {h['setup_minimum_body_bps']} | {result['training']['major_opportunity_coverage']:.1%} | {result['validation']['major_opportunity_coverage']:.1%} |")
    text += ['', '## Next step','', 'Run this script with `replay` to create draft parameter candidates and execute the bounded replay plan. The plan resumes completed trials and retains failures. Code defects still require a verified diagnosis and regression test.']
    (root/'report.md').write_text('\n'.join(text)+'\n',encoding='utf-8')
    print(f'Screened 432 rules; planned {len(chosen)} finalist recipes across {len(symbols)} symbols. No live settings changed.',flush=True)


def replay_argv(root,rank,symbols):
    return [sys.executable,str(Path(__file__).with_name('run_strategy_222_refinement.py')),
        '--runtime',str(root/f'finalist-{rank}'),'--position-symbols',*symbols,
        '--recipe-file',str(root/f'recipe-{rank}.json'),'--stop-request-file',str(root/'stop-requested')]


def replay(root):
    path=root/'replay-plan.json';plan=json.loads(path.read_text())
    if json.loads((root/'dataset-manifest.json').read_text()).get('status')!='screenable':raise ValueError('Dataset was invalidated')
    if digest(root/'features.json')!=plan['features_sha256']:raise ValueError('Frozen dataset changed')
    if (root/'stop-requested').exists():raise ValueError('Stop request remains set; inspect the stopped plan before restarting')
    for command in plan['commands']:
        if command['status']=='completed':continue
        rank=command['rank']
        if (type(rank) is not int or not 1<=rank<=5 or command['argv']!=replay_argv(root,rank,plan['symbols'])
                or digest(root/f'recipe-{rank}.json')!=command['recipe_sha256']):
            raise ValueError('Finalist plan changed')
        command['status']='running';save(path,plan)
        print(f"Replaying finalist {command['rank']}/{len(plan['commands'])}",flush=True)
        process=subprocess.Popen(command['argv'],env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'},
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:code=process.wait()
        except KeyboardInterrupt:
            (root/'stop-requested').touch()
            process.wait();command.update(status='stopped',exit_code=process.returncode);save(path,plan);raise
        command.update(status='completed' if code==0 else 'failed',exit_code=code);save(path,plan)
        if code:raise RuntimeError('Finalist failed; evidence retained. Inspect before retrying.')
    assess_replays()


def assess_replays():
    from scripts.summarize_strategy_222_refinement import summarize
    from scripts.assess_strategy_222_refinement import assess
    summarize(REPLAYS)
    assess(REPLAYS)


def diagnostic_screens(rows,labels):
    """Expose opportunity losses from one-factor filters; never export a recipe."""
    if not rows or [r['id'] for r in rows]!=[r['id'] for r in labels]:
        raise ValueError('Missing or misaligned supervision')
    if len({r['id'] for r in rows})!=len(rows):
        raise ValueError('Duplicate supervised decision rows')
    x={key:np.asarray([r['features'][key] for r in rows]) for key in FEATURES}
    base=(x['fresh_quote']&x['detector_fresh']&x['liquidity_facts_fresh']
          &(x['macd_histogram']>0)&(x['rate10']>=5)&(x['rate60']>=3)
          &(x['spread_bps']<=150)&(x['session_dollars']>=200000)
          &(x['session_shares']>=25000)&(x['body_bps']>=30))
    population=np.ones(len(rows),dtype=bool)
    def first_opportunities(mask):
        first={}
        for i in sorted(np.flatnonzero(mask),key=lambda i:rows[i]['at']):
            if labels[i]['major_good']:first.setdefault(labels[i]['opportunity_id'],int(i))
        return first
    reference=first_opportunities(base)
    results=[]
    for feature,thresholds in (('prior_range_pct',(0,1,2,3,5,8)),
                               ('trade_rate_ratio',(0,1,1.5,2,3))):
        for threshold in thresholds:
            condition=(x['prior_range_pct']>=threshold if feature=='prior_range_pct'
                       else x['rate10']>=threshold*x['rate60'])
            mask=base&condition;retained=first_opportunities(mask)
            lost=[];delayed=[]
            for key,i in reference.items():
                detail=dict(opportunity_id=key,symbol=rows[i]['symbol'],
                            reference_at=rows[i]['at'],reference_ask=labels[i]['entry_ask'])
                if key not in retained:lost.append(detail)
                elif rows[retained[key]]['at']>rows[i]['at']:
                    j=retained[key]
                    delayed.append(dict(**detail,filtered_at=rows[j]['at'],
                        filtered_ask=labels[j]['entry_ask'],delay_seconds=rows[j]['at']-rows[i]['at']))
            results.append(dict(feature=feature,minimum=threshold,
                metrics=metrics(mask,population,labels,rows),lost_opportunities=lost,
                delayed_opportunities=delayed,
                by_symbol={s:metrics(mask,np.asarray([r['symbol']==s for r in rows]),labels,rows)
                           for s in sorted({r['symbol'] for r in rows})}))
    return results


def diagnose(root):
    if json.loads((root/'dataset-manifest.json').read_text()).get('status')!='screenable':
        raise ValueError('Dataset is not accepted for screening')
    rows=json.loads((root/'features.json').read_text());labels=json.loads((root/'labels.json').read_text())
    results=diagnostic_screens(rows,labels)
    method=('One-factor diagnostics hold the research liquidity/body screen fixed: '
        '$200,000 session dollars, 25,000 shares, 150bps spread, 5/3 trades per second '
        'over 10/60 seconds, 30bps candle body, fresh inputs and positive MACD histogram. '
        'Future labels only score the causal features. Lost/delayed opportunities are '
        'relative to that unfiltered screen, not to actual strategy entries.')
    limits=('These are labeled decision rows, not trades or a strategy win rate. '
        'Position-selected familiar-session evidence uses research stops. No recipe, '
        'source change, parameter promotion or live activation is produced.')
    save(root/'diagnostics.json',dict(method=method,limitations=limits,
        features_sha256=digest(root/'features.json'),labels_sha256=digest(root/'labels.json'),
        source_sha256=digest(Path(__file__)),results=results))
    lines=['# Hindsight supervision: filter trade-offs','',method,'',limits,'',
        '| Filter | Minimum | Rows | Profitable labels | Major coverage | Lost major opportunities | Delayed major opportunities |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for row in results:
        m=row['metrics']
        lines.append(f"| {row['feature']} | {row['minimum']} | {m['admitted_rows']} | {m['profitable_fraction']:.1%} | {m['major_opportunity_coverage']:.1%} | {len(row['lost_opportunities'])} | {len(row['delayed_opportunities'])} |")
    lines.extend(['','## Lost opportunity details',''])
    for row in results:
        for loss in row['lost_opportunities']:
            lines.append(f"- {row['feature']} >= {row['minimum']}: {loss['symbol']}, opportunity `{loss['opportunity_id']}`.")
    (root/'diagnostics.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'Diagnostics completed: {len(results)} screens, {len(rows)} frozen decision rows; no strategy changes',flush=True)


def completed_close_progress(rows,bars,lookback=60.,maximum_staleness=5.):
    """As-of progress: never substitute the first bar after the reference time."""
    if not np.isfinite(lookback) or lookback<=0 or not np.isfinite(maximum_staleness) or maximum_staleness<0:
        raise ValueError('Invalid progress horizon')
    indexes={}
    for symbol in {r['symbol'] for r in rows}:
        source=[b for b in bars.get(symbol,[]) if b['timeframe']=='1s']
        times=np.asarray([b['at'] for b in source],dtype=float)
        closes=np.asarray([b['close'] for b in source],dtype=float)
        if not np.all(np.isfinite(times)) or np.any(np.diff(times)<=0):
            raise ValueError('Unordered or duplicate completed bars')
        if not np.all(np.isfinite(closes)) or np.any(closes<=0):
            raise ValueError('Invalid completed close')
        indexes[symbol]=(times,closes)
    result=[]
    for row in rows:
        at=row['at'];times,closes=indexes[row['symbol']]
        if not np.isfinite(at):raise ValueError('Invalid decision clock')
        current=int(np.searchsorted(times,at));prior=int(np.searchsorted(times,at-lookback,side='right'))-1
        if current==len(times) or times[current]!=at:
            result.append(dict(valid=False,reason='current_completed_bar_missing'));continue
        if prior<0 or at-lookback-times[prior]>maximum_staleness:
            result.append(dict(valid=False,reason='historical_reference_missing_or_stale'));continue
        result.append(dict(valid=True,reference_at=float(times[prior]),
            reference_age_seconds=float(at-times[prior]),progress_pct=float(100*(closes[current]/closes[prior]-1))))
    return result


def diagnose_trend(root):
    if json.loads((root/'dataset-manifest.json').read_text()).get('status')!='screenable':
        raise ValueError('Dataset is not accepted for screening')
    rows=json.loads((root/'features.json').read_text());labels=json.loads((root/'labels.json').read_text())
    if not rows or [r['id'] for r in rows]!=[r['id'] for r in labels] or len({r['id'] for r in rows})!=len(rows):
        raise ValueError('Missing, duplicate or misaligned supervision')
    bars_path=STUDY/'bars.json'
    progress=completed_close_progress(rows,json.loads(bars_path.read_text()))
    x={key:np.asarray([r['features'][key] for r in rows]) for key in FEATURES}
    base=(x['fresh_quote']&x['detector_fresh']&x['liquidity_facts_fresh']
          &(x['macd_histogram']>0)&(x['rate10']>=5)&(x['rate60']>=3)
          &(x['spread_bps']<=150)&(x['session_dollars']>=200000)
          &(x['session_shares']>=25000)&(x['body_bps']>=5))
    def first(mask):
        found={}
        for i in sorted(np.flatnonzero(mask),key=lambda i:rows[i]['at']):
            if labels[i]['major_good']:found.setdefault(labels[i]['opportunity_id'],int(i))
        return found
    reference=first(base);results=[]
    for cutoff in (0,1,2,3):
        mask=base&np.asarray([p['valid'] and p['progress_pct']>=cutoff for p in progress])
        found=first(mask);lost=[];delayed=[]
        for key,i in reference.items():
            detail=dict(opportunity_id=key,symbol=rows[i]['symbol'],reference_at=rows[i]['at'],
                        reference_ask=labels[i]['entry_ask'])
            if key not in found:lost.append(detail)
            elif rows[found[key]]['at']>rows[i]['at']:
                j=found[key];delayed.append(dict(**detail,delay_seconds=rows[j]['at']-rows[i]['at'],
                    filtered_ask=labels[j]['entry_ask']))
        results.append(dict(minimum_progress_pct=cutoff,
            metrics=metrics(mask,np.ones(len(rows),dtype=bool),labels,rows),lost=lost,delayed=delayed))
    method=('Static decisions, not realized trades. Latest completed close at or before60seconds ago, '
        'reference at most5seconds stale. Fixed screen:200k dollars/25k shares/150bps spread/5-3 trade '
        'rates/5bps body/fresh inputs/positive MACD. Future labels score only; range, recovery, '
        'replacement entries, adds and shared capital still require causal replay. No promotion.')
    save(root/'trend-diagnostics.json',dict(method=method,rows=len(rows),base_rows=int(sum(base)),
        base_major=len(reference),exclusions=dict(Counter(p['reason'] for b,p in zip(base,progress) if b and not p['valid'])),
        source_hashes={str(p):digest(p) for p in (root/'features.json',root/'labels.json',bars_path,Path(__file__))},
        progress=[dict(id=r['id'],**p) for r,p in zip(rows,progress)],results=results))
    for result in results:
        m=result['metrics']
        print(f"Progress>={result['minimum_progress_pct']}%: {m['admitted_rows']} decisions, "
              f"{len(result['lost'])} lost / {len(result['delayed'])} delayed major opportunities",flush=True)
    print(f"Trend diagnosis completed: {len(rows)} decisions; no strategy changes",flush=True)


def diagnose_episode_exit(episode,quotes):
    """Compare an actual episode with an explicitly separate fixed-size label."""
    first=episode['fills'][0]
    if first['side']!='B':raise ValueError('Episode must start with an actual buy')
    stop=first.get('stop')
    if type(stop) not in (int,float) or not np.isfinite(stop) or stop<=0:
        return dict(valid=False,reason='initial_stop_metadata_unavailable')
    at=datetime.fromisoformat(episode['opened_at']).timestamp()
    label=hindsight_label(quotes,at,float(stop))
    actual_net=episode.get('net')
    return dict(**label,initial_stop=stop,actual_net=actual_net,
        actual_exit_reasons=sorted({f['reason'] for f in episode['fills']
            if f['side']=='S' and f.get('reason')}),
        management_or_sizing_review=bool(label.get('valid') and label.get('profitable')
            and actual_net is not None and actual_net<=0))


def diagnose_exits(root,variant):
    """Audit all covered actual entries; retain missing-quote and open cases."""
    from scripts.summarize_strategy_222_refinement import episodes
    comparison=REPLAYS/'comparison.json'
    trials=json.loads(comparison.read_text());chosen={}
    for trial in trials:
        if trial['name']!=variant or trial['symbol']=='PORTFOLIO':continue
        prior=chosen.get(trial['symbol'])
        if prior is None or (trial['end'],trial['run_id'])>(prior['end'],prior['run_id']):
            chosen[trial['symbol']]=trial
    if not chosen:raise ValueError('No completed isolated replay for this variant; refresh comparison first')
    ledger=json.loads((STUDY/'quote-ledger.json').read_text())
    quote_cache={};quote_hashes={};journal_hashes={};rows=[]
    for index,(symbol,trial) in enumerate(sorted(chosen.items()),1):
        journal=RUNTIME/'trading'/'backtest'/trial['run_id']/'journal.sqlite3'
        actual=episodes(journal)['episodes']  # Rejects journals with a live WAL.
        journal_hashes[trial['run_id']]=digest(journal)
        for number,episode in enumerate(actual,1):
            at=datetime.fromisoformat(episode['opened_at'])
            windows=[(key,value) for key,value in ledger.items()
                if value['status']=='completed' and value['window']['symbol']==symbol
                and datetime.fromisoformat(value['window']['start'])<=at<=datetime.fromisoformat(value['window']['end'])]
            row=dict(symbol=symbol,run_id=trial['run_id'],episode=number,
                opened_at=episode['opened_at'],entry_price=episode['entry_price'],actual_net=episode.get('net'))
            if not windows:
                row.update(valid=False,reason='outside_frozen_quote_windows')
            else:
                key,_=max(windows,key=lambda item:(item[1]['window']['end'],item[0]))
                if key not in quote_cache:
                    path=STUDY/f'quotes-{key}.npz'
                    actual_hash=digest(path)
                    if actual_hash!=ledger[key]['sha256']:
                        raise ValueError(f'Canonical quote provenance mismatch: {key}')
                    with np.load(path) as archive:quote_cache[key]=archive['data']
                    if len(quote_cache[key])==0 or np.any(np.diff(quote_cache[key][:,0])<0):
                        raise ValueError(f'Unordered or empty quotes: {key}')
                    quote_hashes[key]=actual_hash
                row.update(quote_window=key,**diagnose_episode_exit(episode,quote_cache[key]))
            rows.append(row)
        print(f'Exit diagnosis {index}/{len(chosen)} tickers completed; {len(rows)} episodes accounted for',flush=True)
    exclusions=Counter(r['reason'] for r in rows if not r['valid'])
    flagged=[r for r in rows if r.get('management_or_sizing_review')]
    reasons=Counter(reason for r in flagged for reason in r['actual_exit_reasons'])
    method=('Completed isolated replay episodes, longest horizon per ticker. Initial stop comes from '
        'the first actual buy metadata. The comparison uses a separate 100-share entry at the next '
        'quote after fill time plus100ms, 5bps per side, fees/depth checks and a fixed initial-stop/2R '
        'barrier within300seconds. Actual sizing, adds and management differ. A flagged loss requires '
        'management/sizing review; it does not prove an exit defect or predict counterfactual profit. '
        'Open episodes and missing/censored quote coverage are retained. No strategy changes are made.')
    save(root/'exit-diagnostics.json',dict(variant=variant,method=method,
        comparison_sha256=digest(comparison),source_sha256=digest(Path(__file__)),
        journal_sha256=journal_hashes,quote_sha256=quote_hashes,tickers=len(chosen),
        episodes=len(rows),open_episodes=sum(r['actual_net'] is None for r in rows),
        valid_labels=sum(r['valid'] for r in rows),exclusions=dict(exclusions),
        management_or_sizing_reviews=len(flagged),review_exit_reasons=dict(reasons),rows=rows))
    lines=[f'# {variant}: actual entry and exit diagnosis','',method,'',
        f'{len(rows)} episodes; {sum(r["valid"] for r in rows)} valid fixed-stop labels; {len(flagged)} management/sizing reviews.','',
        '| Ticker | Actual entry | Actual net | Fixed-size label net | Exit reasons |',
        '|---|---|---:|---:|---|']
    for row in flagged:
        lines.append(f"| {row['symbol']} | {row['opened_at']} | {row['actual_net']:.2f} | {row['net']:.2f} | {', '.join(row['actual_exit_reasons'])} |")
    lines.extend(['','## Explicit exclusions','',*[f'- {reason}: {count}' for reason,count in sorted(exclusions.items())]])
    (root/'exit-diagnostics.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('build','screen','replay','assess','diagnose','diagnose-exits','diagnose-trend'))
    parser.add_argument('--runtime',type=Path,default=REPLAYS/'supervised-v1')
    parser.add_argument('--top-k',type=int,default=2)
    parser.add_argument('--variant',help='Completed isolated replay variant for diagnose-exits')
    args=parser.parse_args();root=args.runtime.resolve();root.relative_to(RUNTIME.resolve())
    if not 1<=args.top_k<=5:parser.error('--top-k must be between 1 and 5')
    if args.action=='diagnose-exits' and not args.variant:parser.error('diagnose-exits requires --variant')
    root.mkdir(parents=True,exist_ok=True)
    if args.action=='build':build(root)
    elif args.action=='screen':screen(root,args.top_k)
    elif args.action=='replay':replay(root)
    elif args.action=='diagnose':diagnose(root)
    elif args.action=='diagnose-trend':diagnose_trend(root)
    elif args.action=='diagnose-exits':diagnose_exits(root,args.variant)
    else:assess_replays()


if __name__=='__main__':main()
