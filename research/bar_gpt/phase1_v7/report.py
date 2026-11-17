"""Deterministic campaign comparison from immutable run evidence."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
from ..v5.shards.phase1_contract import write_json


def compact(m):
    worst=[]
    for frame,f in m['frames'].items():
        for channel,c in f['channels'].items():
            for band,b in c.get('bands',{}).items():
                if b['count']:worst.append(dict(frame=frame,channel=channel,band=band,**b))
    return dict(price_relative_mean=m['balanced_price_relative_mean'],activity_log_mae=m['balanced_activity_log_mae'],
        worst_band=m['worst_price_band_relative_mean'],worst_groups=sorted(worst,key=lambda x:x['mean'],reverse=True)[:8],
        nonfinite=sum(f['nonfinite'] for f in m['frames'].values()),negative=sum(f['negative'] for f in m['frames'].values()),
        ohlc_violations=sum(f['structure'][s]['prediction'] for f in m['frames'].values() for s in ('trade','bid','ask')),
        crossed_quotes=sum(f['structure']['crossed_close']['prediction'] for f in m['frames'].values()),
        target_crossed_quotes=sum(f['structure']['crossed_close']['target'] for f in m['frames'].values()))


def build(root):
    ledger=json.loads((root/'campaign.json').read_text());items=[]
    for entry in ledger['experiments']:
        run=root/entry['name']
        if not (run/'results.json').exists():items.append(dict(name=entry['name'],state=entry['state']));continue
        r=json.loads((run/'results.json').read_text());identity=json.loads((run/'manifest.json').read_text())
        items.append(dict(name=entry['name'],state=r['state'],identity=identity,elapsed_seconds=r['elapsed_seconds'],
            checkpoint_sha256=r['checkpoint_sha256'],gpu_peak_bytes=r['gpu_peak_bytes'],
            profile=json.loads((run/'profile.json').read_text()),
            metrics={name:compact(m) for name,m in r['evaluations'].items()},probes=r['probes']))
    write_json(root/'comparison.json',dict(experiments=items,dataset=json.loads((root/'dataset/manifest.json').read_text())))
    lines=['V7 lightweight decoder campaign','All reported relative errors are fractions; activity uses log1p MAE.']
    for r in items:
        if r['state']!='complete':lines.append(f"{r['name']}: {r['state']}");continue
        m=r['metrics']['development_full'];masked=r['metrics']['development_masked']
        lines.append(f"{r['name']}: full price {m['price_relative_mean']:.6g}, activity {m['activity_log_mae']:.6g}, worst {m['worst_band']:.6g}; masked price {masked['price_relative_mean']:.6g}, activity {masked['activity_log_mae']:.6g}; seconds {r['elapsed_seconds']:.1f}; OHLC {m['ohlc_violations']}, negative {m['negative']}, nonfinite {m['nonfinite']}")
    (root/'comparison.txt').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();build(a.root)
