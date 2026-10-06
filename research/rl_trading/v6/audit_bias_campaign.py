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

def select_branch_experts(summaries):
    """Independent calibration AP selection; development never enters this API."""
    if not summaries:raise ValueError('No completed calibration trials')
    result={}
    for label in ('ENTRY','EXIT'):
        values=[s['selection']['calibration'][label]['average_precision'] for s in summaries]
        if any(v is None or not np.isfinite(v) or not 0<=v<=1 for v in values):
            raise ValueError('Supported finite calibration AP required')
        result[label]=summaries[int(np.argmax(values))]
    return result

def route_experts(held,entry,exit):
    """Known causal position state, never target actions, chooses the expert."""
    held=np.asarray(held);entry=np.asarray(entry);exit=np.asarray(exit)
    if held.ndim!=2 or held.shape[1]!=11 or entry.shape!=exit.shape or entry.shape!=(len(held),):
        raise ValueError('Branch expert routing axes changed')
    return np.where(held[:,0]>0,exit,entry)

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
    summaries=json.loads((parent/'trials.json').read_text())
    if [s['trial'] for s in summaries]!=pm['trials']:raise ValueError('Completed trial inventory changed')
    experts=select_branch_experts(summaries)
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
    for fold,values in panel.items():
        if not np.array_equal(values['held'][:,0]>0,values['action']>=2):
            raise ValueError('Known position state disagrees with action branch')
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
    def expert_model(summary):
        trial=summary['trial'];path=(parent/trial['name']/'selected.pt').resolve()
        if not path.is_relative_to(parent) or file_hash(path)!=summary['checkpoint_sha256']:raise ValueError('Expert checkpoint changed')
        state=torch.load(path,map_location='cpu',weights_only=False)
        if state['manifest_hash']!=pm['hash'] or state['trial']!=trial or state['normalization']!=norm:raise ValueError('Expert checkpoint binding changed')
        expert=LocalWindowTeacher(trial['architecture'],structured=trial['structured'],shared_forecast=trial['shared_forecast'],stationary=trial['stationary'],normalization=norm).to(device)
        expert.load_state_dict(state['model'],strict=True)
        return expert
    expert_cp={}
    for label,summary in experts.items():
        expert=expert_model(summary);replay,probability=evaluate(expert,calibration,auxiliary=summary['trial']['auxiliary'])
        if replay!=summary['selection']['calibration']:raise ValueError('Expert calibration replay changed')
        expert_cp[label]=probability
        del expert
    combined_cp=route_experts(panel['calibration']['held'],expert_cp['ENTRY'],expert_cp['EXIT'])
    expert_parameters=fit_probability_calibration(panel['calibration']['action'],combined_cp)
    expert_thresholds=calibration_thresholds(panel['calibration']['action'],calibrated_probability(panel['calibration']['action'],combined_cp,expert_parameters))
    days,new=new_public_records(panel['development'],source)
    output.mkdir();binding=dict(parent_manifest_sha256=file_hash(parent/'manifest.json'),checkpoint_sha256=file_hash(checkpoint),
        panel_sha256=proof['panel_sha256'],selected_trial=config,new_public_development_dates=days,selection='parent_calibration_only_before_new_development',
        sealed_labels_read=False,workstation_gpu_used=False,training_started=False,normalization_sha256=digest(norm),
        branch_experts={label:dict(trial=s['trial'],checkpoint_sha256=s['checkpoint_sha256'],calibration=s['selection']) for label,s in experts.items()},
        expert_selection='independent_branch_calibration_AP_only',expert_routing='known_position_state')
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
        expert_dp={};development=tensors(new,norm,device)
        for label,summary in experts.items():
            expert=expert_model(summary);_,expert_dp[label]=evaluate(expert,development)
            del expert
        combined_dp=route_experts(new['held'],expert_dp['ENTRY'],expert_dp['EXIT'])
        expert_adjusted=calibrated_probability(new['action'],combined_dp,expert_parameters)
        result['branch_experts']=dict(default=action_report(new['action'],combined_dp),calibrated=action_report(new['action'],expert_adjusted,expert_thresholds),
            thresholds=expert_thresholds,probability_calibration=expert_parameters,per_day={})
        for day in days:
            evidence=source['sessions'][day];selected_day=(new['clock']>=evidence['begin_us'])&(new['clock']<evidence['end_us'])
            if not selected_day.any():raise ValueError('New public day is empty')
            result['per_day'][day]=dict(default=action_report(new['action'][selected_day],dp[selected_day]),calibrated=action_report(new['action'][selected_day],adjusted[selected_day],thresholds))
            result['branch_experts']['per_day'][day]=dict(default=action_report(new['action'][selected_day],combined_dp[selected_day]),calibrated=action_report(new['action'][selected_day],expert_adjusted[selected_day],expert_thresholds))
        write(output/'metrics.json',result);logger.log(flatten(result,'new_public_development'))
        np.savez_compressed(output/'probabilities.npz',probability=dp,branch_expert_probability=combined_dp,clock=new['clock'],action=new['action'])
        write(output/'complete.json',dict(status='completed',metrics=result,calibration_replay_exact=True,wandb_url=logger.url,**binding))
        logger.summary['completion_status']='completed'
        for name in ('manifest.json','metrics.json','complete.json'):logger.save(str(output/name),base_path=str(output),policy='now')
    finally:logger.finish()
    print('Completed frozen new-public-development audit',output,flush=True)
    return 0

if __name__=='__main__':raise SystemExit(main())
