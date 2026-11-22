"""Read-only report from completed native results; runtime output only."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json,math
from pathlib import Path
from .metrics import summary
from ..v5.shards.phase1_contract import CHANNELS,write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args();root=a.output
    rows=json.loads((root/'comparison.json').read_text());control=next(r for r in rows if r['name'].startswith('e02'))
    price={k for i,k in enumerate(CHANNELS) if i%6<4};report=[]
    for row in rows:
        groups={}
        for family in ('price','activity'):
            pairs=[dict(metric=k,ratio=v/control['metrics'][k]) for k,v in row['metrics'].items() if k.startswith('all_visible/raw_mae/') and (k.split('/')[-1] in price)==(family=='price') and control['metrics'].get(k,0)>0]
            if pairs:groups[family]=dict(raw_geomean_vs_shared_light=math.exp(sum(math.log(max(x['ratio'],1e-12)) for x in pairs)/len(pairs)),worst_groups=sorted(pairs,key=lambda x:-x['ratio'])[:6])
        report.append(dict(name=row['name'],kind=row['kind'],mask=row['mask'],parameters=row['parameters'],peaks=row['peaks'],steps=row['steps'],seen=row['seen'],seconds=row['seconds'],speed=row['speed'],groups=groups,
            development={mode:summary(row['metrics'],mode) for mode in ('all_visible','hidden8','hidden48')},train=summary(row['train_metrics']),checkpoint_sha=row['checkpoint_sha'],wandb_url=row['wandb_url']))
    write_json(root/'report.json',report)
    lines=['# V25 bounded family/masking comparison','',f'Completed experiments: {len(rows)}/8.','',
        'Fresh initialization; fixed training-only transforms; no teacher anchor. Each main run has equal window exposure and batch256. Price MAE below means 1s trade-close dollars. Range/candle/activity are equal-group percentages; activity uses balanced down/flat/up recall.','',
        '| Experiment | Parameters | Peak LR | Close MAE | Candle % | Range % | Activity % | Windows/s | Seconds |',
        '|---|---:|---|---:|---:|---:|---:|---:|---:|']
    def fmt(x):return 'N/A' if x is None else f'{x:.4f}'
    for r in report:
        s=r['development']['all_visible'];lines.append(f"| {r['name']} | {r['parameters']:,} | {r['peaks']} | {fmt(s['close_mae'])} | {fmt(s['candle'])} | {fmt(s['range'])} | {fmt(s['activity'])} | {r['speed']:,.0f} | {r['seconds']:.1f} |")
    lines+=['','All per-channel metrics, denominator counts, price bands, variance and structure diagnostics remain in each evaluation file. Missing-family results are N/A, not zero. `report.json` contains hidden8/hidden48 results, train/development comparison and worst family regressions relative to shared-light.','',
        'This is a bounded development comparison, not a full-dataset run or evidence of forecasting improvement. Run8 repeats a provisional all-visible raw/directional winner with another seed. Its initial selection score does not explicitly optimize range-bin accuracy; inspect range and worst groups before recommending a candidate.']
    (root/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
    os.environ['MPLCONFIGDIR']=str(root/'matplotlib-config')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(2,2,figsize=(12,7),layout='constrained')
    fields=[('close_mae','1s close MAE ($)'),('candle','Candle direction (%)'),('range','Range-bin accuracy (%)'),('activity','Activity balanced direction (%)')]
    colors=plt.get_cmap('tab10');handles=[]
    from matplotlib.lines import Line2D
    for index,r in enumerate(rows):
        color=colors(index);handles.append(Line2D([],[],color=color,marker='o',label=r['name']))
        curves=json.loads((root/r['name']/'curve.json').read_text());values=[summary(c['metrics']) for c in curves]
        for ax,(key,title) in zip(axes.flat,fields):
            if values[-1][key] is not None:ax.plot([c['step']*256/1e6 for c in curves],[v[key] for v in values],marker='o',color=color,label=r['name'])
            ax.set(title=title,xlabel='Training-window exposures (millions)');ax.grid(alpha=.25)
    axes[0,0].set_yscale('log');fig.legend(handles=handles,loc='outside lower center',fontsize=7,ncol=4)
    fig.suptitle('V25: fresh small encoders, family separation and masking\nAll-visible development metrics; hidden-bar results reported separately')
    fig.savefig(root/'learning-curves.png',dpi=150);plt.close(fig)
    print('\n'.join(lines))
if __name__=='__main__':main()
