"""Authenticated original 1a timing and 1b eligibility targets for diagnostics.

Targets remain a separate sidecar: no target enters observations or source banks.
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


def align_targets(data,frame):
    keys=['episode','clock']
    if frame.select(keys).n_unique()!=frame.height:raise ValueError('Duplicate original target key')
    request=pl.DataFrame(dict(episode=data['episode'],clock=data['clock'],order=np.arange(len(data['action']))))
    joined=request.join(frame,on=keys,how='left',validate='m:1').sort('order')
    if joined['entry_1a'].null_count():raise ValueError('Missing original target row')
    eligible=joined['episode_selected'].fill_null(False).to_numpy()
    original_entry=joined['entry_1a'].to_numpy();flat=data['action']<2
    if not np.array_equal((eligible&original_entry)[flat],data['action'][flat]==0):
        raise ValueError('Copied/suppressed 1b ENTRY differs from original targets')
    if not eligible[~flat].all():raise ValueError('Suppressed episode has held supervision')
    if not np.array_equal(joined['exit_1a'].to_numpy()[~flat],data['action'][~flat]==3):
        raise ValueError('Selected held EXIT differs from original targets')
    return dict(entry_1a=original_entry,episode_selected=eligible,exit_1a=joined['exit_1a'].to_numpy())


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve();root=args.panel.resolve();output=args.output.resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime) or not output.is_relative_to(runtime) or output.exists():raise ValueError('Fresh laptop runtime required')
    proof=json.loads((root/'complete.json').read_text());manifest=json.loads((root/'manifest.json').read_text())
    if proof['status']!='prepared' or manifest['version']!=PRICE_DIVERSITY_VERSION or proof['panel_sha256']!=file_hash(root/'panel.pt') or proof['manifest_sha256']!=file_hash(root/'manifest.json'):raise ValueError('Authenticated diversity panel required')
    if manifest['hash']!=digest({k:v for k,v in manifest.items() if k!='hash'}):raise ValueError('Panel manifest changed')
    panel=torch.load(root/'panel.pt',map_location='cpu',weights_only=False);verify_panel(panel,manifest)
    if panel['train']['clock'].max()>=panel['calibration']['clock'].min():raise ValueError('Calibration chronology changed')
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    from research.rl_trading.v6 import published_market_audit as market,saved_label_audit as source
    from research.rl_trading.v6.market_teacher_dataset import read_targets
    output.mkdir();targets={};receipts={}
    for fold,days in manifest['folds'].items():
        frames=[]
        for day in days:
            expected=manifest['sessions'][day];active,_,entry,bank,receipt=market.session(day)
            if active['sha256']!=expected['market_dataset_sha256'] or entry['sha256']!=expected['market_certificate_sha256'] or entry['role']!=expected['role']:
                raise ValueError('Published target authority differs from panel')
            receipts[day]=dict(certificate_sha256=entry['sha256'],shards=[])
            print('Authenticating hierarchy targets',fold,day,flush=True)
            for shard in receipt['shards']:
                folder=bank/shard['path'];record=source.read_json(folder/'complete.json',shard['sha256'])
                if not set(expected['identities'])&set(record['identities']):continue
                frame=read_targets(folder).filter(pl.col('listing_id').is_in(expected['identities'])&pl.col('time_us').is_between(expected['begin_us'],expected['end_us']-1))
                frames.append(frame.select((pl.lit(day+':')+pl.col('listing_id')+':pair:'+pl.col('pair_id').cast(pl.String)).alias('episode'),
                    pl.col('time_us').alias('clock'),pl.col('action_1a').eq('ENTRY').alias('entry_1a'),
                    pl.col('reference_action_1a').eq('EXIT').fill_null(False).alias('exit_1a'),'episode_selected'))
                receipts[day]['shards'].append(dict(path=shard['path'],receipt_sha256=shard['sha256'],labels_sha256=record['files']['labels']['sha256']))
            write(output/'progress.json',dict(phase='preparing',completed=list(receipts),active_day=day))
        targets[fold]=align_targets(panel[fold],pl.concat(frames))
    torch.save(targets,output/'targets.pt')
    write(output/'complete.json',dict(status='prepared',version='rl-v6-original-hierarchy-targets-v1',panel_sha256=proof['panel_sha256'],
        targets_sha256=file_hash(output/'targets.pt'),source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        source_sha256=file_hash(Path(__file__)),receipts=receipts,sealed_labels_read=False,observation_contract_changed=False,
        counts={fold:dict(rows=len(v['entry_1a']),entry_1a=int(v['entry_1a'].sum()),eligible=int(v['episode_selected'].sum())) for fold,v in targets.items()}))
    print('Prepared authenticated hierarchy sidecar',flush=True)
    return 0


def write(path,value):path.write_text(json.dumps(value,indent=2))


if __name__=='__main__':raise SystemExit(main())
