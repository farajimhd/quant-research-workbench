"""Reproducible larger-encoder comparison from saved full-development receipts."""
import argparse,json,math
from pathlib import Path
from ..phase1_v22.compare import summarize


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True)
    p.add_argument('--additional-root',type=Path,action='append',default=[]);a=p.parse_args();root=a.output
    rows=json.loads((root/'comparison.json').read_text());parent=json.loads((root/'parent-metrics.json').read_text())
    for additional in a.additional_root:
        primary_manifest=json.loads((root/'manifest.json').read_text());extra_manifest=json.loads((additional/'manifest.json').read_text())
        for key in ('parent_sha','data_hash','supplement_hash','split_hash','windows_per_arm'):
            if primary_manifest[key]!=extra_manifest[key]:raise ValueError('Incomparable campaign: '+key)
        rows+=json.loads((additional/'comparison.json').read_text())
    summary=[summarize('V22 starting checkpoint',parent)];worst=[]
    controls=[r for r in rows if r['architecture']==dict(width=128,depth=2) and r['seed']==559]
    control=controls[0]['metrics'] if controls else None
    for row in rows:
        m=row['metrics'];entry=summarize(row['name'],m)
        entry.update({k:row[k] for k in ('architecture','parameters','decoder_parameters','seconds','cached_windows_per_second','checkpoint_sha','wandb_url','ranking')})
        entry['sub_010_median_pct']=m.get('all_visible/audit/price_band_0_0.1/median_pct')
        entry['sub_010_count']=m.get('all_visible/audit/price_band_0_0.1/count')
        entry['train_subset']=summarize('fixed first2048 training windows',row['train_metrics'])
        summary.append(entry)
        mae=[];direction=[]
        for k,v in m.items():
            if k.startswith(('masked/raw_mae/','all_visible/raw_mae/')) and parent.get(k,0)>0:mae.append(dict(metric=k,parent=parent[k],candidate=v,ratio=v/parent[k]))
            if k in parent and k.endswith(('moving_direction_pct','balanced_direction_pct','magnitude_accuracy_pct')) and not k.startswith('bar_probe/'):
                direction.append(dict(metric=k,difference_pp=v-parent[k]))
        record=dict(name=row['name'],raw=sorted(mae,key=lambda r:-r['ratio'])[:15],direction_range=sorted(direction,key=lambda r:r['difference_pp'])[:15])
        if control:
            pairs=[dict(metric=k,control=control[k],candidate=v,ratio=v/control[k]) for k,v in m.items() if k.startswith(('masked/raw_mae/','all_visible/raw_mae/')) and control.get(k,0)>0]
            record['matched_control']=dict(name=controls[0]['name'],raw_geomean_ratio=math.exp(sum(math.log(max(p['ratio'],1e-9)) for p in pairs)/len(pairs)),groups_improved=sum(p['ratio']<1 for p in pairs),groups=len(pairs),worst=sorted(pairs,key=lambda p:-p['ratio'])[:15])
        worst.append(record)
    (root/'summary.json').write_text(json.dumps(summary,indent=2));(root/'worst-groups.json').write_text(json.dumps(worst,indent=2))
    lines=['# V23 larger temporal encoders','',
        'Same trained V22 starting checkpoint; same data, masks, loss and decoder. Widthlr runs divide temporal learning rates by width/128, leaving feature and decoder rates unchanged. Function-preserving FP32 initialization; BF16 initial differences recorded separately.','',
        '| Model | Parameters | 1s close MAE | Masked close MAE | Candle direction | Activity balanced direction | Range accuracy | Sub-$0.10 median error | Windows/s |',
        '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for r in summary:
        lines.append(f"| {r['name']} | {r.get('parameters','--')} | ${r['close_1s']:.4f} | ${r['masked_close_1s']:.4f} | {r['candle_direction']:.2f}% | {r['activity_direction']:.2f}% | {r['range_accuracy']:.2f}% | {r.get('sub_010_median_pct',parent['all_visible/audit/price_band_0_0.1/median_pct']):.2f}% | {r.get('cached_windows_per_second',0):,.0f} |")
    lines+=['','Metrics are full development, not held-out or forecasting results. Candle direction excludes unchanged candles; activity accuracy is macro down/flat/up recall. Direction/range are equal-group means across timeframes and streams. Masked raw errors evaluate hidden positions. Price bands group valid target price fields, not securities. All decoders have 594 parameters.','',
        'Windows/s measures cached training updates, including gather, mask generation, optimizer and synchronization; it excludes startup and validation. Experiment seconds include qualification, profiling and evaluation. Training metrics use a fixed 2,048-window subset, so train/development gaps also reflect different coverage.','']
    (root/'COMPARISON.md').write_text('\n'.join(lines),encoding='utf-8');print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
