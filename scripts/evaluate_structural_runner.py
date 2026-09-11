"""Paired runner experiment on frozen accepted entries; development evidence only."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from bisect import bisect_left,bisect_right
from collections import deque
from copy import deepcopy
from hashlib import sha256
import json

from scripts.audit_structural_baseline import POLICY,write
from scripts.swing_book_paths import validate_runtime_root


def runner(sample,rows,quotes):
    """Same initial fill/stop/30s horizon; sell half at target, trail remainder.

    A three-contiguous-bar middle low is usable only at the third close and
    only if it formed after entry. Stop amendments have zero modeled latency.
    """
    base=sample['outcome'];start=base['entry_at'];end=sample['at']+30
    times=[q['at'] for q in quotes];i=bisect_left(times,start)
    ri=bisect_right([r['at'] for r in rows],start)
    remaining=POLICY['shares'];cash=-remaining*base['entry']-POLICY['fee_per_order']
    stop=sample['stop'];partial=False;bars=deque(maxlen=3);journal=[];requested=None
    last_quote=None;last_row_at=None
    def sell(q,qty,reason):
        nonlocal cash,remaining
        price=q['bid']*(1-POLICY['slippage_bps_per_side']/10000)
        cash+=qty*price-POLICY['fee_per_order'];remaining-=qty
        journal.append(dict(at=q['at'],action='sell',quantity=qty,price=price,reason=reason,stop=stop))
    for q in quotes[i:]:
        at=q['at']
        if at>end:break
        # Quotes at a close execute before decisions made at that same close.
        while ri<len(rows) and rows[ri]['at']<at:
            row=rows[ri];bars.append(row['bar']);last_row_at=row['at'];ri+=1
            if partial and len(bars)==3:
                a,b,c=bars
                if a['end']==b['time'] and b['end']==c['time'] and b['time']>start and b['low']<a['low'] and b['low']<c['low']:
                    candidate=b['low']-POLICY['tick']
                    if last_quote and row['at']-last_quote['at']<=1 and stop<candidate<last_quote['bid']:
                        stop=candidate
                        journal.append(dict(at=row['at'],action='raise_stop',stop=stop,swing_at=b['end']))
            if partial and any(e['state']=='failed_breakout' and e['level'].get('unified_level_id')==sample['level'].get('unified_level_id') for e in row['events']):
                if requested is None:
                    requested=row['at']+.1
                    journal.append(dict(at=row['at'],action='exit_requested'))
        last_quote=q
        if q['bid']<=stop:
            sell(q,remaining,'stop');break
        if requested is not None and at>=requested:
            sell(q,remaining,'failed_breakout');break
        if not partial and q['bid']>=sample['target']:
            sell(q,remaining//2,'partial_target');partial=True
    if remaining and last_quote and end-last_quote['at']<=1:
        sell(last_quote,remaining,'horizon')
    return dict(entry=base['entry'],entry_at=start,net=round(cash,4) if not remaining else None,
                unresolved=bool(remaining),remaining=remaining,stop=stop,partial_taken=partial,journal=journal,
                exit_at=journal[-1]['at'] if journal and not remaining else None)


def main(args):
    root=validate_runtime_root(args.runtime);root.mkdir(parents=True,exist_ok=True)
    protocol=dict(version=1,experiment='same accepted entry cohort, half target then structural runner; same 30s cap',
        source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),policy=POLICY,
        release_criteria=dict(minimum_unseen_tickers=5,minimum_unseen_ticker_sessions=20,
            minimum_closed_holdout_trades=100,net_expectancy_positive=True,
            session_block_bootstrap_lower_95pct_above_zero=True,
            double_slippage_net_positive=True,complete_source_certification=True,
            causal_prefix_checks=True,runtime_execution_parity=True),
        note='Research acceptance criteria, not a profitability guarantee. Freeze rules before untouched holdout; count every experiment. No release on development results.')
    path=root/'protocol.json'
    if path.exists() and json.loads(path.read_text())!=json.loads(json.dumps(protocol)):
        raise ValueError('Frozen protocol differs; use a new runtime directory')
    write(path,protocol);summary={}
    for ticker in ('SUGP','JUNS'):
        key=f'{ticker}-2026-08-21';folder=args.input_root/key
        data=json.loads((folder/'inputs.json').read_text());analysis=json.loads((folder/'analysis.json').read_text())
        samples={s['at']:s for s in analysis['opportunities'] if s['family']=='breakout_accepted'}
        pairs=[]
        for pair in analysis['paired_exits']:
            s=samples[pair['at']]
            # Ensure the source decision matches the frozen fill being paired.
            if s['outcome']['entry_at']!=pair['baseline']['entry_at'] or s['outcome']['entry']!=pair['baseline']['entry']:
                raise ValueError('Ambiguous same-timestamp sample; cannot pair entries')
            result=runner(s,data['rows'],data['quotes'])
            pairs.append(dict(at=s['at'],baseline=pair['baseline'],runner=result))
        write(root/f'{key}.json',pairs)
        summary[key]=dict(trades=len(pairs),baseline_net=round(sum(p['baseline']['net'] for p in pairs),2),
            runner_resolved_net=round(sum(p['runner']['net'] or 0 for p in pairs),2),
            unresolved=sum(p['runner']['unresolved'] for p in pairs),
            input_sha256=sha256((folder/'inputs.json').read_bytes()).hexdigest(),
            cohort_sha256=sha256((folder/'analysis.json').read_bytes()).hexdigest())
        print(key,json.dumps(summary[key]),flush=True)
    write(root/'summary.json',summary)
    lines=['# Paired structural runner experiment','',
        'Development only. Entries, initial stops and 30s maximum horizon match the previous accepted cohort. '
        'The variant takes half at the same first target and trails the rest below newly confirmed three-bar lows; '
        'a failed breakout of the origin requests exit after 100ms. Spread, 5bps slippage and $1 per fill are included. '
        'Stop amendments assume instantaneous activation; this is not broker execution parity.', '',
        '| Window | Paired entries | Full target net | Half target / runner net | Unresolved |',
        '|---|---:|---:|---:|---:|']
    for key,s in summary.items():lines.append(f'| {key} | {s["trades"]} | ${s["baseline_net"]:.2f} | ${s["runner_resolved_net"]:.2f} | {s["unresolved"]} |')
    lines += ['', 'The cohort remains fixed even if a runner overlaps a later baseline entry: this is a paired '
        'opportunity comparison, not a one-position portfolio backtest. Any winning variant must subsequently '
        'pass actual occupancy, risk sizing, execution and untouched-data checks.', '',
        'Release requirements are frozen in protocol.json: at least 5 unseen tickers, 20 unseen ticker-sessions, '
        '100 closed holdout trades, positive cost-adjusted expectancy with a positive lower 95% session-block '
        'bootstrap bound, positive net under double slippage, complete source certification, causal-prefix '
        'checks and runtime execution parity. Passing these does not guarantee future profits. These are '
        'minimum research checks; a shortfall means insufficient evidence, not permission to relax them.', '',
        'Current blocker: canonical participant-clock coverage exists only for SUGP/JUNS on six dates '
        'August 14–21. August 13/24/25 sidecars are absent; available live rows for August 24/25 have zero '
        'execution timestamps. Downstream code cannot reconstruct the missing exact timestamps. '
        'An ingestion-owned versioned acquisition/migration is required before unseen-date/ticker validation.', '',
        'No candidate was created or deployed. Existing source authorities and gate policies were not weakened.']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',type=Path,required=True);p.add_argument('--input-root',type=Path,required=True)
    main(p.parse_args())
