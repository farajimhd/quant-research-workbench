"""Static research figure from saved, reproducible development learning curves."""
import argparse,json,os
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);root=p.parse_args().output
    os.environ['MPLCONFIGDIR']=str(root/'matplotlib-config')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    curves=json.loads((root/'checkpoint-audit.json').read_text())
    results={x['name']:x for x in json.loads((root/'summary.json').read_text()) if 'parameters' in x}
    fig,axes=plt.subplots(2,2,figsize=(11,7),layout='constrained')
    fields=[('close_1s','1s trade-close MAE ($)'),('candle_direction','Moving-candle direction (%)'),('range_accuracy','Range-bin accuracy (%)'),('activity_direction','Activity balanced direction (%)')]
    for row in sorted(curves,key=lambda x:results[x['name']]['parameters']):
        n=results[row['name']]['parameters']/1e6
        x=[v['seen']/1e6 for v in row['curve']]
        for ax,(key,title) in zip(axes.flat,fields):
            ax.plot(x,[v['summary'][key] for v in row['curve']],marker='o',linestyle='--' if row['name'].endswith('lr3') else '-',label=f"{n:.1f}M LR×{results[row['name']]['lr_multiplier']:g}")
            ax.set(title=title,xlabel='Training-window exposures (millions)');ax.grid(alpha=.25)
    axes[0,0].set_yscale('log');axes[0,0].legend(title='Total parameters')
    fig.suptitle('V24: matched data and decoder, training-only LR selection\nAll-visible development reconstruction; no forecasting claim')
    fig.savefig(root/'learning-curves.png',dpi=160);plt.close(fig)


if __name__=='__main__':main()
