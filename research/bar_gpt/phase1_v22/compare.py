"""Reproduce the capacity comparison from immutable detailed evaluation receipts."""
import argparse,json
from pathlib import Path
import numpy as np


def mean(metrics,prefix,suffix):
    values=[v for k,v in metrics.items() if k.startswith(prefix) and k.endswith(suffix)]
    return float(np.mean(values)) if values else None


def summarize(name,m):
    return dict(name=name,close_1s=m['all_visible/raw_mae/1s/trade_close'],
        masked_close_1s=m['masked/raw_mae/1s/trade_close'],
        candle_direction=mean(m,'all_visible/candle/','/moving_direction_pct'),
        masked_candle_direction=mean(m,'masked/candle/','/moving_direction_pct'),
        activity_direction=mean(m,'activity/all_visible/','/balanced_direction_pct'),
        masked_activity_direction=mean(m,'activity/masked/','/balanced_direction_pct'),
        range_accuracy=mean(m,'all_visible/range/','/magnitude_accuracy_pct'))


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();root=a.output
    rows=json.loads((root/'comparison.json').read_text());parent=json.loads((root/'parent-metrics.json').read_text())
    report=[summarize('trained_parent',parent)];details=[]
    for row in rows:
        m=row['metrics'];s=summarize(row['name'],m);s.update({k:row[k] for k in ('parameters','decoder_parameters','representation_values','seconds','cached_windows_per_second','ranking','checkpoint_sha','wandb_url')});report.append(s)
        errors=[];direction=[]
        for k,v in m.items():
            if k.startswith(('masked/raw_mae/','all_visible/raw_mae/')) and parent.get(k,0)>0:
                errors.append(dict(metric=k,candidate=v,parent=parent[k],ratio=v/parent[k]))
            if k in parent and k.endswith(('moving_direction_pct','balanced_direction_pct','magnitude_accuracy_pct')) and not k.startswith('bar_probe/'):
                direction.append(dict(metric=k,candidate=v,parent=parent[k],difference_pp=v-parent[k]))
        details.append(dict(name=row['name'],worst_raw=sorted(errors,key=lambda z:-z['ratio'])[:15],worst_direction_range=sorted(direction,key=lambda z:z['difference_pp'])[:15]))
    (root/'summary.json').write_text(json.dumps(report,indent=2));(root/'worst-groups.json').write_text(json.dumps(details,indent=2))
    lines=['# V22 capacity comparison','',
        'Fixed development subset; same training exposure. Parent is already trained; capacity arms reset readouts. Not a held-out audit or forecasting test.','',
        '| Model | Params | Decoder | 1s close MAE | Masked close MAE | Candle direction | Activity balanced direction | Range accuracy | Windows/s |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in report:
        lines.append(f"| {r['name']} | {r.get('parameters','—')} | {r.get('decoder_parameters','—')} | ${r['close_1s']:.4f} | ${r['masked_close_1s']:.4f} | {r['candle_direction']:.2f}% | {r['activity_direction']:.2f}% | {r['range_accuracy']:.2f}% | {r.get('cached_windows_per_second',0):,.0f} |")
    lines+=['','Directions/range are equal-group means across available timeframe/stream groups, not pooled observations. Moving candle direction excludes unchanged candles; activity balanced direction averages class recalls. Masked metrics evaluate hidden positions only.','',
        'See summary.json for both masked/all-visible means and worst-groups.json for explicit regressions. Checkpoints remain local; W&B contains metrics/configuration only.','']
    (root/'COMPARISON.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
