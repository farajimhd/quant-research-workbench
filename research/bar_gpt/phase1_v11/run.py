"""Finite, deadline-guarded V11 qualification and research runner."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,copy,json,math,time
from pathlib import Path
import numpy as np
import torch
from .config import Config
from .model import Autoencoder
from .data import verify,prepare,cache
from .step import backward,Replay
from .evaluation import evaluate_model
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import save_checkpoint,rng_state
from ..v5.shards.phase1_contract import write_json,file_hash


def main():
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['prepare','experiment'])
    p.add_argument('--data',type=Path,default=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset'))
    p.add_argument('--output',type=Path,required=True);p.add_argument('--calibration',type=Path)
    p.add_argument('--source-revision',default='');p.add_argument('--seed',type=int,default=110731)
    p.add_argument('--wandb-project',default='bargpt-phase1-v11-median')
    p.add_argument('--minutes',type=float,default=18);p.add_argument('--train-minutes',type=float,default=12)
    p.add_argument('--learning-rate',type=float,default=.0003);p.add_argument('--reference-weight',type=float,default=1)
    p.add_argument('--precision',choices=['fp32','bf16'],default='bf16');p.add_argument('--hypothesis',default='Median scale survives attention bottleneck with separately weighted reference recovery.')
    p.add_argument('--readout',choices=['latents','include_reference','split_scale'],default='latents')
    p.add_argument('--width',type=int,default=384);p.add_argument('--layers',type=int,default=8)
    p.add_argument('--backbone',choices=['transformer','mlp','conv1d_v1'],default='transformer');p.add_argument('--embedding',type=int,default=1024)
    p.add_argument('--mlp-density-normalize',action='store_true')
    p.add_argument('--dropout',type=float,default=.05)
    p.add_argument('--input-price-scale',type=float,default=100)
    p.add_argument('--visible-weight',type=float,default=0)
    p.add_argument('--tiny-steps',type=int,default=2000);p.add_argument('--batches',type=int,nargs='+',default=[256,1024,2048,4096])
    p.add_argument('--resume-pretraining',action='store_true',help='Engineering retry before any research optimizer step; preserves original identity and deadline')
    p.add_argument('--probes',action='store_true',help='Reserve final evaluation time for frozen within-window information probes')
    p.add_argument('--max-steps',type=int,default=0,help='Optional matched-update cap, within the wall deadline')
    p.add_argument('--validate-seconds',type=float,default=150)
    a=p.parse_args();torch.set_num_threads(4);torch.manual_seed(a.seed);np.random.seed(a.seed)
    if a.mode=='prepare':print(prepare(a.data,a.output));return
    if not 0<a.train_minutes<a.minutes<=60:raise ValueError('Strict bounded experiment required')
    a.device='cuda';a.resume=a.resume_pretraining;began=time.monotonic();preflight(a)
    if a.resume:
        if (a.output/'checkpoint_last.pt').exists():raise RuntimeError('Training-state resume not supported by this finite entry point')
        clock=json.loads((a.output/'clock.json').read_text());began-=time.time()-clock['started']
        if (a.output/'failure.json').exists():(a.output/'failure.json').rename(a.output/f'engineering-failure-{time.time_ns()}.json')
        if (a.output/'tiny.json').exists():(a.output/'tiny.json').rename(a.output/f'tiny-previous-{time.time_ns()}.json')
    else:write_json(a.output/'clock.json',dict(started=time.time()))
    deadline=began+a.minutes*60
    def status(**kw):
        value=dict(elapsed_seconds=time.monotonic()-began,deadline_seconds=a.minutes*60,**kw)
        write_json(a.output/'progress.json',value);print(json.dumps(value),flush=True)
        if time.monotonic()>deadline:raise TimeoutError('Experiment deadline: incomplete, never qualified')
        if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():raise InterruptedError('Human STOP')
    calibration=json.loads(a.calibration.read_text());manifest=verify(a.data)
    if calibration['data_hash']!=manifest['hash']:raise ValueError('Calibration provenance mismatch')
    if a.width%32 or not 1<=a.layers<=24:raise ValueError('Invalid attention width/depth')
    if not 0<=a.dropout<1:raise ValueError('Invalid dropout')
    if a.input_price_scale<=0:raise ValueError('Positive input scale required')
    if a.visible_weight<0:raise ValueError('Nonnegative observed reconstruction weight required')
    if a.embedding<=32:raise ValueError('Embedding must retain scale and shape capacity')
    config=Config(width=a.width,layers=a.layers,embedding=a.embedding,backbone=a.backbone,mlp_density_normalize=a.mlp_density_normalize,dropout=a.dropout,reference_weight=a.reference_weight,precision=a.precision,readout=a.readout,input_price_scale=a.input_price_scale,visible_weight=a.visible_weight)
    def make():return Autoencoder(config,calibration['parameters']).cuda()
    contract=dict(version=11,source_revision=a.source_revision,config=vars(config),seed=a.seed,data_hash=manifest['hash'],calibration_sha256=file_hash(a.calibration),
        hypothesis=a.hypothesis,learning_rate=a.learning_rate,deadline_seconds=a.minutes*60,training_seconds=a.train_minutes*60,
        max_steps=a.max_steps,
        acceptance='Finite source-valid raw outputs; tiny fit; dev masked+all-visible competitive with visible-median control across timeframe/price bands; predicted-reference scale recovery; no hidden-reference bypass.')
    if a.resume:
        original=json.loads((a.output/'contract.json').read_text())
        if original['config']!=contract['config'] or original['data_hash']!=contract['data_hash']:raise ValueError('Changed science cannot resume')
        write_json(a.output/('engineering-'+a.source_revision[:9]+'.json'),dict(original_source=original['source_revision'],engineering_source=a.source_revision,reason='Pretraining engineering/qualification retry; see archived failure, tiny diagnostics and launcher arguments. Scientific training state has not started.'))
        contract=original
    else:write_json(a.output/'contract.json',contract)
    import wandb
    identity=json.loads((a.output/'wandb.json').read_text())['id'] if a.resume else None
    run=wandb.init(entity='mehdifaraji',project=a.wandb_project,name=a.output.name,dir=str(a.output),config=contract,id=identity,resume='must' if a.resume else None)
    if a.resume:run.summary['failure']=None
    write_json(a.output/'wandb.json',dict(id=run.id,url=run.url))
    def log(metrics,step):
        # Several evaluations share one optimizer step; never write a closed SDK row.
        run.log(dict(metrics,optimizer_step=step),step=max(step,run.step))
    run_exit=0
    try:
        tinyrows=np.load(a.data/'tiny.npy',allow_pickle=False)[:4];tiny=cache(tinyrows,'cuda');m=make().train()
        # Explicit synthetic easy-case test only: repeat each first market bar.
        # Immutable source data and all scientific train/dev batches stay unchanged.
        tiny['values'][...,:18]=tiny['values'][:,:1,:18].clone().expand(-1,64,-1)
        tiny['valid'][...,:18]=tiny['valid'][:,:1,:18].clone().expand(-1,64,-1)
        opt=torch.optim.AdamW(m.parameters(),lr=.001,weight_decay=.01,fused=True)
        # Fixed four easy examples; random visibility remains active.
        replay=Replay(m,tiny['values'],tiny['valid']);initial=None
        for step in range(a.tiny_steps):
            opt.param_groups[0]['lr']=1e-5+.00099*.5*(1+math.cos(math.pi*step/max(a.tiny_steps-1,1)))
            loss,norm,parts=replay(tiny['values'],tiny['valid']);opt.step()
            if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Tiny nonfinite')
            if initial is None:initial=float(loss)
            if step%100==0:status(stage='tiny overfit',step=step,loss=float(loss))
        from ..phase1_v10.model import select
        from .transforms import PRICES
        with torch.no_grad():
            m.eval();x,v,pos,h=select(tiny['values'],tiny['valid'],16);prediction=m(x,v,pos)['logs']
            error=torch.expm1(prediction[...,list(PRICES)]-tiny['values'][...,list(PRICES)]).abs()
            keep=tiny['valid'][...,list(PRICES)]&h[...,None];error=error[keep]
            tiny_price_mean=float(error.mean());tiny_price_p90=float(torch.quantile(error,.9))
        tiny_result=dict(steps=a.tiny_steps,initial=initial,final=float(loss),ratio=float(loss)/initial,price_relative_mean=tiny_price_mean,price_relative_p90=tiny_price_p90,
            scope='Synthetic constant-market bars copied from four certified tiny examples; canonical time metadata retained; scientific dataset unchanged.')
        from .transforms import references,decode
        diagnostics={};original_precision=config.precision
        with torch.no_grad():
            for precision in ('bf16','fp32'):
                config.precision=precision;m.eval();out=m(x,v,pos)
                ref,rv=references(x[...,:18],v[...,:18],m.encoder.reference_mean)
                for kind,logs in (('predicted',out['logs']),('oracle_visible_reference_DIAGNOSTIC',decode(out['code'],ref))):
                    e=torch.expm1(logs[...,list(PRICES)]-tiny['values'][...,list(PRICES)]).abs()[keep]
                    diagnostics[precision+'/'+kind]=dict(mean=float(e.mean()),p90=float(torch.quantile(e,.9)))
            config.precision=original_precision
        tiny_result['diagnostics']=diagnostics
        save_checkpoint(a.output/'checkpoint_tiny.pt',dict(model=m.state_dict(),optimizer=opt.state_dict(),rng=rng_state(),contract=contract,step=a.tiny_steps,cursor=(0,a.tiny_steps)))
        write_json(a.output/'tiny.json',tiny_result)
        if tiny_result['ratio']>.2 or tiny_price_p90>.005:raise AssertionError('Tiny fit failed: '+str(tiny_result))
        del replay,opt,m;tiny=None;torch.cuda.empty_cache()
        status(stage='loading verified frozen subsets')
        rows={k:np.load(a.data/(k+'.npy'),allow_pickle=False) for k in ('train','development')}
        data={k:cache(r,'cuda') for k,r in rows.items()}
        profile=[]
        for bs in a.batches:
            m=make().train();opt=torch.optim.AdamW(m.parameters(),lr=a.learning_rate,weight_decay=.01,fused=True);replay=None
            try:
                x=data['train']['values'][:bs];v=data['train']['valid'][:bs];startup=time.monotonic();replay=Replay(m,x,v)
                startup=time.monotonic()-startup
                for _ in range(3):loss,norm,_=replay(x,v);opt.step()
                torch.cuda.synchronize();start=time.monotonic();torch.cuda.reset_peak_memory_stats()
                for _ in range(15):loss,norm,_=replay(x,v);opt.step()
                torch.cuda.synchronize();seconds=time.monotonic()-start
                result=dict(batch=bs,windows_per_second=bs*15/seconds,startup_seconds=startup,peak_mib=torch.cuda.max_memory_allocated()/2**20,reserved_mib=torch.cuda.memory_reserved()/2**20)
            except torch.cuda.OutOfMemoryError:result=dict(batch=bs,error='out_of_memory')
            finally:del replay,opt,m;torch.cuda.empty_cache()
            profile.append(result);write_json(a.output/'profile.json',profile);status(stage='GPU cached pipeline profile',latest=result)
        best=max((r for r in profile if 'windows_per_second' in r),key=lambda r:r['windows_per_second']);bs=best['batch']
        torch.manual_seed(a.seed);m=make().train();opt=torch.optim.AdamW(m.parameters(),lr=a.learning_rate,weight_decay=.01,fused=True)
        replay=Replay(m,data['train']['values'][:bs],data['train']['valid'][:bs])
        contract['batch']=bs;contract['encoder_parameters']=sum(p.numel() for p in m.encoder.parameters());contract['decoder_parameters']=sum(p.numel() for p in m.parameters())-contract['encoder_parameters']
        write_json(a.output/'contract.json',contract);run.config.update(contract,allow_val_change=True)
        train_start=time.monotonic();train_end=min(train_start+a.train_minutes*60,deadline-240);step=0;history=[];bestscore=float('inf');lastval=train_start
        if a.max_steps:
            planned=min(a.max_steps,int(max(train_end-train_start-20,0)*best['windows_per_second']/bs*.9))
            if planned<=0:raise TimeoutError('Insufficient time for planned training and final evaluation')
            contract['requested_max_steps']=a.max_steps;contract['planned_max_steps']=planned;a.max_steps=planned
            write_json(a.output/'contract.json',contract);run.config.update(contract,allow_val_change=True)
        def checkpoint(name):
            save_checkpoint(a.output/name,dict(model=m.state_dict(),optimizer=opt.state_dict(),rng=rng_state(),contract=contract,step=step,cursor=(0,step)))
        while time.monotonic()<train_end and (not a.max_steps or step<a.max_steps):
            progress=step/max(a.max_steps-1,1) if a.max_steps else (time.monotonic()-train_start)/max(train_end-train_start,1)
            opt.param_groups[0]['lr']=1e-5+.5*(a.learning_rate-1e-5)*(1+math.cos(math.pi*progress))
            ids=torch.randint(len(rows['train']),(bs,),device='cuda')
            loss,norm,parts=replay(data['train']['values'][ids],data['train']['valid'][ids]);opt.step();step+=1
            if step%50==0:
                if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Training nonfinite')
                status(stage='training',step=step,loss=float(loss),gradient_norm=float(norm),windows_per_second=step*bs/(time.monotonic()-train_start),batch=bs)
                log({'train/loss':float(loss),'train/gradient_norm':float(norm),'train/lr':opt.param_groups[0]['lr']},step)
            if time.monotonic()-lastval>a.validate_seconds:
                # Fixed 1024-row learning curve; full frozen dev evaluated at end.
                count=min(1024,len(rows['development']));small={k:v[:count] for k,v in data['development'].items()}
                report=evaluate_model(m,small,rows['development'][:count],a.output,status)
                record=dict(step=step,train_loss=float(loss),seconds=time.monotonic()-train_start,**{mode:{k:v for k,v in metrics.items() if k in ('balanced_price_relative_mean','balanced_activity_log_mae','reference_price_relative')} for mode,metrics in report.items()})
                history.append(record);write_json(a.output/'learning-curves.json',history)
                score=report['masked']['selection_score']
                if score<bestscore:bestscore=score;checkpoint('checkpoint_best.pt')
                for mode in report:log({f'dev/{mode}/price_relative':report[mode]['balanced_price_relative_mean'],f'dev/{mode}/activity_log':report[mode]['balanced_activity_log_mae']},step)
                checkpoint('checkpoint_last.pt');m.train();lastval=time.monotonic()
        checkpoint('checkpoint_last.pt');del replay
        report=evaluate_model(m,data['development'],rows['development'],a.output,status,controls=True,constant_logs=calibration['constant_logs'])
        write_json(a.output/'development.json',report)
        count=min(2048,len(rows['train']));train_report=evaluate_model(m,{k:v[:count] for k,v in data['train'].items()},rows['train'][:count],a.output,status)
        write_json(a.output/'training-evaluation.json',train_report)
        if a.probes:
            from .probes import compare
            status(stage='frozen information probes');torch.manual_seed(a.seed);untrained=make()
            probe_report=compare(m,untrained,data,rows);write_json(a.output/'probes.json',probe_report);del untrained
            for variant,values in probe_report.items():
                if variant in ('trained','untrained_same_architecture','raw_summary_control'):
                    log({f'probes/{variant}/{label}/r2':metrics['r2'] for label,metrics in values.items()},step)
        for mode,metrics in report.items():
            logs={f'final/{mode}/{key}':metrics[key] for key in ('balanced_price_relative_mean','balanced_activity_log_mae','worst_price_band_relative_mean')}
            for frame,record in metrics['frames'].items():
                for channel,values in record['channels'].items():
                    if values['mae'] is not None:logs[f'raw_mae/{mode}/{frame}/{channel}']=values['mae']
            log(logs,step)
        # Full final checkpoint and matching metrics are always paired.
        result=dict(state='complete',step=step,seconds=time.monotonic()-began,training_wall_seconds=time.monotonic()-train_start,
            tiny=tiny_result,best_profile=best,masked_price=report['masked']['balanced_price_relative_mean'],all_visible_price=report['all_visible']['balanced_price_relative_mean'],
            masked_activity=report['masked']['balanced_activity_log_mae'],masked_reference_price=report['masked']['reference_price_relative'],
            checkpoint_sha256=file_hash(a.output/'checkpoint_last.pt'),audit_consulted=False)
        write_json(a.output/'result.json',result);run.summary.update(result);status(stage='complete',result=result)
    except BaseException as exc:
        run_exit=1
        if isinstance(exc,(InterruptedError,TimeoutError)) and 'train_start' in locals():checkpoint('checkpoint_last.pt')
        write_json(a.output/'failure.json',dict(error=repr(exc),seconds=time.monotonic()-began));run.summary['failure']=repr(exc);raise
    finally:run.finish(exit_code=run_exit)


if __name__=='__main__':main()
