"""Trace actual first-blocking decisions between frozen hindsight labels and entry."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

NY=ZoneInfo('America/New_York')


def select_comparison(case,by_run,variant,portfolio=False):
    choices=[]
    for value in case['variants']:
        if value['name']!=variant:continue
        trial=by_run[value['run_id']]
        if (trial['symbol']=='PORTFOLIO')!=portfolio:continue
        choices.append(value)
    return max(choices,key=lambda v:(by_run[v['run_id']]['end'],v['run_id'])) if choices else None


def audit(root,variant,portfolio=False):
    root=root.resolve();root.relative_to(Path('D:/TradingML/runtimes').resolve())
    cases=json.loads((root/'position-comparison.json').read_text())['cases']
    trials=json.loads((root/'comparison.json').read_text())
    by_run={t['run_id']:t for t in trials}
    opportunities={}
    for case in cases:
        if not case.get('opportunity_id'):continue
        key=case['opportunity_id']
        if key not in opportunities:opportunities[key]=dict(case,positions=[])
        opportunities[key]['positions'].append(case['position'])
    output=[];common=Counter()
    for key,case in opportunities.items():
        chosen=select_comparison(case,by_run,variant,portfolio)
        if chosen is None:
            output.append(dict(opportunity_id=key,positions=case['positions'],status='awaiting_replay'))
            continue
        trial=by_run[chosen['run_id']]
        start=datetime.fromtimestamp(float(key.rsplit(':',1)[1]),NY)
        end=datetime.fromisoformat(chosen.get('entry_time') or case['peak_time'])
        path=Path('D:/TradingML/runtimes/trading/backtest')/chosen['run_id']/'journal.sqlite3'
        wal=Path(str(path)+'-wal')
        if wal.exists() and wal.stat().st_size:raise ValueError('Audit requires a closed journal')
        connection=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
        samples=[]
        try:
            for stamp,raw in connection.execute("select event_time,payload_json from journal where category='strategy_decision' order by sequence"):
                at=datetime.fromisoformat(stamp)
                if not start<=at<=end:continue
                decision=json.loads(raw)
                if decision.get('ticker')!=case['symbol']:continue
                if not any(':1s:' in str(s) for s in decision.get('source_signal_ids',[])):continue
                metadata=decision.get('metadata') or {}
                liquidity=metadata.get('liquidity_admission') or {}
                setup=metadata.get('setup_management') or {}
                samples.append(dict(time=at.astimezone(NY).isoformat(),reason=decision['reason'],
                    price=metadata.get('reference_price'),liquidity_failed=liquidity.get('failed',[]),
                    liquidity_facts=liquidity.get('facts'),range=setup.get('range'),
                    reference=metadata.get('historical_hod_reference'),macd=metadata.get('macd'),
                    body=metadata.get('entry_body'),quote_clearance=metadata.get('entry_quote_clearance'),
                    early_base=metadata.get('early_base_assessment'),
                    recovery=metadata.get('setup_recovery')))
        finally:
            connection.close()
        reasons=Counter(s['reason'] for s in samples)
        liquidity=Counter(f for s in samples if s['reason']=='tradability_incomplete' for f in s['liquidity_failed'])
        common.update(reasons)
        output.append(dict(opportunity_id=key,symbol=case['symbol'],positions=case['positions'],
            major_move=case['major_move'],hindsight_time=case['hindsight_time'],
            hindsight_ask=case['hindsight_ask'],peak_bid=case['peak_bid'],
            comparison=chosen,run_id=trial['run_id'],status='audited',
            first_blocking_reasons=dict(reasons),liquidity_failures=dict(liquidity),samples=samples))
        print(f"Processed {len(output)}/{len(opportunities)} opportunities: audited {case['symbol']} {case['hindsight_time']}",flush=True)
    scope='portfolio' if portfolio else 'isolated'
    output_name=variant+('-portfolio' if portfolio else '')
    document=dict(variant=variant,scope=scope,method='Frozen hindsight labels are evaluation anchors only. Longest completed coverage within the selected isolated or shared-capital scope is used. Counts use actual completed-one-second decision records before first overlapping entry or labeled peak. Reasons identify the first blocking branch, not proof that every later branch would pass. Duplicate original positions share one opportunity.',
        common_first_blockers=dict(common.most_common()),opportunities=output)
    (root/f'delay-audit-{output_name}.json').write_text(json.dumps(document,indent=2),encoding='utf-8')
    lines=[f'# {variant} ({scope}): entry delay audit','',document['method'],'',
        '| Positions | Ticker | Hindsight (ET) | Major | Entry delay | Price move used before entry | Leading blockers |',
        '|---|---|---|---|---:|---:|---|']
    for row in output:
        if row['status']!='audited':continue
        comparison=row['comparison'];delay=comparison.get('delay_seconds');fraction=comparison.get('fraction_of_price_move_before_entry')
        reasons=sorted(row['first_blocking_reasons'].items(),key=lambda item:-item[1])[:3]
        lines.append(f"| {','.join(map(str,row['positions']))} | {row['symbol']} | {row['hindsight_time']} | {row['major_move']} | {f'{delay:.0f}s' if delay is not None else 'missed'} | {f'{fraction:.1%}' if fraction is not None else '—'} | {'; '.join(f'{k}: {v}' for k,v in reasons)} |")
    (root/f'delay-audit-{output_name}.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f"Completed {sum(r['status']=='audited' for r in output)}/{len(output)} unique opportunities; remaining await replay coverage",flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,default=Path('D:/TradingML/runtimes/analysis/strategy-222-refinement'))
    parser.add_argument('--variant',default='full-v6')
    parser.add_argument('--portfolio',action='store_true',help='Audit shared-capital runs only; default is isolated tickers')
    args=parser.parse_args();audit(args.runtime,args.variant,args.portfolio)
