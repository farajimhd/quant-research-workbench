"""Rebuild comparable scalar and all-channel evidence from immutable run outputs."""
import argparse,csv,json
from pathlib import Path
from ..v5.shards.phase1_contract import write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);a=p.parse_args();records=[];channels=[]
    decisions=json.loads((a.root/'decisions.json').read_text()) if (a.root/'decisions.json').exists() else {}
    for folder in sorted(a.root.glob('e*')):
        if not (folder/'results.json').exists():continue
        result=json.loads((folder/'results.json').read_text());m=json.loads((folder/'manifest.json').read_text())
        profile=json.loads((folder/'profile.json').read_text());best=max(profile['configurations'],key=lambda r:r['origins_per_second'])
        record=dict(name=folder.name,source_revision=m['source_revision'],dataset_hash=m['dataset_hash'],config=m['config'],
            hypothesis=m['hypothesis'],parent=m['parent'],parameters=m['parameter_count'],encoder_parameters=m['window_encoder_parameters'],
            reconstruction_decoder_parameters=m['reconstruction_decoder_parameters'],examples=result['examples'],steps=result['step'],
            seconds=result['elapsed_seconds'],profile=best,checkpoint_sha256=result['checkpoint_sha256'],
            reconstruction_weight=m['reconstruction_encoder_weight'],masked_probability=m.get('masked_reconstruction_probability',0),
            forecast_dependency=m['config'].get('anchor_mode','historical'),
            decision=decisions.get(folder.name,'Pending evidence review'),metrics={})
        for split,v in result['evaluations'].items():
            record['metrics'][split]={k:v[k] for k in ('price_mae_skill','activity_mae_skill','price_session_bootstrap95','activity_session_bootstrap95','selection_score')}
            record['metrics'][split].update(reconstruction_price_relative=v['reconstruction']['balanced_price_relative_mean'],
                reconstruction_activity_log=v['reconstruction']['balanced_activity_log_mae'],reconstruction_worst_price_group=v['reconstruction']['worst_price_band_relative_mean'])
            for task in ('native_forecast','native_persistence','reconstruction'):
                for frame,f in v[task]['frames'].items():
                    for name,c in f['channels'].items():
                        channels.append(dict(run=folder.name,split=split,task=task,timeframe_us=frame,channel=name,count=c['count'],mae=c['mae'],
                            relative_mean=c.get('relative',{}).get('mean'),relative_median=c.get('relative',{}).get('median'),relative_p90=c.get('relative',{}).get('p90'),
                            activity_log_mae=c.get('log1p_absolute_error',{}).get('mean'),zero_denominators=c['zero_denominators'],tiny_denominators=c['tiny_denominators'],
                            frame_nonfinite=f['nonfinite'],frame_negative=f['negative']))
        records.append(record)
    write_json(a.root/'comparison.json',dict(selection='Future performance primary; reconstruction supporting. No final recommendation from sorting alone.',experiments=records))
    if channels:
        with (a.root/'all-channel-comparison.csv').open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(channels[0]));writer.writeheader();writer.writerows(channels)
    lines=['# V9 campaign comparison','',
        'Forecast skill is percentage error reduction versus the causal last-bar predictor. Positive is better. Reconstruction is mean price-relative error, not forecasting skill. Development and unseen-ticker development are shown separately. Best checkpoints are selected within each run; final optimizer steps can differ from the selected checkpoint.',
        '', 'Historical-anchor runs are causal conditional controls. They are not eligible for the final embedding-only forecasting recommendation. Learned-anchor runs decode the reference from the exported embedding.',
        '', '| Run | Forecast anchor | Price skill dev / novel | Activity skill dev / novel | Reconstruction dev / novel | Seconds | Profile origins/s |',
        '|---|---|---:|---:|---:|---:|---:|']
    for r in records:
        d=r['metrics']['development'];n=r['metrics']['novel_development']
        pair=lambda key:f'{100*d[key]:.2f}% / {100*n[key]:.2f}%'
        lines.append(f"| {r['name']} | {r['forecast_dependency']} | {pair('price_mae_skill')} | {pair('activity_mae_skill')} | {pair('reconstruction_price_relative')} | {r['seconds']:.1f} | {r['profile']['origins_per_second']:.0f} |")
    lines+=['','Each forecast example contains sixteen 64-bar windows. Profile throughput includes complete optimizer steps; experiment seconds also include preparation, tiny fit, profiling and evaluation. Different data, batch, update and parameter budgets must not be treated as isolated architecture effects.','',
        'Native units, all 18 channels, denominator counts and nonfinite counts: `all-channel-comparison.csv`. Full price bands, quantiles, structure, variance, confidence intervals and controls remain in each immutable `results.json`.','', '## Decisions','']
    lines += [f"- **{r['name']}**: {r['decision']}" for r in records]
    (a.root/'COMPARISON.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(f'{len(records)} completed experiments; {len(channels)} native channel rows')


if __name__=='__main__':main()
