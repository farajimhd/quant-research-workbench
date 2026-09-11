"""Separate entry timing from paired exit research; never a strategy release."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
from bisect import bisect_left, bisect_right
from copy import deepcopy
from hashlib import sha256
import json
from statistics import mean

from scripts.audit_structural_baseline import POLICY, collect, evaluate_rows, write
from scripts.evaluate_managed_breakouts import WINDOWS, level_id
from scripts.swing_book_paths import validate_runtime_root

TIMING = dict(minimum_age_seconds=10, stall_seconds=5, rejection_age_seconds=5,
              retrace_spreads=2, retrace_bps=10, diagnostic_move_bps=100,
              post_exit_seconds=30, sequence_memory_seconds=60)


def adaptive_exit(sample, rows, quotes):
    """Use frozen baseline entry; stream only as-of bars/quotes after entry.

    Hard stop/target are identical to baseline. Only add stalled-progress exit.
    """
    base = sample['outcome']
    if base['status'] != 'resolved':
        return dict(status='unpaired')
    start, end = base['entry_at'], sample['at']+POLICY['horizon_seconds']
    times = [q['at'] for q in quotes]
    i, j = bisect_left(times, start), bisect_right(times, end)
    ri = bisect_right([r['at'] for r in rows], start)
    peak, last_progress = quotes[i]['bid'], start
    rejection_at = float('-inf')
    previous_close = None
    previous_end = None
    falling = 0
    requested = None
    trace = []
    for q in quotes[i:j]:
        at = q['at']
        while ri < len(rows) and rows[ri]['at'] <= at:
            row = rows[ri]
            # Restrict rejection evidence to an overhead barrier near the price.
            if any(e['state'] in ('rejection','failed_breakout') and
                   e['level']['lower'] <= row['bar']['high'] and
                   e['level']['upper'] >= row['bar']['close'] for e in row['events']):
                rejection_at = row['at']
            close = row['bar']['close']
            contiguous = previous_end == row['bar']['time']
            falling = falling+1 if contiguous and previous_close is not None and close < previous_close else 0
            previous_close, previous_end = close, row['at']
            ri += 1
        if q['bid'] > peak:
            peak, last_progress = q['bid'], at
        reason = None
        if q['bid'] <= sample['stop']:
            reason = 'stop'
        elif q['bid'] >= sample['target']:
            reason = 'target'
        elif requested is not None and at >= requested+POLICY['latency_ms']/1000:
            reason = 'stalled_failure'
        if reason:
            price = q['bid']*(1-POLICY['slippage_bps_per_side']/10000)
            return dict(status='resolved',entry=base['entry'],entry_at=start,exit_at=at,
                        exit_bid=q['bid'],reason=reason,net=(price-base['entry'])*POLICY['shares']-2*POLICY['fee_per_order'],trace=trace)
        retrace = max(TIMING['retrace_spreads']*(q['ask']-q['bid']),base['entry']*TIMING['retrace_bps']/10000)
        weak = at-rejection_at <= TIMING['rejection_age_seconds'] or (falling >= 2 and at-previous_end <= POLICY['quote_age_seconds'])
        if (requested is None and at-start >= TIMING['minimum_age_seconds'] and
            at-last_progress >= TIMING['stall_seconds'] and peak-q['bid'] >= retrace and weak):
            requested = at
            trace.append(dict(at=at,action='exit_requested',peak_bid=peak,last_progress_at=last_progress,
                              rejection_at=rejection_at if rejection_at != float('-inf') else None,
                              falling_closes=falling,retrace=retrace))
    # The unchanged baseline supplies the common horizon or censoring behavior.
    return dict(deepcopy(base),trace=trace)


def timing_metrics(sample, quotes):
    at = sample['at']; times = [q['at'] for q in quotes]
    past = quotes[bisect_left(times,at-30):bisect_right(times,at)]
    o = sample['outcome']
    metrics = dict(distance_above_level_bps=(sample['close']/sample['level']['upper']-1)*10000,
                   preceding_30s_runup_bps=(past[-1]['bid']/min(q['bid'] for q in past)-1)*10000 if past else None)
    if o['status'] != 'resolved':
        return metrics
    live = quotes[bisect_left(times,o['entry_at']):bisect_right(times,o['exit_at'])]
    exit_bid = live[-1]['bid']
    peak = max(q['bid'] for q in live)
    metrics.update(holding_seconds=o['exit_at']-o['entry_at'],
                   favorable_before_exit_bps=(peak/o['entry']-1)*10000,
                   giveback_bps=(peak-exit_bid)/o['entry']*10000)
    end = o['exit_at']+TIMING['post_exit_seconds']
    future = quotes[bisect_right(times,o['exit_at']):bisect_right(times,end)]
    observed = bool(future and end-future[-1]['at'] <= POLICY['quote_age_seconds'])
    metrics.update(post_exit_observed=observed,
                   post_exit_max_extension_bps=max(0,(max(q['bid'] for q in future)/exit_bid-1)*10000) if observed else None,
                   post_exit_target_reached=any(q['bid']>=sample['target'] for q in future) if observed else None)
    return metrics


def analyze(data):
    rows, quotes = data['rows'], data['quotes']
    samples, entry_summary = evaluate_rows(rows, quotes)
    # Link decisions only to evidence already observed at the same level.
    ri = 0; history = {}
    for sample in samples:
        at = sample['at']
        while ri < len(rows) and rows[ri]['at'] <= at:
            row = rows[ri]
            for e in row['events']:
                key = level_id(e['level'])
                if e['state'] in ('failed_breakout','resistance_reclaim'):
                    history.pop(key,None)
                if e['state'] == 'breakout':
                    history[key] = row['at']
            ri += 1
        broke = history.get(level_id(sample['level']))
        sample['timing'] = timing_metrics(sample,quotes)
        sample['timing']['seconds_since_breakout'] = at-broke if broke is not None and at-broke <= TIMING['sequence_memory_seconds'] else None
    # Select the cohort using the ORIGINAL baseline occupancy; never use the
    # experimental exit to admit extra trades. Both variants share exact fills.
    paired = []; available = 0
    for s in samples:
        if s['family'] != 'breakout_accepted' or s['blocked'] or s['at'] <= available:
            continue
        o = s['outcome']
        if o['status'] == 'censored':
            available=o['reserved_until'];continue
        if o['status'] != 'resolved':
            available=s['at']+POLICY['quote_age_seconds'];continue
        available=o['exit_at']
        experimental = adaptive_exit(s,rows,quotes)
        alternative = dict(s,outcome=experimental)
        paired.append(dict(at=s['at'],baseline=o,experimental=experimental,
                           baseline_timing=s['timing'],experimental_timing=timing_metrics(alternative,quotes)))
    timing = {}
    for family in entry_summary:
        subset = [s for s in samples if s['family']==family]
        eligible = [s for s in subset if not s['blocked'] and s['outcome']['status']=='resolved']
        delays = [s['timing']['seconds_since_breakout'] for s in eligible if s['timing']['seconds_since_breakout'] is not None]
        # Hindsight diagnostics only, not proof a blocked opportunity was tradable.
        timing[family] = dict(eligible_resolved=len(eligible),
            mean_break_delay_seconds=mean(delays) if delays else None,
            mean_preceding_runup_bps=mean(s['timing']['preceding_30s_runup_bps'] for s in eligible) if eligible else None,
            blocked_with_100bps_mfe=sum(bool(s['blocked']) and s['outcome'].get('mfe_bps') is not None and
                                       s['outcome']['mfe_bps']>=TIMING['diagnostic_move_bps'] for s in subset))
    summary = dict(entries=entry_summary,timing=timing,paired=dict(count=len(paired),
        baseline_net=round(sum(p['baseline']['net'] for p in paired),2),
        experimental_net=round(sum(p['experimental']['net'] for p in paired),2),
        changed_exits=sum(p['baseline']['exit_at']!=p['experimental']['exit_at'] for p in paired),
        mean_baseline_giveback_bps=mean(p['baseline_timing']['giveback_bps'] for p in paired) if paired else None,
        mean_experimental_giveback_bps=mean(p['experimental_timing']['giveback_bps'] for p in paired) if paired else None))
    return dict(summary=summary,opportunities=samples,paired_exits=paired)


async def main(args):
    root=validate_runtime_root(args.runtime);root.mkdir(parents=True,exist_ok=True)
    plan=dict(version=1,windows=WINDOWS,entry_policy=POLICY,timing_policy=TIMING,
              source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
              scope='Aug21 development; Aug24/25 fixed forward attempts; no parameter search')
    if (root/'plan.json').exists() and json.loads((root/'plan.json').read_text()) != json.loads(json.dumps(plan)):
        raise ValueError('Frozen plan mismatch; use a new runtime directory')
    write(root/'plan.json',plan)
    results={}
    for ticker,day,hour,book in WINDOWS:
        key=f'{ticker}-{day}';folder=root/key;folder.mkdir(exist_ok=True)
        print(f'{key}: active',flush=True)
        try:
            source=args.input_root/key/'inputs.json'
            dest=folder/'inputs.json'
            if source.exists() and not dest.exists():
                data=json.loads(source.read_text())
                if (data['ticker'],data['session'],data['book']['id']) != (ticker,day,book):
                    raise ValueError('Input identity mismatch')
                write(dest,data)
            data=await collect(root,ticker,day,hour,book)
            result=analyze(data);write(folder/'analysis.json',result)
            results[key]=dict(status='completed',input_sha256=sha256(dest.read_bytes()).hexdigest(),**result['summary'])
            p=result['summary']['paired']
            print(f'{key}: {p["count"]} paired trades; baseline ${p["baseline_net"]:.2f}; experiment ${p["experimental_net"]:.2f}',flush=True)
        except Exception as exc:
            results[key]=dict(status='failed',error=str(exc));print(f'{key}: failed; details in summary.json',flush=True)
        write(root/'summary.json',results)
    lines=['# Structural timing experiment','',
           'Research only; no candidate deployed. Costs: 100 shares, $1 per order, 5 bps slippage per side, executable bid/ask. '
           'SUGP 04:00–04:30 and JUNS 07:00–07:30 New York. Aug21 is previously examined data.', '',
           '| Window | Pressure / fixed exit | Breakout / fixed exit | Acceptance / fixed exit | Same accepted entries / experimental exit |',
           '|---|---:|---:|---:|---:|']
    for key,r in results.items():
        if r['status']=='failed':lines.append(f'| {key} | unavailable | unavailable | unavailable | unavailable |');continue
        cells=[f'{r["entries"][f]["trades"]} trades / ${r["entries"][f]["net"]:.2f}' for f in ('prebreak_pressure','breakout','breakout_accepted')]
        cells.append(f'{r["paired"]["count"]} trades / ${r["paired"]["experimental_net"]:.2f}')
        lines.append(f'| {key} | '+' | '.join(cells)+' |')
    lines += ['', 'Entry comparison holds the stop/target/30s exit policy and gates constant; each family has its own '
              'one-position occupancy. Pressure uses four increasing closes within 10 bps below resistance. '
              'It is an intentionally simple baseline, not a comprehensive pressure predictor.', '',
              'Exit comparison fixes the original accepted-breakout entry fills and occupancy. Hard stops, targets and '
              'the 30s horizon remain unchanged. Added exit requires age >=10s, no new bid high for >=5s, '
              'retracement >=max(2 spreads,10bps), and either a recent relevant rejection or two contiguous falling closes. '
              'An exit request executes on the first subsequent quote at least 100ms later. No rule sees future observations.', '',
              'analysis.json includes every opportunity, blocked reasons, prior runup, confirmation delay, pre-exit profit '
              'giveback and 30s post-exit continuation. Future-dependent fields are diagnostics only. Missing future '
              'coverage is marked; favorable post-exit movement is not proof an exit was wrong. The blocked-with-100bps-MFE '
              'count is counterfactual and must not override tradability gates. It does not detect moves with no signal at all.', '',
              'No threshold tuning, detector rebuild, live execution change, full market-impact model, or held-out-ticker '
              'validation occurred. Forward source failures remain explicit; do not infer robustness from August 21.', '',
              'Resume with the same command; completed captured inputs are retained. Source/plan changes require a new '
              'runtime directory. plan.json pins policies and source; summary.json pins input hashes and detailed failures.']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    failed=sum(r['status']=='failed' for r in results.values())
    print(f'Finished: completed={len(results)-failed} failed={failed} active=0 queued=0; {root}',flush=True)
    return 2 if failed else 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--input-root',type=Path,required=True)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
