"""Chronological local TCN experiment admitted only by its complete underfit gate."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import argparse
import json
from pathlib import Path
import subprocess
import numpy as np
import torch
from research.rl_trading.v1.common import file_hash,digest
from research.rl_trading.v6.bias_panel import verify_panel,PRICE_DIVERSITY_VERSION
from research.rl_trading.v6.bias_models import LocalWindowTeacher
from research.rl_trading.v6.bias_metrics import binary_report,fit_probability_calibration,calibrated_probability,calibration_thresholds,action_report
from research.rl_trading.v6.run_bias_campaign import tensors,run_epoch,evaluate,Trial,write,flatten
from research.rl_trading.v6.run_full_head_underfit import passes


def admit_gate(root,panel_sha,target_sha):
    plan=json.loads((root/'manifest.json').read_text());complete=json.loads((root/'complete.json').read_text());report=json.loads((root/'tcn.json').read_text())
    if plan['panel_sha256']!=panel_sha or plan['forecast_targets_sha256']!=target_sha or complete['status']!='completed' or complete['generalization_evaluated'] is not False or not report['passed'] or not passes(report['metrics']):raise ValueError('Exact complete-head TRAIN underfit gate required')
    trial=next(x for x in complete['results'] if x['architecture']=='tcn')
    if not trial['passed'] or file_hash(root/'tcn.pt')!=trial['checkpoint_sha256']:raise ValueError('Underfit checkpoint changed')
    hashes={}
    for name in ('bias_models.py','run_bias_campaign.py','temporal_encoders.py','hierarchical_heads.py'):
        relative='research/rl_trading/v6/'+name;path=Path(relative)
        original=subprocess.check_output(['git','show',plan['source_commit']+':'+relative]).decode('utf-8').replace('\r\n','\n')
        if original!=path.read_text(encoding='utf-8').replace('\r\n','\n'):raise ValueError('Model/objective changed since underfit: '+name)
        hashes[name]=file_hash(path)
    return hashes


def report_all(model,data):
    report,probability=evaluate(model,data,auxiliary=True);flat=data['actions_numpy']<2
    report['WAIT']=binary_report(data['actions_numpy'][flat]==1,1-probability[flat]);report['HOLD']=binary_report(data['actions_numpy'][~flat]==2,1-probability[~flat])
    return report,probability


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('panel','targets','underfit','output'):p.add_argument('--'+name,type=Path,required=True)
    args=p.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve()
    root=args.panel.resolve();targetroot=args.targets.resolve();gate=args.underfit.resolve();output=args.output.resolve()
    if not runtime.is_dir() or any(not x.is_relative_to(runtime) for x in (root,targetroot,gate,output)) or output.exists() or not torch.cuda.is_available():raise ValueError('Fresh laptop CUDA runtime required')
    proof=json.loads((root/'complete.json').read_text());manifest=json.loads((root/'manifest.json').read_text());tp=json.loads((targetroot/'complete.json').read_text())
    if proof['status']!='prepared' or manifest['version']!=PRICE_DIVERSITY_VERSION or proof['panel_sha256']!=file_hash(root/'panel.pt') or proof['manifest_sha256']!=file_hash(root/'manifest.json') or manifest['hash']!=digest({k:v for k,v in manifest.items() if k!='hash'}):raise ValueError('Panel authentication failed')
    if tp['status']!='prepared' or tp['version']!='rl-v6-complete-forecast-targets-v1' or tp['panel_sha256']!=proof['panel_sha256'] or tp['targets_sha256']!=file_hash(targetroot/'targets.pt') or tp['sealed_labels_read'] is not False or tp['observation_contract_changed'] is not False:raise ValueError('Forecast sidecar authentication failed')
    hashes=admit_gate(gate,proof['panel_sha256'],tp['targets_sha256'])
    panel=torch.load(root/'panel.pt',map_location='cpu',weights_only=False);verify_panel(panel,manifest);targets=torch.load(targetroot/'targets.pt',map_location='cpu',weights_only=False)
    if panel['train']['clock'].max()>=panel['calibration']['clock'].min():raise ValueError('Calibration chronology changed')
    def prepared(fold):
        data=tensors(panel[fold],manifest['normalization'],'cuda');target=targets[fold]
        if target['future'].shape!=(len(data['action']),5) or target['future_quality'].shape!=target['future'].shape:raise ValueError('Target shapes changed')
        data.update(future=torch.as_tensor(target['future'],device='cuda'),future_quality=torch.as_tensor(target['future_quality'],device='cuda'));return data
    torch.set_num_threads(4);torch.use_deterministic_algorithms(True);torch.manual_seed(17);output.mkdir()
    plan=dict(version='rl-v6-complete-head-generalization-v1',epochs=10,seed=17,architecture='structured-tcn',objective='branch-balanced current + .1 current quality + .1 ratio + .25 free/forced future actions + .1 free/forced future quality',
        panel_sha256=proof['panel_sha256'],targets_sha256=tp['targets_sha256'],underfit_receipt_sha256=file_hash(gate/'complete.json'),producer_hashes=hashes,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        model_scope='local 19-ticker diagnostic; not full-market production teacher',development_status='previously inspected exploratory',sealed_labels_read=False,workstation_gpu_used=False)
    write(output/'manifest.json',plan)
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path.cwd()),verbose=False)
    import wandb
    logger=wandb.init(project='rl-trading-v6',name=output.name,mode='online',dir=str(output),config=plan);write(output/'wandb.json',dict(id=logger.id,url=logger.url))
    train=prepared('train');cal=prepared('calibration');model=LocalWindowTeacher('tcn',structured=True).cuda();optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=1e-4);trial=Trial('tcn_complete',architecture='tcn',structured=True,auxiliary=True);best=-1
    try:
        for epoch in range(1,11):
            fit=run_epoch(model,train,optimizer,trial,epoch=epoch);training,_=report_all(model,train);calibration,cp=report_all(model,cal)
            score=float(np.mean([calibration[c]['average_precision'] or 0 for c in ('ENTRY','EXIT')]))
            record=dict(epoch=epoch,fit=fit,train=training,calibration=calibration);logger.log(flatten(record));write(output/'progress.json',dict(phase='training',epoch=epoch))
            if score>best:
                best=score;torch.save(model.state_dict(),output/'selected.pt');write(output/'selection.json',dict(epoch=epoch,score=score,calibration=calibration,checkpoint_sha256=file_hash(output/'selected.pt'),selected_before_development=True))
            print('epoch',epoch,'trainENTRY',training['ENTRY']['f1'],'calAP',calibration['ENTRY']['average_precision'],flush=True)
        selection=json.loads((output/'selection.json').read_text())
        if file_hash(output/'selected.pt')!=selection['checkpoint_sha256']:raise ValueError('Selected checkpoint changed')
        model.load_state_dict(torch.load(output/'selected.pt',map_location='cuda',weights_only=True),strict=True)
        calibration,cp=report_all(model,cal)
        if calibration!=selection['calibration']:raise ValueError('Calibration replay changed')
        development,dp=report_all(model,prepared('development'));platt=fit_probability_calibration(panel['calibration']['action'],cp)
        thresholds=calibration_thresholds(panel['calibration']['action'],calibrated_probability(panel['calibration']['action'],cp,platt))
        result=dict(status='completed',calibration_replay_exact=True,development=development,development_calibrated=action_report(panel['development']['action'],calibrated_probability(panel['development']['action'],dp,platt),thresholds),calibration=calibration,sealed_labels_read=False,workstation_gpu_used=False,bias_fixed=False,production_teacher_certified=False)
        np.savez_compressed(output/'probabilities.npz',calibration=cp,development=dp);write(output/'complete.json',result);logger.log(flatten(result,'fixed-development'));logger.summary['completion_status']='completed';write(output/'progress.json',dict(phase='completed_requires_review',bias_fixed=False))
    finally:logger.finish()
    return 0


if __name__=='__main__':raise SystemExit(main())
