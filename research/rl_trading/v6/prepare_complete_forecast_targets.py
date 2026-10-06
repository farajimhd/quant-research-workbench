"""Authenticated flat/held five-candle targets; original panels stay immutable."""
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
from research.rl_trading.v6.teacher_forecast import ForecastWindows,VERSION as FORECAST_VERSION
from research.rl_trading.v6.run_bias_campaign import write


def target_frame(frame,day):
    frame=frame.sort(['listing_id','time_us']);parts=[]
    for held,windows in ((False,ForecastWindows.from_frame(frame)),(True,ForecastWindows.from_reference_frame(frame))):
        indices=np.arange(frame.height)[:,None]+np.arange(5)[None]
        mask=indices<windows.ends[:,None];safe=np.minimum(indices,max(0,frame.height-1))
        actions=np.where(mask,windows.actions[safe],-1)
        probabilities=windows.probabilities[safe]
        quality=np.take_along_axis(probabilities,actions.clip(min=0)[...,None],axis=2)[...,0]
        clocks=np.where(mask,windows.clocks[safe],0)
        keys=frame.select((pl.lit(day+':')+pl.col('listing_id')+':pair:'+pl.col('pair_id').cast(pl.String)).alias('episode'),pl.col('time_us').alias('clock')).with_columns(pl.lit(held).alias('held'))
        parts.append(keys.hstack(pl.DataFrame({**{f'a{h}':actions[:,h] for h in range(5)},**{f'q{h}':np.where(mask[:,h],quality[:,h],0.) for h in range(5)},**{f't{h}':clocks[:,h] for h in range(5)}})))
    return pl.concat(parts)


def align(data,frame):
    keys=['episode','clock','held']
    if frame.select(keys).n_unique()!=frame.height:raise ValueError('Duplicate forecast target key')
    request=pl.DataFrame(dict(episode=data['episode'],clock=data['clock'],held=data['action']>=2,order=np.arange(len(data['action']))))
    joined=request.join(frame,on=keys,how='left',validate='m:1').sort('order')
    if joined['a0'].null_count():raise ValueError('Missing authenticated forecast row')
    actions=joined.select([f'a{h}' for h in range(5)]).to_numpy().astype(np.int64)
    clocks=joined.select([f't{h}' for h in range(5)]).to_numpy().astype(np.int64)
    if (actions[:,0]<0).any() or not np.array_equal(clocks[:,0],data['clock']):raise ValueError('Missing current forecast coverage')
    held=data['action']>=2
    if not np.array_equal(actions[held,0],data['action'][held]):raise ValueError('Held current action parity changed')
    if not np.array_equal(actions[~held],data['future'][~held]):raise ValueError('Original flat forecasts changed')
    return dict(future=actions,future_quality=joined.select([f'q{h}' for h in range(5)]).to_numpy().astype(np.float32),future_clock=clocks)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--panel',type=Path,required=True);p.add_argument('--output',type=Path,required=True);args=p.parse_args(argv)
    runtime=Path('D:/TradingML/runtimes').resolve();root=args.panel.resolve();output=args.output.resolve()
    if not runtime.is_dir() or any(not x.is_relative_to(runtime) for x in (root,output)) or output.exists():raise ValueError('Fresh laptop runtime required')
    proof=json.loads((root/'complete.json').read_text());manifest=json.loads((root/'manifest.json').read_text())
    if proof['status']!='prepared' or manifest['version']!=PRICE_DIVERSITY_VERSION or proof['panel_sha256']!=file_hash(root/'panel.pt') or proof['manifest_sha256']!=file_hash(root/'manifest.json') or manifest['hash']!=digest({k:v for k,v in manifest.items() if k!='hash'}):raise ValueError('Panel authentication failed')
    panel=torch.load(root/'panel.pt',map_location='cpu',weights_only=False);verify_panel(panel,manifest)
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path.cwd()),verbose=False)
    from research.rl_trading.v6 import published_market_audit as market,saved_label_audit as source
    from research.rl_trading.v6.market_teacher_dataset import read_targets
    output.mkdir();targets={};receipts={}
    for fold,days in manifest['folds'].items():
        frames=[]
        for day in days:
            expected=manifest['sessions'][day];active,_,entry,bank,receipt=market.session(day)
            if active['sha256']!=expected['market_dataset_sha256'] or entry['sha256']!=expected['market_certificate_sha256'] or entry['role']!=expected['role']:raise ValueError('Published target authority changed')
            receipts[day]=dict(certificate_sha256=entry['sha256'],shards=[])
            for shard in receipt['shards']:
                folder=bank/shard['path'];record=source.read_json(folder/'complete.json',shard['sha256'])
                if not set(expected['identities'])&set(record['identities']):continue
                frame=read_targets(folder).filter(pl.col('listing_id').is_in(expected['identities']))
                if frame.is_empty():continue
                frames.append(target_frame(frame,day))
                receipts[day]['shards'].append(dict(path=shard['path'],receipt_sha256=shard['sha256'],labels_sha256=record['files']['labels']['sha256']))
            print('Authenticated forecasts',fold,day,flush=True);write(output/'progress.json',dict(active_day=day,completed=list(receipts)))
        targets[fold]=align(panel[fold],pl.concat(frames))
    torch.save(targets,output/'targets.pt')
    write(output/'complete.json',dict(status='prepared',version='rl-v6-complete-forecast-targets-v1',forecast_contract=FORECAST_VERSION,panel_sha256=proof['panel_sha256'],targets_sha256=file_hash(output/'targets.pt'),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),receipts=receipts,sealed_labels_read=False,observation_contract_changed=False,counts={fold:[np.bincount(v['future'][:,h][v['future'][:,h]>=0],minlength=4).tolist() for h in range(5)] for fold,v in targets.items()}))
    return 0


if __name__=='__main__':raise SystemExit(main())
