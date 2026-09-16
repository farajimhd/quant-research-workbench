"""Compare actual fill prefixes; temporal overlap is diagnostic, not causal attribution."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
import argparse
from collections import Counter
from datetime import datetime
from hashlib import sha256
import json
from math import isfinite
from pathlib import Path
import sqlite3
from uuid import UUID
from zoneinfo import ZoneInfo

if __package__:
    from .summarize_strategy_222_refinement import episodes
else:
    from summarize_strategy_222_refinement import episodes

RUNTIME = Path('D:/TradingML/runtimes')


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError('Comparison timestamps must include a timezone')
    return result


def prefix_episodes(rows, cutoff):
    """Rebuild each position from fills; never import its later exit or net label."""
    end = timestamp(cutoff)
    result = []
    for row in rows:
        fills = [f for f in row['fills'] if timestamp(f['time']) <= end]
        if not fills:
            continue
        item = dict(symbol=row['symbol'], opened_at=fills[0]['time'],
                    entry_price=fills[0]['price'], quantity=0., buy=0., sell=0., fees=0., fills=fills)
        previous = None
        for fill in fills:
            at = timestamp(fill['time'])
            if previous is not None and at < previous:
                raise ValueError('Fill times are out of order')
            previous = at
            q, price, fee = (fill[k] for k in ('quantity', 'price', 'fee'))
            if not all(type(v) in (int, float) and isfinite(v) and v >= 0 for v in (q, price, fee)) or q == 0 or price == 0:
                raise ValueError('Invalid fill quantity, price or fee')
            if fill['side'] not in ('B', 'S'):
                raise ValueError('Unknown fill side')
            buy = fill['side'] == 'B'
            item['quantity'] += q if buy else -q
            item['buy' if buy else 'sell'] += q * price
            item['fees'] += fee
            if item['quantity'] < -1e-8:
                raise ValueError('Exit without sufficient position')
        if abs(item['quantity']) < 1e-8:
            item.update(closed_at=fills[-1]['time'], net=item['sell']-item['buy']-item['fees'])
        result.append(item)
    return result


def compare(baseline, candidate, cutoff):
    timestamp(cutoff)
    sides = [prefix_episodes(rows, cutoff) for rows in (baseline, candidate)]
    def key(e):
        return e['symbol'], timestamp(e['opened_at'])
    if any(len({key(e) for e in rows}) != len(rows) for rows in sides):
        raise ValueError('Ambiguous duplicate episode entry')
    summaries = []
    for rows in sides:
        closed = [e for e in rows if 'net' in e]
        summaries.append(dict(entries=len(rows), closed=len(closed), wins=sum(e['net'] > 0 for e in closed),
            closed_net=sum(e['net'] for e in closed), all_fill_fees=sum(e['fees'] for e in rows),
            open=[dict(symbol=e['symbol'], quantity=e['quantity']) for e in rows if 'net' not in e]))
    candidates = {key(e): i for i, e in enumerate(sides[1])}
    matches = []
    for i, old in enumerate(sides[0]):
        same = candidates.get(key(old))
        overlap = [j for j, new in enumerate(sides[1]) if new['symbol'] == old['symbol']
            and timestamp(new['opened_at']) < timestamp(old.get('closed_at', cutoff))
            and timestamp(old['opened_at']) < timestamp(new.get('closed_at', cutoff))]
        # An entry exactly at the inclusive cutoff has a zero-length open interval.
        if same is not None and same not in overlap:
            overlap.append(same)
        status = 'same_time_entry' if same is not None else 'overlapping_shifted_entry' if overlap else 'no_overlapping_candidate_entry'
        matches.append(dict(baseline_index=i, symbol=old['symbol'], baseline_entry=old['opened_at'],
            status=status, same_time_candidate_index=same, overlapping_candidate_indices=overlap,
            entry_time_changes_seconds=[(timestamp(sides[1][j]['opened_at'])-timestamp(old['opened_at'])).total_seconds() for j in overlap]))
    return dict(cutoff=cutoff, summaries=dict(zip(('baseline', 'candidate'), summaries)),
        closed_net_change=summaries[1]['closed_net']-summaries[0]['closed_net'],
        match_counts=dict(Counter(m['status'] for m in matches)), matches=matches,
        candidate_entries_without_same_time_baseline=[j for j, e in enumerate(sides[1]) if key(e) not in {key(b) for b in sides[0]}],
        episodes=dict(zip(('baseline', 'candidate'), sides)),
        limitations=['Closed P&L excludes open positions and is not marked account equity.',
            'Overlapping episodes can be many-to-many; do not sum overlap-linked P&L as an attribution.',
            'No overlap does not prove a setup was permanently removed; later new setups remain in the candidate ledger.',
            'Temporal overlap does not prove the same setup, or establish hindsight-move coverage.'])


def read_terminal_run(run_id, cutoff):
    run_id = str(UUID(run_id))
    path = RUNTIME/'trading/backtest'/run_id/'journal.sqlite3'
    wal = Path(str(path)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Wait for the replay writer to close its journal')
    connection = sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1', uri=True)
    try:
        started = connection.execute("select payload_json from journal where run_id=? and category='lifecycle' and json_extract(payload_json,'$.status')='running' order by sequence limit 1", (run_id,)).fetchone()
        session = json.loads(started[0]).get('config', {}).get('anchor_date') if started else None
        if session != timestamp(cutoff).astimezone(ZoneInfo('America/New_York')).date().isoformat():
            raise ValueError('Comparison cutoff must belong to the recorded market session')
        terminal = connection.execute("select event_time,payload_json from journal where run_id=? and category='lifecycle' order by sequence desc limit 1", (run_id,)).fetchone()
        if (not terminal or json.loads(terminal[1]).get('status') not in ('completed', 'stopped')
                or json.loads(terminal[1]).get('error')):
            raise ValueError('Comparison requires a completed or cleanly stopped run')
        if timestamp(terminal[0]) < timestamp(cutoff):
            raise ValueError('Comparison cutoff exceeds the recorded run boundary')
    finally:
        connection.close()
    data = episodes(path)['episodes']
    return data, dict(run_id=run_id, terminal_time=terminal[0],
        fill_evidence_sha256=sha256(json.dumps(data, sort_keys=True).encode()).hexdigest())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-run', required=True)
    parser.add_argument('--candidate-run', required=True)
    parser.add_argument('--cutoff', required=True, help='Inclusive market timestamp with timezone')
    parser.add_argument('--output', required=True, type=Path, help='JSON report under the runtime root')
    args = parser.parse_args()
    try:
        output = args.output.resolve()
        output.relative_to(RUNTIME.resolve())
        baseline, before = read_terminal_run(args.baseline_run, args.cutoff)
        candidate, after = read_terminal_run(args.candidate_run, args.cutoff)
        report = compare(baseline, candidate, args.cutoff)
    except (ValueError, sqlite3.Error) as error:
        parser.error(str(error))
    report['sources'] = dict(baseline=before, candidate=after)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
    temporary.replace(output)
    print(f"Compared through {args.cutoff}")
    for name, row in report['summaries'].items():
        print(f"{name}: entries={row['entries']} closed={row['closed']} wins={row['wins']} open={len(row['open'])} closed_net=${row['closed_net']:.2f}")
    print('Closed P&L only; shifted/overlapping entries are not credited as removed trades.')
    print(f'Report: {output}')


if __name__ == '__main__':
    main()
