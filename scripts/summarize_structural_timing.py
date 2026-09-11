"""Human-readable timing findings and retrospective fixed-window move coverage."""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
from bisect import bisect_left,bisect_right
from datetime import datetime,timedelta
from hashlib import sha256
import json

from scripts.audit_structural_baseline import NY,write
from scripts.evaluate_structural_timing import TIMING
from scripts.evaluate_managed_breakouts import WINDOWS
from scripts.swing_book_paths import validate_runtime_root


def move_coverage(quotes,samples,start,end):
    """Fixed 30s tiles; no hindsight-selected starting lows or trading decisions."""
    times=[q['at'] for q in quotes];tiles=[]
    while start+30<=end:
        left=bisect_right(times,start)-1;right=bisect_right(times,start+30)
        tile=dict(start=start,end=start+30,status='unobserved')
        if left>=0 and start-times[left]<=1 and right>left and start+30-times[right-1]<=1:
            anchor=quotes[left]['bid'];future=quotes[left+1:right]
            hit=next((q for q in future if q['bid']>=anchor*(1+TIMING['diagnostic_move_bps']/10000)),None)
            tile.update(status='observed',move_100bps=bool(hit),anchor_bid=anchor)
            if hit:
                tile['first_hit_at']=hit['at']
                # Signal coverage, not portfolio exposure: count eligible signals
                # whose modeled fills precede the move threshold within this tile.
                tile['signal_before_hit']={family:any(s['family']==family and not s['blocked'] and
                    s['outcome']['status']=='resolved' and start<=s['at'] and
                    s['outcome']['entry_at']<=hit['at'] for s in samples)
                    for family in ('prebreak_pressure','breakout','breakout_accepted')}
        tiles.append(tile);start+=30
    return tiles


def main(root):
    root=validate_runtime_root(root)
    summary=json.loads((root/'summary.json').read_text());details={};pairs=[];lines=[
        '# Entry and exit timing findings','',
        'This report separates entry-family results from exits paired on identical fills. '
        'Results are research evidence, not a strategy release.', '',
        '| Entry (New York) | Net | Exit | Hold seconds | Above origin at decision | Bid extension in 30s after exit |',
        '|---|---:|---|---:|---:|---:|']
    for ticker,day,hour,book in WINDOWS:
        key=f'{ticker}-{day}'
        if summary[key]['status']!='completed':continue
        folder=root/key;analysis=json.loads((folder/'analysis.json').read_text());data=json.loads((folder/'inputs.json').read_text())
        for pair in analysis['paired_exits']:
            pairs.append(pair)
            o,t=pair['baseline'],pair['baseline_timing']
            stamp=datetime.fromtimestamp(pair['at'],NY).strftime('%H:%M:%S')
            extension=f'{t["post_exit_max_extension_bps"]/100:.2f}%' if t['post_exit_observed'] else 'unobserved'
            lines.append(f'| {ticker} {day} {stamp} | ${o["net"]:.2f} | {o["reason"]} | '
                         f'{t["holding_seconds"]:.2f} | {t["distance_above_level_bps"]/100:.2f}% | {extension} |')
        start=datetime.fromisoformat(day).replace(hour=hour,tzinfo=NY).timestamp()
        tiles=move_coverage(data['quotes'],analysis['opportunities'],start,start+1800)
        moves=[t for t in tiles if t.get('move_100bps')]
        details[key]=dict(tiles=tiles,observed_tiles=sum(t['status']=='observed' for t in tiles),
            upward_move_tiles=len(moves),without_eligible_signal={f:sum(not t['signal_before_hit'][f] for t in moves)
            for f in ('prebreak_pressure','breakout','breakout_accepted')})
    targets=[p for p in pairs if p['baseline']['reason']=='target']
    delays=[p['baseline_timing']['seconds_since_breakout'] for p in pairs if p['baseline_timing']['seconds_since_breakout'] is not None]
    lines += ['', '## What this establishes', '',
        f'- Paired trades: {len(pairs)}; changed exit timestamps: {sum(p["baseline"]["exit_at"]!=p["experimental"]["exit_at"] for p in pairs)}. '
        f'Baseline net ${sum(p["baseline"]["net"] for p in pairs):.2f}; experimental net ${sum(p["experimental"]["net"] for p in pairs):.2f}.',
        f'- Observed acceptance delays since linked breakout: {sorted(set(delays))} seconds. '
        'This measures detector confirmation delay, not how early the first price movement began. '
        'The table shows distance above the originating level; an old level acceptance '
        'can be a poor description of the current entry location.',
        f'- {sum(p["baseline_timing"]["holding_seconds"]<10 for p in pairs)} of {len(pairs)} exits occurred before 10 seconds. '
        'The new rule cannot address those cases because its minimum age is 10 seconds.',
        f'- {sum(p["baseline"]["net"]<0 for p in targets)} target exits lost money after costs. '
        'Geometric room to a resistance alone is insufficient; net executable reward matters. '
        f'{sum(p["baseline_timing"].get("post_exit_max_extension_bps") is not None and p["baseline_timing"]["post_exit_max_extension_bps"]>=100 for p in targets)} '
        f'of {len(targets)} target exits were followed by at least another 1% '
        'bid rise within 30 seconds. This is hindsight evidence to examine target importance, not a rule to hold every winner.',
        '- Entry families retain identical stop/target/horizon and gate policies. Raw breakout and pressure '
        'baselines still lose money on this sample; earlier entry alone has not demonstrated an edge.', '',
        '## Moves with no qualifying signal', '',
        'The following diagnostic partitions each window into fixed, non-overlapping 30s tiles. An upward '
        'move means the bid rises at least 100 bps from its as-of tile-start bid. A signal must pass the '
        'existing gates and have its modeled fill before that threshold is first hit. This counts signal '
        'coverage, not executable missed profits, and does not credit positions opened before the tile. '
        'Missing endpoint quotes are explicitly unobserved. No starting lows are selected with hindsight.', '',
        '| Window | Observed / 60 tiles | Upward moves | No pressure signal | No breakout signal | No acceptance signal |',
        '|---|---:|---:|---:|---:|---:|']
    for key,d in details.items():
        counts=d['without_eligible_signal']
        lines.append(f'| {key} | {d["observed_tiles"]} | {d["upward_move_tiles"]} | '
                     f'{counts["prebreak_pressure"]} | {counts["breakout"]} | {counts["breakout_accepted"]} |')
    lines += ['', '## Next decision', '',
        'Do not promote the stalled-exit experiment. The next entry/target design should measure the '
        'current encounter location and level importance, distinguish local barriers from meaningful outer '
        'resistance, and require net reward after costs. Then test partial profit/continuation management on '
        'the same entry cohort; the earlier runner experiment changed entry selection too and is not an '
        'isolated verdict on runners. Parameters must be specified before new results are inspected.', '',
        f'{sum(r["status"]=="failed" for r in summary.values())} requested windows remain failed; see summary.json for exact causes. '
        'There is no untouched ticker validation or deployable performance claim. No certification was bypassed.', '',
        '## Reproduce', '', 'From the laptop repository with its Python environment:', '', '```powershell',
        "$env:PYTHONDONTWRITEBYTECODE='1'",
        f'python -B scripts/evaluate_structural_timing.py --runtime {root.as_posix()} --input-root D:/TradingML/runtimes/research/managed-breakouts-20260911',
        f'python -B scripts/summarize_structural_timing.py --runtime {root.as_posix()}', '```', '',
        'The evaluator returns nonzero while windows remain failed. The summarizer reads completed results; '
        'it does not retry or conceal failed windows. Detailed errors and provenance are in summary.json and plan.json.']
    write(root/'move-coverage.json',dict(source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),windows=details))
    (root/'FINDINGS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'Wrote timing findings for {len(details)} completed windows; see {root / "FINDINGS.md"}')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--runtime',type=Path,required=True)
    main(p.parse_args().runtime)
