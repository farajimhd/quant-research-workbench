"""Summarize closed, immutable replay journals, retaining every fill and episode."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
import argparse
import json
from pathlib import Path
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo


def episodes(path):
    wal=Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError(f'Journal still has a WAL; wait for the writer to close: {path}')
    connection=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
    result=[];active_by_symbol={};effective_stops={}
    try:
        for sequence,stamp,category,raw in connection.execute("select sequence,event_time,category,payload_json from journal where (category='execution' and entity_type='fill') or category='protection' order by sequence"):
            fill=json.loads(raw)
            if category=='protection':
                if fill.get('kind')=='stop' and fill.get('phase')=='effective' and fill.get('active'):
                    for key in ('order_id','client_order_id'):
                        if fill.get(key):
                            effective_stops[(fill.get('ticker'),str(fill[key]))]=dict(price=fill['price'],sequence=sequence,time=stamp)
                continue
            quantity=float(fill['size']);price=float(fill['price'])
            symbol=fill['symbol']
            active=active_by_symbol.get(symbol)
            buy=fill['side']=='B'
            if fill['side'] not in ('B','S'):
                raise ValueError('Unknown fill side')
            if active is None:
                if not buy:raise ValueError('Exit without entry')
                active=dict(symbol=symbol,opened_at=stamp,entry_price=price,quantity=0.,buy=0.,sell=0.,fees=0.,fills=[])
                active_by_symbol[symbol]=active
                result.append(active)
            active['quantity']+=quantity if buy else -quantity
            active['buy' if buy else 'sell']+=quantity*price
            active['fees']+=float(fill['commission'])
            metadata=fill.get('canonical_metadata') or {}
            # Entry metadata can retain the original stop after replacements.
            # Match only this order's preceding effective protection record.
            effective=effective_stops.get((symbol,str(fill.get('order_ref')))) or effective_stops.get((symbol,str(fill.get('order_id'))))
            active['fills'].append(dict(sequence=sequence,time=stamp,side=fill['side'],quantity=quantity,
                price=price,fee=fill['commission'],reason=metadata.get('reason'),stop=metadata.get('active_stop'),
                stop_reference='frozen_order_metadata',effective_order_stop=effective))
            if active['quantity'] < -1e-8:raise ValueError('Oversold position')
            if abs(active['quantity'])<1e-8:
                active.update(closed_at=stamp,net=active['sell']-active['buy']-active['fees'])
                del active_by_symbol[symbol]
        reasons=connection.execute("select json_extract(payload_json,'$.reason'),count(*) from journal where category='strategy_decision' group by 1 order by 2 desc").fetchall()
        return dict(episodes=result,decision_reasons=dict(reasons))
    finally:
        connection.close()


def summarize(root):
    root=root.resolve();root.relative_to(Path('D:/TradingML/runtimes').resolve())
    trials=[];pending=[]
    for manifest in sorted(root.rglob('manifest.json')):
        state=json.loads(manifest.read_text())
        for trial in state['trials']:
            if trial['status']!='completed':continue
            directory=Path('D:/TradingML/runtimes/trading/backtest')/trial['run_id']
            wal=directory/'journal.sqlite3-wal'
            if wal.exists() and wal.stat().st_size:
                pending.append(trial['run_id'])
                print(f"Awaiting journal close: {trial['run_id']}; excluded from current coverage",flush=True)
                continue
            data=episodes(directory/'journal.sqlite3')
            closed=[e for e in data['episodes'] if 'closed_at' in e]
            row=dict(**trial,symbol=state['identity']['symbol'],end=state['identity']['end'],
                tickers=state['identity'].get('tickers',[state['identity']['symbol']]),
                experiment=str(manifest.parent.relative_to(root)),**data,
                closed_count=len(closed),open_count=len(data['episodes'])-len(closed),
                wins=sum(e['net']>0 for e in closed),net_closed=sum(e['net'] for e in closed))
            trials.append(row)
    (root/'comparison.json').write_text(json.dumps(trials,indent=2),encoding='utf-8')
    (root/'comparison-pending.json').write_text(json.dumps(dict(awaiting_journal_close=pending),indent=2),encoding='utf-8')
    lines=['# Strategy 222 replay refinement','',
        'Same-day development evidence; hindsight selects evaluation windows only. Each run starts at 04:00 with $10,000. Single-symbol rows exclude portfolio competition; PORTFOLIO rows share capital across their listed tickers. Do not sum isolated rows as portfolio profit. Net includes simulated fill commissions; open positions are excluded from closed net.', '',
        '| Experiment | Symbol | Variant | Entries | Closed wins | Open | Closed net | First entry (ET) |',
        '|---|---|---|---:|---:|---:|---:|---|']
    if pending:lines.insert(4,f"{len(pending)} terminal runs still have an open journal and are excluded until the writer closes.\n")
    for row in trials:
        first=row['episodes'][0] if row['episodes'] else None
        stamp=datetime.fromisoformat(first['opened_at']).astimezone(ZoneInfo('America/New_York')).strftime('%H:%M:%S') if first else 'none'
        lines.append(f"| {row['experiment']} | {row['symbol']} | {row['name']} | {len(row['episodes'])} | {row['wins']}/{row['closed_count']} | {row['open_count']} | ${row['net_closed']:.2f} | {stamp} |")
        print(row['symbol'],row['name'],f"entries={len(row['episodes'])} wins={row['wins']}/{row['closed_count']} open={row['open_count']} net=${row['net_closed']:.2f} first={stamp}",flush=True)
    (root/'comparison.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    compare_positions(root,trials)


def compare_positions(root,trials):
    import numpy as np
    study=Path('D:/TradingML/runtimes/analysis/strategy-222-parameter-study-20260914')
    reviews=json.loads(Path('D:/TradingML/runtimes/analysis/strategy-222-opportunity-study-20260914/position-opportunity-review.json').read_text())
    ledger=json.loads((study/'quote-ledger.json').read_text())
    quotes={};cases=[]
    for review in reviews:
        best=review.get('best')
        row=dict(position=review['n'],symbol=review['s'],original_entry_time=review['actual_ET'],
                 original_entry=review['actual_entry'],original_net=review['actual_net'],variants=[])
        if not best:
            row['hindsight_status']='No positive tested hindsight label'
            window=ledger[review['group']]['window']
            start=datetime.fromisoformat(window['start']);end=datetime.fromisoformat(window['end'])
            for trial in trials:
                run_end=datetime.fromisoformat('2026-08-21T'+trial['end']).replace(tzinfo=ZoneInfo('America/New_York'))
                if review['s'] in trial['tickers'] and run_end>=end:
                    row['variants'].append(dict(name=trial['name'],run_id=trial['run_id'],
                        status='no_positive_hindsight_label',episodes=[e for e in trial['episodes']
                            if e['symbol']==review['s'] and start<=datetime.fromisoformat(e['opened_at'])<=end]))
            cases.append(row);continue
        key=review['group']
        if key not in quotes:quotes[key]=np.load(study/f'quotes-{key}.npz')['data']
        data=quotes[key]
        # A merged review window can contain multiple separate surges. Do not
        # credit an early, stopped trade with a much later session high.
        future=data[(data[:,0]/1e6>=best['t']) & (data[:,0]/1e6<=best['t']+300)]
        peak=future[int(np.argmax(future[:,1]))]
        peak_t=float(peak[0]/1e6);peak_bid=float(peak[1]);origin=float(best['label']['ask'])
        row.update(hindsight_time=best['ET'],hindsight_ask=origin,peak_bid=peak_bid,
                   peak_time=datetime.fromtimestamp(peak_t,ZoneInfo('America/New_York')).isoformat(),
                   major_move=peak_bid/origin-1>=.10,
                   opportunity_id=f"{review['s']}:{best['t']}")
        end=datetime.fromisoformat(ledger[key]['window']['end']).timestamp()
        start=datetime.fromisoformat(ledger[key]['window']['start']).timestamp()
        for trial in trials:
            if review['s'] not in trial['tickers']:continue
            run_end=datetime.fromisoformat('2026-08-21T'+trial['end']).replace(tzinfo=ZoneInfo('America/New_York')).timestamp()
            if run_end<end-1:continue
            eligible=[]
            for episode in trial['episodes']:
                if episode['symbol']!=review['s']:continue
                opened=datetime.fromisoformat(episode['opened_at']).timestamp()
                closed=datetime.fromisoformat(episode['closed_at']).timestamp() if episode.get('closed_at') else float('inf')
                if opened<=peak_t and closed>=best['t'] and closed>=start:
                    eligible.append(episode)
            entry=eligible[0] if eligible else None
            compared=dict(name=trial['name'],run_id=trial['run_id'],experiment=trial['experiment'],
                status='entered_before_peak' if entry else 'no_position_during_labeled_move')
            if entry:
                opened=datetime.fromisoformat(entry['opened_at']).timestamp()
                compared.update(entry_time=entry['opened_at'],entry_price=entry['entry_price'],
                    delay_seconds=opened-best['t'],episode_net=entry.get('net'),
                    fraction_of_price_move_before_entry=(entry['entry_price']-origin)/(peak_bid-origin) if peak_bid>origin else None,
                    exited_before_peak=datetime.fromisoformat(entry['closed_at']).timestamp()<peak_t if entry.get('closed_at') else False)
            row['variants'].append(compared)
        cases.append(row)
    (root/'position-comparison.json').write_text(json.dumps(dict(
        method='Hindsight best tested ask to maximum bid in the following five minutes, bounded by the frozen review window. This is an offline diagnostic, not an executable target or proof of profit. Shared opportunity IDs must be deduplicated when aggregating.',
        cases=cases),indent=2),encoding='utf-8')
    baseline_count=sum(any(v['name']=='corrected-baseline' for v in c['variants']) for c in cases)
    candidate_count=sum(any(v['name']!='corrected-baseline' for v in c['variants']) for c in cases)
    print(f"Position audit: {len(cases)} original positions; completed baseline coverage={baseline_count}, candidate coverage={candidate_count}",flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,default=Path('D:/TradingML/runtimes/analysis/strategy-222-refinement'))
    summarize(parser.parse_args().runtime)
