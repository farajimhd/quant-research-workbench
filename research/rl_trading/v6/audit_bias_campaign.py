"""Evaluate a frozen selected teacher on newly prepared public development.

No optimizer, model selection or sealed targets. The original TRAIN/calibration
export hashes and normalization must match before new development is admitted.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse,json
from pathlib import Path
import numpy as np
import torch
from research.rl_trading.v1.common import digest,file_hash
from research.rl_trading.v6.bias_panel import FOLDS,EXTENDED_FOLDS,verify_panel,normalization
from research.rl_trading.v6.bias_models import LocalWindowTeacher
from research.rl_trading.v6.bias_metrics import action_report,fit_probability_calibration,calibrated_probability,calibration_thresholds
from research.rl_trading.v6.run_bias_campaign import tensors,reserve_calibration,evaluate,write,flatten

def checked_panel(root):
    proof=json.loads((root/'complete.json').read_text());manifest=json.loads((root/'manifest.json').read_text())
    if (proof['status']!='prepared' or proof['panel_sha256']!=file_hash(root/'panel.pt')
        or proof['manifest_sha256']!=file_hash(root/'manifest.json') or manifest['hash']!=digest({k:v for k,v in manifest.items() if k!='hash'})):
        raise ValueError('Panel completion/bytes changed')
    return proof,manifest

def new_public_records(original,source):
    """Admit only the four frozen public dates and retain shared feature indices."""
    days=[d for d in EXTENDED_FOLDS['development'] if d not in FOLDS['development']]
    mask=np.zeros(len(original['action']),bool)
    for day in days:
        evidence=source['sessions'][day]
        if evidence['role']!='development':raise ValueError('New audit role is not public development')
        selected=(original['clock']>=evidence['begin_us'])&(original['clock']<evidence['end_us'])
        if not selected.any():raise ValueError('New public day is empty')
        mask|=selected
    new={k:(v if k=='features' else [x for x,good in zip(v,mask) if good] if k=='episode' else v[mask]) for k,v in original.items()}
    return days,new

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent',type=Path,required=True);p.add_argument('--parent-panel',type=Path,required=True)
    p.add_argument('--panel',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve()
    parent,oldroot,root,output=(x.resolve() for x in (args.parent,args.parent_panel,args.panel,args.output))
    if not runtime.is_dir() or not all(x.is_relative_to(runtime) for x in (parent,oldroot,root,output)) or output.exists() or not torch.cuda.is_available():
        raise ValueError('Completed laptop campaign, authenticated panels and fresh CUDA audit required')
    completed=json.loads((parent/'complete.json').read_text());pm=json.loads((parent/'manifest.json').read_text())
    selected=json.loads((parent/'selected-trial.json').read_text())
    if completed['status']!='completed' or selected['selected_before_development'] is not True or pm['hash']!=digest({k:v for k,v in pm.items() if k!='hash'}):
        raise ValueError('Parent selection was not frozen before development')
    if completed['selected']!={k:v for k,v in selected.items() if k!='selected_before_development'} or selected['trial'] not in pm['trials']:
        raise ValueError('Parent completion/selected trial disagree')
    oldproof,old=checked_panel(oldroot);proof,source=checked_panel(root)
    if pm['panel_sha256']!=oldproof['panel_sha256'] or pm['panel_manifest_sha256']!=oldproof['manifest_sha256']:
        raise ValueError('Parent campaign used a different panel')
    if old['folds']!={k:list(v) for k,v in FOLDS.items()} or source['folds']!={k:list(v) for k,v in EXTENDED_FOLDS.items()}:
        raise ValueError('Public fold admission changed')
    for key in ('version','tickers','seconds','input_history_contract'):
        if old.get(key)!=source.get(key):raise ValueError('Input contract changed')
    for day in FOLDS['train']+FOLDS['calibration']:
        exported=json.loads((root/(day+'-receipt.json')).read_text())
        if old['derived_from']['exports'][day]['sha256']!=exported['sha256'] or file_hash(root/(day+'.pt'))!=exported['sha256']:
            raise ValueError('Frozen training/calibration exports differ')
    for name in ('bias_models.py','bias_metrics.py','temporal_encoders.py','model.py','hierarchical_heads.py','execution_features.py'):
        if file_hash(Path(__file__).parent/name)!=pm['source_files_sha256'][name]:raise ValueError('Selected model/metric source changed')
    checkpoint=(parent/selected['trial']['name']/'selected.pt').resolve()
    if not checkpoint.is_relative_to(parent):raise ValueError('Checkpoint escaped parent runtime')
    if file_hash(checkpoint)!=selected['checkpoint_sha256']:raise ValueError('Selected checkpoint changed')
    saved=torch.load(checkpoint,map_location='cpu',weights_only=False)
    if saved['manifest_hash']!=pm['hash'] or saved['trial']!=selected['trial']:raise ValueError('Selected checkpoint identity changed')
    panel=torch.load(root/'panel.pt',map_location='cpu',weights_only=False);verify_panel(panel,source)
    panel=reserve_calibration(panel,source);norm=normalization(panel['train'])
    if digest(norm)!=pm['normalization_sha256'] or norm!=saved['normalization']:raise ValueError('Frozen normalization changed')
    torch.set_num_threads(4);device=torch.device('cuda');config=selected['trial']
    model=LocalWindowTeacher(config['architecture'],structured=config['structured'],shared_forecast=config['shared_forecast'],stationary=config['stationary'],normalization=norm).to(device)
    model.load_state_dict(saved['model'],strict=True)
    calibration=tensors(panel['calibration'],norm,device)
    metrics,cp=evaluate(model,calibration,auxiliary=config['auxiliary'])
    if metrics!=selected['selection']['calibration']:raise ValueError('Frozen calibration replay changed')
    parameters=fit_probability_calibration(panel['calibration']['action'],cp)
    thresholds=calibration_thresholds(panel['calibration']['action'],calibrated_probability(panel['calibration']['action'],cp,parameters))
    days,new=new_public_records(panel['development'],source)
    output.mkdir();binding=dict(parent_manifest_sha256=file_hash(parent/'manifest.json'),checkpoint_sha256=file_hash(checkpoint),
        panel_sha256=proof['panel_sha256'],selected_trial=config,new_public_development_dates=days,selection='parent_calibration_only_before_new_development',
        sealed_labels_read=False,workstation_gpu_used=False,training_started=False,normalization_sha256=digest(norm))
    write(output/'manifest.json',binding)
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    import wandb
    logger=wandb.init(project='rl-trading-v6',name=output.name,mode='online',dir=str(output),config=binding)
    if logger is None or logger.settings.mode!='online':raise ValueError('Online W&B required')
    try:
        default,dp=evaluate(model,tensors(new,norm,device),auxiliary=config['auxiliary'])
        adjusted=calibrated_probability(new['action'],dp,parameters)
        result=dict(default=default,calibrated=action_report(new['action'],adjusted,thresholds),thresholds=thresholds,probability_calibration=parameters,per_day={})
        for day in days:
            evidence=source['sessions'][day];selected_day=(new['clock']>=evidence['begin_us'])&(new['clock']<evidence['end_us'])
            if not selected_day.any():raise ValueError('New public day is empty')
            result['per_day'][day]=dict(default=action_report(new['action'][selected_day],dp[selected_day]),calibrated=action_report(new['action'][selected_day],adjusted[selected_day],thresholds))
        write(output/'metrics.json',result);logger.log(flatten(result,'new_public_development'))
        np.savez_compressed(output/'probabilities.npz',probability=dp,clock=new['clock'],action=new['action'])
        write(output/'complete.json',dict(status='completed',metrics=result,calibration_replay_exact=True,wandb_url=logger.url,**binding))
        logger.summary['completion_status']='completed'
        for name in ('manifest.json','metrics.json','complete.json'):logger.save(str(output/name),base_path=str(output),policy='now')
    finally:logger.finish()
    print('Completed frozen new-public-development audit',output,flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
