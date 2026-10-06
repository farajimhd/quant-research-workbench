"""Exact-clock auxiliary current/next-four price targets from an admitted panel.

Current prices are recovered only when a later strictly-prior window certifies
their feature-row/clock identity. Unavailable boundary targets are masked and
counted. No arithmetic guess at feature-row identity is permitted.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
import json
from pathlib import Path
import subprocess
import numpy as np
import polars as pl
import torch
from research.rl_trading.v1.common import file_hash,digest
from research.rl_trading.v6.bias_panel import verify_panel,PRICE_DIVERSITY_VERSION


def observed_returns(data):
    clock=data['clock'];identity=data['identity'];day=clock//86_400_000_000
    last=data['windows'][:,-1];n=len(clock);keys=['day','identity','clock']
    lookup=pl.DataFrame(dict(day=day,identity=identity,clock=data['max_input_clock'],feature=last)).filter(pl.col('feature')>0)
    if lookup.group_by(keys).agg(pl.col('feature').n_unique().alias('n')).filter(pl.col('n')!=1).height:
        raise ValueError('One observed clock maps to multiple feature rows')
    lookup=lookup.unique(keys)
    flat=data['action']<2
    if not np.array_equal(data['held'][:,0]>0,~flat):raise ValueError('Known state/branch mismatch')
    actual=pl.DataFrame(dict(day=day[flat],identity=identity[flat],clock=clock[flat])).sort(keys)
    if actual.select(keys).n_unique()!=actual.height:raise ValueError('Duplicate actual flat price candle')
    request=pl.DataFrame(dict(day=day,identity=identity,clock=clock,order=np.arange(n)))
    values=np.zeros((n,5),np.float32);mask=np.zeros((n,5),bool);times=np.full((n,5),-1,np.int64);indexes=np.full((n,5),-1,np.int64)
    if (data['max_input_clock']>=clock).any():raise ValueError('Observation is not strictly prior')
    anchor=data['features'][last,3]
    for horizon in range(5):
        future=actual.with_columns(pl.col('clock').shift(-horizon).over('day','identity').alias('target_clock'))
        future=future.join(lookup.rename({'clock':'target_clock'}),on=['day','identity','target_clock'],how='left',validate='m:1')
        joined=request.join(future.select(*keys,'target_clock','feature'),on=keys,how='left',validate='m:1').sort('order')
        index=joined['feature'].fill_null(-1).to_numpy();target_clock=joined['target_clock'].fill_null(-1).to_numpy()
        valid=(index>0)&(last>0)
        if ((target_clock[valid]<clock[valid])|(target_clock[valid]//86_400_000_000!=day[valid])).any():raise ValueError('Future target escaped same session')
        if (data['features'][index[valid],35]<.5).any():raise ValueError('Forecast target is not a valid price candle')
        values[valid,horizon]=(data['features'][index[valid],3]-anchor[valid])*10_000
        mask[:,horizon]=valid;times[:,horizon]=target_clock;indexes[:,horizon]=index
    if not np.isfinite(values).all():raise ValueError('Nonfinite price forecast target')
    for horizon in range(1,5):
        valid=mask[:,horizon]&mask[:,horizon-1]
        if (times[valid,horizon]<=times[valid,horizon-1]).any():raise ValueError('Future actual candles are not ordered')
    return dict(return_bps=values,mask=mask,target_close_us=times,target_feature_index=indexes,
        unavailable_counts=(~mask).sum(0).tolist(),no_prior_price_rows=int((last==0).sum()),
        unavailability_reason='no prior priced anchor or no exact later-window clock binding within admitted session')


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--panel',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve();root=args.panel.resolve();output=args.output.resolve()
    if not runtime.is_dir() or any(not p.is_relative_to(runtime) for p in (root,output)) or output.exists():raise ValueError('Fresh laptop runtime required')
    proof=json.loads((root/'complete.json').read_text());manifest=json.loads((root/'manifest.json').read_text())
    if proof['status']!='prepared' or manifest['version']!=PRICE_DIVERSITY_VERSION or proof['panel_sha256']!=file_hash(root/'panel.pt') or proof['manifest_sha256']!=file_hash(root/'manifest.json') or manifest['hash']!=digest({k:v for k,v in manifest.items() if k!='hash'}):raise ValueError('Authenticated diversity panel required')
    panel=torch.load(root/'panel.pt',map_location='cpu',weights_only=False);verify_panel(panel,manifest)
    if panel['train']['clock'].max()>=panel['calibration']['clock'].min():raise ValueError('Calibration chronology changed')
    targets={fold:observed_returns(data) for fold,data in panel.items()}
    train=targets['train'];scale=max(1.,float(np.quantile(np.abs(train['return_bps'][train['mask']]),.9)))
    output.mkdir();torch.save(targets,output/'targets.pt')
    (output/'complete.json').write_text(json.dumps(dict(status='prepared',version='rl-v6-exact-clock-price-forecast-targets-v1',
        panel_sha256=proof['panel_sha256'],targets_sha256=file_hash(output/'targets.pt'),train_only_scale_bps=scale,
        source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),source_sha256=file_hash(Path(__file__)),
        counts={fold:dict(rows=len(v['mask']),available=v['mask'].sum(0).tolist(),unavailable=v['unavailable_counts'],reason=v['unavailability_reason']) for fold,v in targets.items()},
        sealed_labels_read=False,observation_contract_changed=False),indent=2))
    print('Prepared exact-clock price targets; TRAIN-only scale bps',scale,flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
