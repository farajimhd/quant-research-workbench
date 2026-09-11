"""Chronological research replay; not a registered strategy or broker simulator.

Run from the repository with --runtime under TradingML/runtimes. Captured
baseline inputs can be reused with --input-root. No source repair or fallback.
"""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
from collections import Counter, deque
from copy import deepcopy
from datetime import datetime, timedelta
from hashlib import sha256
import json

from scripts.audit_structural_baseline import POLICY, NY, collect, evaluate_rows, write
from scripts.swing_book_paths import validate_runtime_root

RULES = dict(POLICY, minimum_reward_risk=1.5, stop_spreads=2, stop_ticks=2)
WINDOWS = [(ticker, day, hour, book) for day in ('2026-08-21', '2026-08-24', '2026-08-25')
           for ticker, hour, book in [('SUGP', 4, 'structure_book_c58ec63aab23'),
                                     ('JUNS', 7, 'structure_book_1bc48fe01de7')]]


def level_id(level):
    return str(level.get('unified_level_id') or (level['lower'], level['upper']))


def replay(rows, quotes, end, partial=False, rules=None):
    """Merge closed-candle decisions and quotes; future data never selects entries."""
    p = rules or RULES
    if any(a['at'] >= b['at'] for a, b in zip(rows, rows[1:])):
        raise ValueError('Unordered decision candles')
    if any(a['at'] > b['at'] for a, b in zip(quotes, quotes[1:])):
        raise ValueError('Unordered quotes')
    position = pending = latest_quote = None
    bars = deque(maxlen=3)
    latest_exit = float('-inf')
    breaks = {}
    trades, journal = [], []
    blocked = Counter()
    qi = 0

    def sell(q, quantity, reason):
        nonlocal position, latest_exit
        price = q['bid'] * (1-p['slippage_bps_per_side']/10000)
        position['cash'] += quantity*price-p['fee_per_order']
        position['remaining'] -= quantity
        journal.append(dict(at=q['at'], action='sell', quantity=quantity, price=price,
                            reason=reason, stop=position['stop']))
        if position['remaining'] == 0:
            position.update(exit_at=q['at'], reason=reason, net=position['cash'])
            trades.append(position)
            latest_exit = q['at']
            position = None

    def quality(q, stop, target):
        ask = q['ask']*(1+p['slippage_bps_per_side']/10000)
        risk = ask-stop*(1-p['slippage_bps_per_side']/10000)+2*p['fee_per_order']/p['shares']
        reward = target*(1-p['slippage_bps_per_side']/10000)-ask-2*p['fee_per_order']/p['shares']
        return (0 < stop < q['bid'] <= q['ask'] < target and
                q['bid']-stop+1e-9 >= max(p['stop_ticks']*p['tick'], p['stop_spreads']*(q['ask']-q['bid'])) and
                reward >= p['minimum_reward_risk']*risk and
                20000*(q['ask']-q['bid'])/(q['ask']+q['bid']) <= p['maximum_spread_bps'])

    def on_quote(q):
        nonlocal position, pending, latest_quote
        if not 0 < q['bid'] <= q['ask']:
            raise ValueError('Invalid quote')
        latest_quote = q
        if position:
            if q['bid'] <= position['stop']:
                sell(q, position['remaining'], 'protective_stop')
            elif q['at'] >= end:
                if q['at']-end <= p['quote_age_seconds']:
                    sell(q, position['remaining'], 'window_end')
            elif position.get('exit_due') is not None and q['at'] >= position['exit_due']:
                sell(q, position['remaining'], 'structural_failure')
            elif q['bid'] >= position['target'] and not position['took_profit']:
                quantity = position['remaining']//2 if partial else position['remaining']
                position['took_profit'] = True
                sell(q, quantity, 'profit_target')
        if pending and q['at'] >= pending['due']:
            order = pending
            pending = None
            if q['at'] > order['at']+p['quote_age_seconds'] or q['at'] >= end:
                blocked['entry_expired'] += 1
            elif not quality(q, order['stop'], order['target']):
                blocked['execution_quality'] += 1
            else:
                price = q['ask']*(1+p['slippage_bps_per_side']/10000)
                position = dict(order, entry_at=q['at'], entry=price, remaining=p['shares'],
                                cash=-p['shares']*price-p['fee_per_order'], took_profit=False,
                                below_count=0)
                journal.append(dict(at=q['at'], decision_at=order['at'], action='buy',
                                    quantity=p['shares'], price=price, stop=order['stop'],
                                    target=order['target'], level=order['level']))

    for row in rows:
        at = row['at']
        if at >= end:
            break
        while qi < len(quotes) and quotes[qi]['at'] <= at:
            on_quote(quotes[qi]); qi += 1
        if any(l.get('confirmed_at_ms', 0) > at*1000 for l in row['levels']+[e['level'] for e in row['events']]):
            raise ValueError('Future level confirmation')
        bars.append(row['bar'])
        for event in row['events']:
            if event['state'] == 'breakout':
                breaks[level_id(event['level'])] = at
        if position:
            level = position['level']
            position['below_count'] = position['below_count']+1 if row['bar']['close'] < level['lower'] else 0
            failed = any(e['state'] == 'failed_breakout' and level_id(e['level']) == level_id(level) for e in row['events'])
            if failed or position['below_count'] >= 2:
                if position.get('exit_due') is None:
                    position['exit_due'] = at+p['latency_ms']/1000
                    journal.append(dict(at=at, action='exit_requested', reason='structural_failure'))
            # Three contiguous completed bars confirm the middle candle's low.
            # The low must have formed after entry; the stop never decreases.
            if len(bars) == 3:
                a, b, c = bars
                if (a['end'] == b['time'] and b['end'] == c['time'] and
                    b['time'] > position['entry_at'] and b['low'] < a['low'] and b['low'] < c['low']):
                    stop = b['low']-p['tick']
                    if (latest_quote and at-latest_quote['at'] <= p['quote_age_seconds'] and
                        position['stop'] < stop < latest_quote['bid'] and
                        latest_quote['bid']-stop >= max(p['stop_ticks']*p['tick'],p['stop_spreads']*(latest_quote['ask']-latest_quote['bid']))):
                        position['stop'] = stop
                        journal.append(dict(at=at, action='raise_stop', stop=stop, swing_at=b['end']))
            continue
        if pending:
            continue
        for event in row['events']:
            if event['state'] != 'breakout_accepted':
                continue
            level = event['level']
            reason = None
            overhead = [l['lower'] for l in row['levels'] if l.get('side') in (-1, 'resistance') and
                        l['lower'] > max(row['bar']['close'], level['upper'])]
            stop, target = level['lower']-p['tick'], min(overhead) if overhead else 0
            if breaks.get(level_id(level), float('-inf')) <= latest_exit and latest_exit != float('-inf'):
                reason = 'fresh_break_required'
            elif row['shares'] < p['minimum_shares'] or row['dollars'] < p['minimum_dollars']:
                reason = 'session_volume'
            elif min(row['rate10'], row['rate60']) < p['minimum_trade_rate']:
                reason = 'activity'
            elif latest_quote is None or at-latest_quote['at'] > p['quote_age_seconds']:
                reason = 'stale_quote'
            elif not quality(latest_quote, stop, target):
                reason = 'room_stop_or_spread'
            if reason:
                blocked[reason] += 1
                journal.append(dict(at=at, action='entry_blocked', reason=reason, level=deepcopy(level)))
                continue
            pending = dict(at=at, due=at+p['latency_ms']/1000, stop=stop, target=target,
                           level=deepcopy(level), sequence=deepcopy(row['sequence']), regime=deepcopy(row['direction']))
            journal.append(dict(at=at, action='entry_requested', stop=stop, target=target, level=deepcopy(level)))
            break
    while qi < len(quotes) and quotes[qi]['at'] <= end+p['quote_age_seconds']:
        on_quote(quotes[qi]); qi += 1
    return dict(trades=trades, journal=journal, unresolved_position=position, pending_entry=pending,
                summary=dict(trades=len(trades), net=round(sum(t['net'] for t in trades), 2),
                             wins=sum(t['net'] > 0 for t in trades), blocked=dict(blocked),
                             exits=dict(Counter(t['reason'] for t in trades)),
                             unresolved=int(position is not None), pending=int(pending is not None)))


async def main(args):
    root = validate_runtime_root(args.runtime)
    root.mkdir(parents=True, exist_ok=True)
    plan = dict(version=1, rules=RULES, windows=WINDOWS,
                source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                scope='Aug21 development; Aug24/25 fixed forward checks, not held-out tickers',
                variants=['fixed_30s_baseline', 'managed_full_target', 'managed_half_target_runner'])
    plan_path = root/'plan.json'
    if plan_path.exists() and json.loads(plan_path.read_text()) != json.loads(json.dumps(plan)):
        raise ValueError('Frozen plan differs; choose a new runtime directory')
    write(plan_path, plan)
    results = {}
    for ticker, day, hour, book in WINDOWS:
        key = f'{ticker}-{day}'
        print(f'{key}: active', flush=True)
        folder = root/key; folder.mkdir(exist_ok=True)
        cache = args.input_root/key/'inputs.json' if args.input_root else None
        try:
            if cache and cache.exists():
                data = json.loads(cache.read_text())
                if data['ticker'] != ticker or data['session'] != day or data['book']['id'] != book:
                    raise ValueError('Cached input identity mismatch')
                write(folder/'inputs.json', data)
            elif args.cached_only and not (folder/'inputs.json').exists():
                raise ValueError('No input cache; omit --cached-only to collect certified source')
            else:
                data = await collect(root, ticker, day, hour, book)
            end = (datetime.fromisoformat(day).replace(hour=hour, tzinfo=NY)+timedelta(minutes=30)).timestamp()
            _, base = evaluate_rows(data['rows'], data['quotes'])
            results[key] = dict(status='completed', baseline=base['breakout_accepted'],
                                input_sha256=sha256((folder/'inputs.json').read_bytes()).hexdigest())
            for name, partial in [('managed_full_target', False), ('managed_half_target_runner', True)]:
                result = replay(data['rows'], data['quotes'], end, partial)
                write(folder/f'{name}.json', result)
                results[key][name] = result['summary']
            print(f'{key}: completed; full ${results[key]["managed_full_target"]["net"]:.2f}; runner ${results[key]["managed_half_target_runner"]["net"]:.2f}', flush=True)
        except Exception as exc:
            results[key] = dict(status='failed', error=str(exc))
            print(f'{key}: failed; details in summary.json', flush=True)
        write(root/'summary.json', results)
    failed = sum(r['status'] == 'failed' for r in results.values())
    lines = ['# Managed accepted-breakout evaluation', '',
             'Research only. Existing candidates are unchanged. Parameters were fixed before these runs.', '',
             '| Window (New York) | Fixed 30s baseline | Managed full target | Half target + runner |',
             '|---|---:|---:|---:|']
    for key, r in results.items():
        if r['status'] == 'failed':
            lines.append(f'| {key} | source/evaluation failed | — | — |'); continue
        cells = [f'{r[n]["trades"]} trades / ${r[n]["net"]:.2f}' for n in ('baseline','managed_full_target','managed_half_target_runner')]
        lines.append(f'| {key} | '+ ' | '.join(cells)+' |')
    lines += ['', 'SUGP uses 04:00–04:30; JUNS uses 07:00–07:30. Aug21 is previously examined development data. '
              'Aug24/25 are fixed forward checks, not a broad or held-out-ticker study.', '',
              'All variants use 100 shares, executable bid/ask, 5 bps slippage per side and $1 per fill. '
              'Managed variants require net reward/risk >=1.5 and stop clearance >=2 spreads and >=2 cents '
              'at decision and entry. Entry executes on the first quote >=100 ms later, expiring after 1s. '
              'A new breakout after the last exit must precede reentry acceptance. Stops ratchet below newly '
              'confirmed three-bar lows; two closes below the entry level or its failed-breakout label request '
              'an exit. Protective stops remain active. Full-target exits all; the separate runner variant '
              'takes half at the target and trails the rest. The window boundary closes remaining positions '
              'using a quote within 1s, otherwise they remain explicitly unresolved.', '',
              'The baseline has a 30s horizon; managed trades can last to the window boundary. Results therefore '
              'compare whole rule sets, not an isolated causal effect of one change. No fitted regime filter, '
              'borrow, displayed-depth constraint, queue/impact/partial-fill model, or broker integration is included. '
              'Stop updates assume instantaneous amendment at candle close; this needs runtime parity testing '
              'before candidate release. Full trade and decision journals, blocked counts and unresolved positions '
              'are stored alongside this report. Frozen plan and input hashes retain provenance.', '']
    (root/'REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'Finished: completed={len(results)-failed} failed={failed} active=0 queued=0; {root}', flush=True)
    return 2 if failed else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--input-root', type=Path)
    parser.add_argument('--cached-only', action='store_true')
    raise SystemExit(asyncio.run(main(parser.parse_args())))
