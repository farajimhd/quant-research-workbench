"""Assess actual replay coverage, major-move timing, and winner retention."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
import argparse
import json
from pathlib import Path
from statistics import median

ROOT=Path('D:/TradingML/runtimes/analysis/strategy-222-refinement')


def assess_cases(cases,trials):
    by_run={t['run_id']:t for t in trials}
    def scope(value):
        return 'portfolio' if by_run.get(value['run_id'],{}).get('symbol')=='PORTFOLIO' else 'isolated'
    names=sorted({(v['name'],scope(v)) for c in cases for v in c['variants']})
    result=[]
    for name,run_scope in names:
        covered=[];moves={}
        for case in cases:
            options=[v for v in case['variants'] if v['name']==name and scope(v)==run_scope]
            if not options:continue
            selected=max(options,key=lambda v:(by_run.get(v['run_id'],{}).get('end',''),v['run_id']))
            covered.append(case['position'])
            if not case.get('major_move'):continue
            key=case['opportunity_id']
            if key in moves:
                moves[key]['positions'].append(case['position']);continue
            baselines=[v for v in case['variants'] if v['name']=='corrected-baseline' and scope(v)==run_scope]
            baseline=max(baselines,key=lambda v:(by_run.get(v['run_id'],{}).get('end',''),v['run_id'])) if baselines else None
            fraction=selected.get('fraction_of_price_move_before_entry')
            entry_net=selected.get('episode_net')
            baseline_net=baseline.get('episode_net') if baseline else None
            issues=[]
            if selected['status']=='no_position_during_labeled_move':issues.append('major_move_missed')
            if fraction is not None and fraction>.5:issues.append('entered_after_half_of_labeled_price_advance')
            if entry_net is not None and entry_net<=0:issues.append('first_overlapping_entry_did_not_profit')
            if selected['status']=='entered_before_peak' and entry_net is None:issues.append('first_overlapping_episode_has_no_realized_result')
            retention=entry_net/baseline_net if entry_net is not None and baseline_net is not None and baseline_net>=200 else None
            if retention is not None and retention<.9:issues.append('large_baseline_winner_reduced_by_more_than_ten_percent')
            moves[key]=dict(opportunity_id=key,symbol=case['symbol'],positions=[case['position']],
                selected=selected,baseline=baseline,large_winner_retention_ratio=retention,issues=issues)
        fractions=[m['selected']['fraction_of_price_move_before_entry'] for m in moves.values()
                   if m['selected'].get('fraction_of_price_move_before_entry') is not None]
        result.append(dict(variant=name,scope=run_scope,covered_positions=covered,coverage=len(covered),
            total_positions=len(cases),all_positions_covered=len(covered)==len(cases),
            major_opportunities_covered=len(moves),
            major_opportunities_entered=sum(m['selected']['status']=='entered_before_peak' for m in moves.values()),
            median_price_advance_before_entry=median(fractions) if fractions else None,
            flagged_major_opportunities=sum(bool(m['issues']) for m in moves.values()),
            major_opportunities=list(moves.values()),
            next_action='resolve_flagged_moves_and_validate_shared_capital' if len(covered)==len(cases) else 'finish_position_coverage'))
    return result


def assess(root):
    root=root.resolve();root.relative_to(Path('D:/TradingML/runtimes').resolve())
    cases=json.loads((root/'position-comparison.json').read_text())['cases']
    trials=json.loads((root/'comparison.json').read_text())
    rows=assess_cases(cases,trials)
    document=dict(method='Actual fills are evaluated against the frozen original-position opportunity labels. Shared opportunity IDs are counted once. The first overlapping episode determines timing and episode profit; later profits do not erase an earlier false entry. More than half the labeled price advance and a greater-than-10-percent reduction of a baseline episode worth at least $200 are diagnostic flags, not automatic release criteria.',
        limitations='Only completed replay coverage is assessed. Isolated ticker results are not a shared-capital portfolio result. Familiar-session evidence cannot establish unseen robustness. No goal completion or live activation is automatic.',
        variants=rows)
    (root/'assessment.json').write_text(json.dumps(document,indent=2),encoding='utf-8')
    lines=['# Strategy 222 replay assessment','',document['method'],'',document['limitations'],'',
        '| Variant | Original positions covered | Unique major moves entered / covered | Median price advance before entry | Major moves flagged | Next action |',
        '|---|---:|---:|---:|---:|---|']
    for row in rows:
        fraction=row['median_price_advance_before_entry']
        lines.append(f"| {row['variant']} ({row['scope']}) | {row['coverage']}/{row['total_positions']} | {row['major_opportunities_entered']}/{row['major_opportunities_covered']} | {f'{fraction:.1%}' if fraction is not None else 'n/a'} | {row['flagged_major_opportunities']} | {row['next_action']} |")
        print(f"{row['variant']}: coverage {row['coverage']}/{row['total_positions']}; {row['flagged_major_opportunities']} major opportunities require review",flush=True)
    for row in rows:
        if row['variant']=='corrected-baseline':continue
        flagged=[m for m in row['major_opportunities'] if m['issues']]
        if not flagged:continue
        lines.extend(['',f"## {row['variant']}: follow-up cases",''])
        for move in flagged:
            lines.append(f"- {move['symbol']} positions {','.join(map(str,move['positions']))}: {'; '.join(move['issues'])}. Run `{move['selected']['run_id']}`.")
    (root/'assessment.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return document


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,default=ROOT)
    assess(parser.parse_args().runtime)
