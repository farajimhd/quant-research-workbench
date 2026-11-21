"""Compare complete, paired validation receipts without running inference."""
import argparse
import json
import math
from pathlib import Path


def compare(fixed, control):
    receipts = [json.loads((p / 'manifest.json').read_text()) for p in (fixed, control)]
    for key in ('source', 'checkpoint_sha256', 'split_hash', 'calibration_sha256', 'batch_size', 'workers'):
        if receipts[0][key] != receipts[1][key]:
            raise ValueError(f'Unpaired validation: {key}')
    if [r['variant'] for r in receipts] != ['stable_balanced', 'stable']:
        raise ValueError('Expected count-balanced candidate and count-unbalanced control')
    results = [json.loads((p / 'result.json').read_text()) for p in (fixed, control)]
    if not all(r['full_population'] for r in results) or results[0]['windows'] != results[1]['windows']:
        raise ValueError('Incomplete or different validation populations')
    fm, cm = [r['metrics'] for r in results]
    masked = [k for k in fm if k.startswith('masked/')]
    changed = {k: [cm[k], fm[k]] for k in masked if fm[k] != cm[k]}
    keys = [k for k in fm if k.startswith('all_visible/raw_mae/')]
    rows = [{'metric': k, 'control': cm[k], 'fixed': fm[k],
             'fixed_over_control': fm[k] / cm[k] if cm[k] else None} for k in keys]
    ratios = [r['fixed_over_control'] for r in rows if r['fixed_over_control'] is not None]
    return dict(windows=results[0]['windows'], receipts=receipts,
                masked_metrics=len(masked), masked_changed=changed,
                raw_mae_groups=len(rows), improved=sum(r < 1 for r in ratios),
                geomean_ratio=math.exp(sum(math.log(r) for r in ratios) / len(ratios)),
                worst_groups=sorted(rows, key=lambda r: r['fixed_over_control'] or 0, reverse=True)[:12],
                all_raw_mae=rows,
                seconds=dict(fixed=results[0]['seconds'], control=results[1]['seconds']),
                wandb=dict(fixed=results[0]['wandb_url'], control=results[1]['wandb_url']))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--fixed', type=Path, required=True)
    p.add_argument('--control', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    result = compare(a.fixed, a.control)
    with a.output.open('x', encoding='utf-8') as f:
        json.dump(result, f, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k not in ('receipts', 'all_raw_mae')}, indent=2))
    if result['masked_changed']:
        raise RuntimeError('Masked metrics changed: paired control requires investigation')


if __name__ == '__main__':
    main()
