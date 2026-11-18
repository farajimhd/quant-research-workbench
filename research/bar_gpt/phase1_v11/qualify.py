"""Separate bounded checkpoint comparison; no optimizer updates or silent resumes."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,time
from pathlib import Path
import numpy as np
import torch
from .config import Config
from .model import Autoencoder
from .data import verify,cache
from .evaluation import evaluate_model
from .probes import compare
from ..phase1_v8.runtime import preflight
from ..v5.shards.phase1_contract import write_json,file_hash
from ..phase2_v2.train import load_checkpoint


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--calibration',type=Path,required=True);p.add_argument('--checkpoints',type=Path,nargs='+',required=True)
    p.add_argument('--data',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset'))
    p.add_argument('--probes',action='store_true');p.add_argument('--audit',action='store_true')
    p.add_argument('--historical-v10',type=Path);p.add_argument('--historical-calibration',type=Path);a=p.parse_args()
    a.device='cuda';a.resume=False;preflight(a);torch.set_num_threads(4);started=time.monotonic()
    def status(**kw):
        if time.monotonic()-started>1200:raise TimeoutError('Twenty-minute checkpoint comparison deadline')
        if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():raise InterruptedError('Human STOP')
        write_json(a.output/'progress.json',dict(elapsed_seconds=time.monotonic()-started,deadline_seconds=1200,**kw))
    manifest=verify(a.data);calibration=json.loads(a.calibration.read_text())
    rows={k:np.load(a.data/(k+'.npy'),allow_pickle=False) for k in ('train','development')}
    data={k:cache(v,'cuda') for k,v in rows.items()};results=[]
    import wandb
    run=wandb.init(entity='mehdifaraji',project='bargpt-phase1-v11-median',name=a.output.name,dir=str(a.output),config=dict(source_revision=a.source_revision,data_hash=manifest['hash'],mode='frozen checkpoint comparison',deadline_seconds=1200))
    write_json(a.output/'wandb.json',dict(id=run.id,url=run.url))
    try:
        for i,path in enumerate(a.checkpoints):
            status(stage='loading checkpoint',checkpoint=str(path))
            saved=torch.load(path,map_location='cpu',weights_only=False);contract=saved['contract'];saved=load_checkpoint(path,contract)
            if contract['calibration_sha256']!=file_hash(a.calibration) or contract['data_hash']!=manifest['hash']:raise ValueError('Checkpoint provenance differs')
            config=Config(**contract['config']);model=Autoencoder(config,calibration['parameters']).cuda();model.load_state_dict(saved['model'])
            identity=path.parent.name+'-'+path.stem;record=dict(checkpoint=str(path),checkpoint_sha256=file_hash(path),training_step=saved['step'],contract=contract)
            record['development']=evaluate_model(model,data['development'],rows['development'],a.output,status,True,calibration['constant_logs'])
            # Fixed representative train sample for gap assessment, shared across candidates.
            ids=np.sort(np.random.default_rng(7711).choice(len(rows['train']),2048,replace=False));take=torch.as_tensor(ids,device='cuda')
            record['training']=evaluate_model(model,{k:v[take] for k,v in data['train'].items()},rows['train'][ids],a.output,status)
            if a.probes:
                status(stage='frozen information probes',checkpoint=str(path));torch.manual_seed(contract['seed']);untrained=Autoencoder(config,calibration['parameters']).cuda()
                record['probes']=compare(model,untrained,data,rows);del untrained
            if a.audit:
                if len(a.checkpoints)!=1:raise ValueError('Audit is reserved for one prespecified finalist')
                verify(a.data,('audit',));audit_rows=np.load(a.data/'audit.npy',allow_pickle=False)
                record['audit']=evaluate_model(model,cache(audit_rows,'cuda'),audit_rows,a.output,status,True,calibration['constant_logs'])
            write_json(a.output/(identity+'.json'),record)
            summary=dict(identity=identity,checkpoint_sha256=record['checkpoint_sha256'],step=saved['step'])
            for mode,m in record['development'].items():
                for key in ('balanced_price_relative_mean','balanced_activity_log_mae','worst_price_band_relative_mean'):summary[mode+'/'+key]=m[key]
                summary[mode+'/reference_price_mean']=m['reference_price_relative']['mean']
                summary[mode+'/reference_price_p90']=m['reference_price_relative']['p90']
            results.append(summary);run.log({k:v for k,v in summary.items() if isinstance(v,(int,float))},step=i)
            artifact=wandb.Artifact(identity,type='evaluation');artifact.add_file(str(a.output/(identity+'.json')));run.log_artifact(artifact)
            del model,saved;torch.cuda.empty_cache()
        if a.historical_v10:
            if not a.historical_calibration:raise ValueError('Pinned historical calibration required')
            from .historical import compare_v10
            record=compare_v10(a.historical_v10,a.historical_calibration,data,rows,status,a.probes)
            write_json(a.output/'historical-v10.json',record)
            artifact=wandb.Artifact('historical-v10-epoch4',type='evaluation');artifact.add_file(str(a.output/'historical-v10.json'));run.log_artifact(artifact)
            summary=dict(identity='historical-v10-epoch4',checkpoint_sha256=record['checkpoint_sha256'],step=record['training_step'])
            for mode in ('masked','all_visible'):
                for key in ('balanced_price_relative_mean','balanced_activity_log_mae','worst_price_band_relative_mean'):summary[mode+'/'+key]=record[mode][key]
            results.append(summary);run.log({k:v for k,v in summary.items() if isinstance(v,(int,float))},step=len(a.checkpoints))
        write_json(a.output/'result.json',dict(state='complete',seconds=time.monotonic()-started,results=results,audit_consulted=a.audit));status(stage='complete',results=results)
    finally:run.finish()


if __name__=='__main__':main()
