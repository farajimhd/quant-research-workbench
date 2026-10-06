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
    return actions and future and regressions


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--panel',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv);runtime=Path('D:/TradingML/runtimes').resolve();root=args.panel.resolve();output=args.output.resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime) or not output.is_relative_to(runtime) or output.exists() or not torch.cuda.is_available():raise ValueError('Fresh laptop CUDA runtime required')
    proof=json.loads((root/'complete.json').read_text());manifest=json.loads((root/'manifest.json').read_text())
    if proof['status']!='prepared' or manifest['version']!=PRICE_DIVERSITY_VERSION or proof['panel_sha256']!=file_hash(root/'panel.pt') or proof['manifest_sha256']!=file_hash(root/'manifest.json') or manifest['hash']!=digest({k:v for k,v in manifest.items() if k!='hash'}):raise ValueError('Panel authentication failed')
    # Serialized panel integrity is checked, but only TRAIN is tensorized/evaluated.
    panel=torch.load(root/'panel.pt',map_location='cpu',weights_only=False);verify_panel(panel,manifest)
    data=panel['train'];rng=np.random.default_rng(17)
    rows=np.concatenate([rng.choice(np.flatnonzero(data['action']==c),32,replace=False) for c in range(4)])
    train=tensors(data,manifest['normalization'],'cuda');tiny={**train,'actions_numpy':data['action'][rows],'episodes':[data['episode'][i] for i in rows]}
    for key in ('windows','market','held','action','weight','future','quality','ratio'):tiny[key]=train[key][rows]
    output.mkdir();torch.set_num_threads(4);torch.use_deterministic_algorithms(True)
    plan=dict(panel_sha256=proof['panel_sha256'],source_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),rows=rows.tolist(),architectures=['tcn','gru','transformer'],max_epochs=400,
        criterion='all four current and each of five future action F1 >= .95, ratio and current ENTRY/EXIT quality MAE <= .02',
        scope='local causal-window diagnostic; production market attention and future quality heads NOT certified',generalization_evaluated=False,sealed_labels_read=False,workstation_gpu_used=False)
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
