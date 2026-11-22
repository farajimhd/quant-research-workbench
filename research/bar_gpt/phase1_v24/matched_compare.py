"""Compare recorded evaluations at an identical exposure count, without inference."""
import argparse,json,math
from pathlib import Path
from ..phase0_v1.metrics import scalars
from ..phase1_v17.metrics import scalars as activity_scalars
from ..phase1_v22.compare import summarize


def flatten(detail):
    metrics={}
    for mode in ('masked','all_visible','bar_probe'):
        metrics.update({mode+'/'+k:v for k,v in scalars(detail[mode]).items()})
        metrics.update({'activity/'+mode+'/'+k:v for k,v in activity_scalars(detail['activity_'+mode]).items()})
    metrics.update({'all_visible/audit/'+k:v for k,v in detail['capacity_audit'].items()})
    return metrics


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--root',type=Path,action='append',required=True)
    p.add_argument('--seen',type=int,required=True);a=p.parse_args()
    if a.seen<=0:raise ValueError('Positive exposure required')
    reference=None;rows=[]
    for root in a.root:
        manifest=json.loads((root/'manifest.json').read_text())
        identity={k:manifest[k] for k in ('parent_sha','data_hash','supplement_hash','split_hash')}
        if reference is None:reference=identity
        if identity!=reference:raise ValueError('Different parent/data: '+str(root))
        for f in sorted(root.glob('e*/result.json')):
            result=json.loads(f.read_text());arm=f.parent
            if result['seen']<a.seen:raise ValueError('Incomplete requested exposure: '+str(arm))
            detail=json.loads((arm/f'evaluation-{a.seen}.json').read_text());metrics=flatten(detail)
            row=summarize(result['name'],metrics)
            row.update(parameters=result['parameters'],seen=a.seen,steps=a.seen//result['batch'],
                batch=result['batch'],seed=result['seed'],evaluation=str(arm/f'evaluation-{a.seen}.json'),
                lr=json.loads((arm/'manifest.json').read_text())['learning_rates'],metrics=metrics)
            rows.append(row)
    if not rows:raise ValueError('No completed arms')
    if len({(r['batch'],r['seed']) for r in rows})!=1:raise ValueError('Unmatched updates/seed')
    control=next(r for r in rows if r['parameters']==7788482)['metrics']
    for row in rows:
        pairs=[dict(metric=k,ratio=v/control[k]) for k,v in row['metrics'].items() if k.startswith(('masked/raw_mae/','all_visible/raw_mae/')) and control.get(k,0)>0]
        row['raw_geomean_vs_control']=math.exp(sum(math.log(max(x['ratio'],1e-9)) for x in pairs)/len(pairs))
        row['worst_raw_vs_control']=sorted(pairs,key=lambda x:-x['ratio'])[:10]
    a.output.mkdir(parents=True,exist_ok=True)
    (a.output/f'matched-{a.seen}.json').write_text(json.dumps(dict(identity=reference,seen=a.seen,rows=rows),indent=2))
    lines=[f'# Matched comparison: {a.seen:,} training-window exposures','',
        'All rows use the same parent, training draws, masks, batch and update count. LR policies differ. Development metrics only; no forecasting evidence.','',
        '| Model | Parameters | Close MAE | Candle direction | Activity direction | Range accuracy | Raw MAE ratio vs control |',
        '|---|---:|---:|---:|---:|---:|---:|']
    for r in sorted(rows,key=lambda x:x['parameters']):
        lines.append(f"| {r['name']} | {r['parameters']:,} | ${r['close_1s']:.4f} | {r['candle_direction']:.2f}% | {r['activity_direction']:.2f}% | {r['range_accuracy']:.2f}% | {r['raw_geomean_vs_control']:.3f} |")
    (a.output/f'MATCHED-{a.seen}.md').write_text('\n'.join(lines),encoding='utf-8')
    print('\n'.join(lines))


if __name__=='__main__':main()
