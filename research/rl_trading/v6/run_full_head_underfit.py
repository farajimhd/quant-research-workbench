"""TRAIN-only local teacher head learnability; no generalization admission by proxy."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG',':4096:8')
import argparse
import gc
import json
from pathlib import Path
import subprocess
import numpy as np
import torch
from research.rl_trading.v1.common import file_hash,digest
from research.rl_trading.v6.bias_panel import verify_panel,PRICE_DIVERSITY_VERSION
from research.rl_trading.v6.bias_models import LocalWindowTeacher
from research.rl_trading.v6.bias_metrics import binary_report
from research.rl_trading.v6.run_bias_campaign import tensors,run_epoch,evaluate,Trial,write,flatten


def passes(report):
    actions=all(report[c]['f1']>=.95 for c in ('ENTRY','WAIT','HOLD','EXIT'))
    future=all(m['count']>0 and m['f1']>=.95 for horizon in report['future'] for m in horizon.values())
    regressions=report['ratio_targets']>0 and report['ratio_mae'] is not None and report['ratio_mae']<=.02
    regressions=regressions and all(report['quality_mae'].get(c) is not None and report['quality_mae'][c]<=.02 for c in ('ENTRY','EXIT'))
    future_quality=report.get('future_quality_mae',[])
    regressions=regressions and len(future_quality)==5 and all(horizon.get(c) is not None and horizon[c]<=.02 for horizon in future_quality for c in ('ENTRY','EXIT'))
    return actions and future and regressions


def underfit_rows(actions,future,*,per_class=32):
    """TRAIN-only stratification covers every evaluated forecast class."""
    rng=np.random.default_rng(17);required=set()
    for horizon in range(5):
        for label in range(4):
            candidates=np.flatnonzero(future[:,horizon]==label)
            if len(candidates)<2:raise ValueError('Source lacks two examples of a forecast class')
            required.update(rng.choice(candidates,2,replace=False).tolist())
    rows=[]
    for label in range(4):
        selected=sorted(i for i in required if actions[i]==label)
        if len(selected)>per_class:raise ValueError('Forecast coverage exceeds bounded current-class budget')
        candidates=np.setdiff1d(np.flatnonzero(actions==label),selected)
        if len(candidates)<per_class-len(selected):raise ValueError('Insufficient bounded current-action examples')
        rows.extend(selected+rng.choice(candidates,per_class-len(selected),replace=False).tolist())
    return np.asarray(rows,np.int64)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--targets',type=Path,required=True)
    parser.add_argument('--rows-per-class',type=int,choices=(32,128,256,512),default=32)
    parser.add_argument('--architectures',nargs='+',choices=('tcn','gru','transformer'),default=['tcn','gru','transformer'])
    args=parser.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve();root=args.panel.resolve();output=args.output.resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime) or not output.is_relative_to(runtime) or output.exists() or not torch.cuda.is_available():raise ValueError('Fresh laptop CUDA runtime required')
    proof=json.loads((root/'complete.json').read_text());manifest=json.loads((root/'manifest.json').read_text())
    if proof['status']!='prepared' or manifest['version']!=PRICE_DIVERSITY_VERSION or proof['panel_sha256']!=file_hash(root/'panel.pt') or proof['manifest_sha256']!=file_hash(root/'manifest.json') or manifest['hash']!=digest({k:v for k,v in manifest.items() if k!='hash'}):raise ValueError('Panel authentication failed')
    # Serialized panel integrity is checked, but only TRAIN is tensorized/evaluated.
    panel=torch.load(root/'panel.pt',map_location='cpu',weights_only=False);verify_panel(panel,manifest)
    data=panel['train']
    targetroot=args.targets.resolve()
    if not targetroot.is_relative_to(runtime):raise ValueError('Forecast targets escaped runtime')
    targetproof=json.loads((targetroot/'complete.json').read_text())
    if targetproof['status']!='prepared' or targetproof['version']!='rl-v6-complete-forecast-targets-v1' or targetproof['panel_sha256']!=proof['panel_sha256'] or targetproof['targets_sha256']!=file_hash(targetroot/'targets.pt') or targetproof['sealed_labels_read'] is not False or targetproof['observation_contract_changed'] is not False:raise ValueError('Forecast sidecar authentication failed')
    targets=torch.load(targetroot/'targets.pt',map_location='cpu',weights_only=False)['train']
    if targets['future'].shape!=(len(data['action']),5) or targets['future_quality'].shape!=targets['future'].shape:raise ValueError('Forecast target shape changed')
    rows=underfit_rows(data['action'],targets['future'],per_class=args.rows_per_class)
    train=tensors(data,manifest['normalization'],'cuda');tiny={**train,'actions_numpy':data['action'][rows],'episodes':[data['episode'][i] for i in rows]}
    for key in ('windows','market','held','action','weight','future','quality','ratio'):tiny[key]=train[key][rows]
    tiny['future']=torch.as_tensor(targets['future'][rows],device='cuda');tiny['future_quality']=torch.as_tensor(targets['future_quality'][rows],device='cuda')
    if any(not np.isin(np.arange(4),targets['future'][rows,h]).all() for h in range(5)):raise ValueError('Underfit sample lacks a forecast class; no experiment admitted')
    output.mkdir();torch.set_num_threads(4);torch.use_deterministic_algorithms(True)
    plan=dict(panel_sha256=proof['panel_sha256'],source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),rows=rows.tolist(),rows_per_class=args.rows_per_class,architectures=list(dict.fromkeys(args.architectures)),max_epochs=400,
        criterion='all four current and each of five future action F1 >= .95, ratio and current ENTRY/EXIT quality MAE <= .02',
        scope='local causal-window diagnostic; production market attention NOT certified',forecast_targets_sha256=targetproof['targets_sha256'],generalization_evaluated=False,sealed_labels_read=False,workstation_gpu_used=False)
    write(output/'manifest.json',plan)
    from research.mlops.env import discover_env_files,load_env_files
    load_env_files(discover_env_files(Path.cwd()),verbose=False)
    import wandb
    logger=wandb.init(project='rl-trading-v6',name=output.name,mode='online',dir=str(output),config=plan)
    write(output/'wandb.json',dict(url=logger.url,id=logger.id));results=[]
    try:
        for architecture in plan['architectures']:
            torch.manual_seed(17);model=LocalWindowTeacher(architecture,structured=True).cuda()
            optimizer=torch.optim.AdamW(model.parameters(),lr=3e-4,weight_decay=1e-4)
            trial=Trial(architecture,architecture=architecture,structured=True,auxiliary=True);passed=False
            for epoch in range(1,401):
                fit=run_epoch(model,tiny,optimizer,trial,epoch=epoch,batch_size=128)
                if epoch%10:continue
                report,p=evaluate(model,tiny,batch_size=128,auxiliary=True);flat=tiny['actions_numpy']<2
                report['WAIT']=binary_report(tiny['actions_numpy'][flat]==1,1-p[flat]);report['HOLD']=binary_report(tiny['actions_numpy'][~flat]==2,1-p[~flat])
                passed=passes(report);record=dict(epoch=epoch,passed=passed,metrics=report,fit=fit)
                logger.log(flatten(record,architecture));write(output/(architecture+'.json'),record)
                write(output/'progress.json',dict(architecture=architecture,epoch=epoch,passed=passed,completed=len(results)))
                print(architecture,epoch,passed,'ENTRY',report['ENTRY']['f1'],'ratioMAE',report['ratio_mae'],flush=True)
                if passed:break
            torch.save(model.state_dict(),output/(architecture+'.pt'));results.append(dict(architecture=architecture,passed=passed,epoch=epoch,checkpoint_sha256=file_hash(output/(architecture+'.pt'))))
            del model,optimizer;gc.collect();torch.cuda.empty_cache()
        write(output/'complete.json',dict(status='completed',results=results,generalization_evaluated=False,production_teacher_certified=False,wandb_url=logger.url));logger.summary['completion_status']='completed'
    finally:logger.finish()
    return 0


if __name__=='__main__':raise SystemExit(main())
