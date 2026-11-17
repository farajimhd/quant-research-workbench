"""Deadline-bounded learned-only MAE experiments with fixed data and native metrics."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',CUBLAS_WORKSPACE_CONFIG=':4096:8')
import argparse,json,random,time
from dataclasses import asdict
from pathlib import Path
import numpy as np
import torch
from .model import Config,Autoencoder,objective,inverse,PRICES
from .data import load_data,cache,calibrate,batch
from .evaluate import measure,predict,evaluate,probes,summaries
from .runtime import preflight
from .checkpoint import save_checkpoint
from ..v5.shards.phase1_contract import write_json,file_hash


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--architecture',choices=('mlp','attention','fused'),default='mlp');p.add_argument('--width',type=int,default=1024)
    p.add_argument('--layers',type=int,default=3);p.add_argument('--embedding',type=int,default=512)
    p.add_argument('--output-coordinates',choices=('direct','geometry','joint'),default='direct')
    p.add_argument('--visible',type=int,default=16);p.add_argument('--full-probability',type=float,default=.25)
    p.add_argument('--precision',choices=('fp32','bf16'),default='fp32');p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--price-weight',type=float,default=1.,help='Explicit family weight; price residual remains in 100*log-relative units')
    p.add_argument('--full-price-mse',type=float,default=0.,help='Additional squared 100*log-price residual on all-visible batches only')
    p.add_argument('--steps',type=int,default=5000);p.add_argument('--minutes',type=float,default=25)
    p.add_argument('--validate-every',type=int,default=500);p.add_argument('--seed',type=int,default=17)
    p.add_argument('--device',choices=('cuda','cpu'),default='cuda');p.add_argument('--resume',action='store_true')
    p.add_argument('--wandb-mode',choices=('online','disabled'),default='online');p.add_argument('--hypothesis',required=True);p.add_argument('--parent',default='none')
    a=p.parse_args(argv)
    if not 0<a.minutes<=60 or not 1<=a.visible<64 or not 0<=a.full_probability<=1 or not 0<a.price_weight<=10 or not 0<=a.full_price_mse<=.1:raise ValueError('Invalid bounded configuration')
    started=time.time();deadline=started+a.minutes*60;preflight(a)
    torch.set_num_threads(4);torch.manual_seed(a.seed);np.random.seed(a.seed);random.seed(a.seed)
    torch.set_float32_matmul_precision('highest');torch.use_deterministic_algorithms(True)
    step=0;progress={};wb=None
    def status(stage=None,**kw):
        if stage is not None:progress['stage']=stage
        progress.update(experiment=a.output.name,step=step,planned_steps=a.steps,updated=time.time(),elapsed=time.time()-started,limit_seconds=a.minutes*60,**kw)
        write_json(a.output/'progress.json',progress)
    status('verifying frozen data')
    rows,manifest=load_data(a.data)
    cpu={name:cache(value) for name,value in rows.items()}
    params,calibration=calibrate(cpu['train'],a.output_coordinates)
    config=Config(a.architecture,a.width,a.layers,a.embedding,a.visible,a.output_coordinates,a.precision)
    model=Autoencoder(config,**params).to(a.device)
    identity=dict(version='v8-fully-learned-mae-1',config=asdict(config),source_revision=a.source_revision,dataset_hash=manifest['hash'],
        steps=a.steps,minutes=a.minutes,seed=a.seed,learning_rate=a.lr,price_weight=a.price_weight,full_price_mse=a.full_price_mse,full_probability=a.full_probability,parent=a.parent,hypothesis=a.hypothesis,
        input=('Visible-only centered asinh market residuals + fixed-normalized time + validity; visible means/coverage pass through learned context and export matrices; no copied values'
               if a.architecture=='fused' else '18 fixed-global log/log1p normalized market channels + 9 fixed-normalized time channels + validity; no window statistics'),
        latent='Every coordinate produced by learned export projection; zero copied reference/validity slots',
        decoder='One affine projection to 64x54; predicts all market/time values and validity; optional fixed structure algebra; no external inputs',
        loss='0.5*(price_weight*mean Huber(100*log-price residual) + mean Huber(log1p-activity residual)); .05*time and .05*validity; full_price_mse*mean(square(100*log-price residual)) on all-visible batches only',
        calibration=calibration,parameters={k:v.tolist() for k,v in params.items()},
        sampling='Equal timeframe/last-close-price-band mass; uniform rows within cells; calibration and metrics unweighted',
        parameter_count=sum(v.numel() for v in model.parameters()),decoder_parameters=sum(v.numel() for v in model.decoder.parameters()),
        selection='50*balanced price-relative mean + activity log MAE + .05*worst price-band mean; separately inspect hidden-only, metadata and probes',
        acceptance='Complete finite evaluation; all-learned bottleneck; tiny fit; compare all groups and frozen probes with controls; do not accept aggregate loss alone')
    if a.resume:
        if json.loads((a.output/'manifest.json').read_text())!=identity:raise ValueError('Changed scientific experiment cannot resume')
    else:write_json(a.output/'manifest.json',identity)
    data={name:{k:v.to(a.device) for k,v in value.items()} for name,value in cpu.items()};del cpu
    groups=np.c_[rows['train']['timeframe_us'],np.digitize(rows['train']['values'][:,-1,3],[.1,1,5,20,100])]
    cells,ids,counts=np.unique(groups,axis=0,return_inverse=True,return_counts=True)
    sampling=torch.tensor(1/counts[ids],dtype=torch.float32,device=a.device)
    if not a.resume:write_json(a.output/'sampling.json',dict(cells=cells.tolist(),counts=counts.tolist(),per_row_probability=(1/counts/len(counts)).tolist()))
    write_json(a.output/'startup.json',dict(seconds=time.time()-started,windows={k:len(v) for k,v in rows.items()},loader_wait_seconds=0,
        cache='Verified fixed subset cached on GPU; workers/prefetch intentionally unnecessary for iteration'))
    opt=torch.optim.AdamW(model.parameters(),lr=a.lr,weight_decay=.01,fused=a.device=='cuda')
    scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(opt,a.steps,eta_min=a.lr*.05)
    gen=torch.Generator(device=a.device).manual_seed(a.seed);best=float('inf')
    if a.resume:
        state=torch.load(a.output/'last.pt',map_location=a.device,weights_only=False)
        if state['identity']!=identity:raise ValueError('Checkpoint identity changed')
        model.load_state_dict(state['model']);opt.load_state_dict(state['optimizer']);scheduler.load_state_dict(state['scheduler'])
        gen.set_state(state['generator'].cpu());torch.set_rng_state(state['torch_rng'].cpu())
        if a.device=='cuda':torch.cuda.set_rng_state_all([s.cpu() for s in state['cuda_rng']])
        np.random.set_state(state['numpy_rng']);random.setstate(state['python_rng'])
        step=state['step'];started=state['started'];deadline=started+a.minutes*60;best=state['best']
    def checkpoint(name='last.pt'):save_checkpoint(a.output/name,model,opt,scheduler,gen,step,started,identity,best)
    def loss_for(m,b):return objective(m(b[0],b[1],b[2])[1],*b[3:],m.input_mean,m.input_scale,a.price_weight,a.full_price_mse)
    try:
        if not a.resume:
            raw=torch.from_numpy(rows['train']['values'].copy()).double();valid=torch.from_numpy(rows['train']['valid'].astype(bool))
            recovered=inverse(data['train']['values'][...,:18].cpu())
            torch.testing.assert_close(recovered[valid],raw[valid],rtol=1e-4,atol=1e-5)
            relative=((recovered-raw).abs()/raw.abs().clamp_min(1e-30))[valid&(raw>0)]
            write_json(a.output/'roundtrip.json',dict(max_positive_relative=float(relative.max()),zero_count=int((valid&(raw==0)).sum()),
                valid_count=int(valid.sum()),no_target_references=True,precision='FP64 log before FP32 model; FP64 native inverse'))
            del raw,valid,recovered,relative
            status('tiny overfit')
            tiny=Autoencoder(config,**params).to(a.device);to=torch.optim.AdamW(tiny.parameters(),lr=.0008,fused=a.device=='cuda')
            tiny_gen=torch.Generator(device=a.device).manual_seed(912)
            b=batch(data['tiny'],torch.arange(4,device=a.device),config.visible,tiny_gen,True)
            first=None
            for k in range(800):
                to.param_groups[0]['lr']=.00001+.00079*.5*(1+np.cos(np.pi*k/799))
                to.zero_grad(set_to_none=True);loss,_=loss_for(tiny,b)
                if first is None:first=float(loss)
                loss.backward();torch.nn.utils.clip_grad_norm_(tiny.parameters(),1.,error_if_nonfinite=True);to.step()
                if k%100==0:status('tiny overfit',tiny_step=k,tiny_loss=float(loss))
            with torch.no_grad():
                logpred=tiny(*b[:3])[1][0];mask=b[4][...,:18]
                price_error=(logpred[...,list(PRICES)]-b[3][...,:18][...,list(PRICES)]).abs()[mask[...,list(PRICES)]].mean()
            tiny_result=dict(initial=first,final=float(loss),ratio=float(loss)/first,price_log_mae=float(price_error),steps=800)
            write_json(a.output/'tiny-fit.json',tiny_result)
            if tiny_result['ratio']>.05 or tiny_result['price_log_mae']>.01:raise RuntimeError('Tiny fit failed; diagnose before larger training')
            del tiny,to
            profile=[]
            profile_gen=torch.Generator(device=a.device).manual_seed(817)
            for bs in (128,512,1024):
                status('profiling',profile_batch=bs)
                pm=Autoencoder(config,**params).to(a.device);po=torch.optim.AdamW(pm.parameters(),lr=a.lr,fused=a.device=='cuda')
                for k in range(15):
                    if k==3:
                        if a.device=='cuda':torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats()
                        begin=time.perf_counter()
                    ix=torch.multinomial(sampling,bs,replacement=True,generator=profile_gen)
                    full=bool(torch.rand((),device=a.device,generator=profile_gen)<a.full_probability)
                    b=batch(data['train'],ix,config.visible,profile_gen,full)
                    po.zero_grad(set_to_none=True);loss,_=loss_for(pm,b)
                    if not torch.isfinite(loss):raise FloatingPointError('Nonfinite profile loss')
                    loss.backward();torch.nn.utils.clip_grad_norm_(pm.parameters(),1.,error_if_nonfinite=True);po.step()
                if a.device=='cuda':torch.cuda.synchronize()
                elapsed=time.perf_counter()-begin
                profile.append(dict(batch=bs,seconds=elapsed,windows_per_second=bs*12/elapsed,peak_bytes=torch.cuda.max_memory_allocated() if a.device=='cuda' else 0))
                del pm,po
            bs=max(profile,key=lambda x:x['windows_per_second'])['batch']
            write_json(a.output/'profile.json',dict(configurations=profile,selected_batch=bs,precision=a.precision,loader_workers=0,loader_wait_seconds=0,
                full_probability=a.full_probability,includes_gradient_clip_and_finite_check=True,profile_seed=817))
            status('untrained probes and controls')
            _,tz,_,_=predict(model,data['train'],bs);_,dz,hidden,_=predict(model,data['development'],bs,False)
            _,fullz,_,_=predict(model,data['development'],bs)
            write_json(a.output/'untrained-probes.json',probes(tz,fullz,rows['train'],rows['development']))
            sy,_=summaries(rows['train']);dy,_=summaries(rows['development'])
            write_json(a.output/'raw-summary-probes.json',probes(sy,dy,rows['train'],rows['development']))
            constant=inverse(params['input_mean'][:18].expand(1,64,18)).numpy()
            write_json(a.output/'constant-development.json',evaluate(np.broadcast_to(constant,(len(rows['development']),64,18)),rows['development']))
            visible=rows['development']['valid'].astype(bool)&~hidden
            control=np.empty_like(rows['development']['values'],dtype=np.float64)
            for j in range(18):
                med=np.ma.median(np.ma.array(rows['development']['values'][...,j],mask=~visible[...,j]),axis=1).filled(float(constant[0,0,j]))
                control[...,j]=med[:,None]
            write_json(a.output/'visible-median-development.json',evaluate(control,rows['development'],hidden))
            del tz,dz,fullz,control
        else:bs=json.loads((a.output/'profile.json').read_text())['selected_batch']
        if a.wandb_mode=='online':
            from research.mlops.env import discover_env_files,load_env_files
            load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
            import wandb
            path=a.output/'wandb-identity.json';wid=json.loads(path.read_text())['id'] if path.exists() else wandb.util.generate_id()
            write_json(path,dict(id=wid,entity='mehdifaraji',project='bargpt-v8-learned-mae-20261117'))
            wb=wandb.init(entity='mehdifaraji',project='bargpt-v8-learned-mae-20261117',id=wid,name=a.output.name,
                resume='must' if a.resume else 'never',config=identity,dir=str(a.output))
        validation_seconds=0.;training_started=time.time();initial_step=step;last_loss=None
        while step<a.steps and time.time()<deadline-max(180,validation_seconds*6):
            if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():checkpoint();status('stopped');return
            model.train();ix=torch.multinomial(sampling,bs,replacement=True,generator=gen)
            full=bool(torch.rand((),device=a.device,generator=gen)<a.full_probability)
            b=batch(data['train'],ix,config.visible,gen,full)
            opt.zero_grad(set_to_none=True);loss,components=loss_for(model,b)
            if not torch.isfinite(loss):raise FloatingPointError('Nonfinite loss')
            loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
            opt.step();scheduler.step();step+=1;last_loss=float(loss)
            if step%25==0:
                status('training',loss=last_loss,windows_per_second=(step-initial_step)*bs/max(time.time()-training_started,.001),
                    loader_wait_seconds=0,gpu_memory_bytes=torch.cuda.max_memory_allocated() if a.device=='cuda' else 0)
            if step%a.validate_every==0 or step==a.steps:
                status('development validation',loss=last_loss);begin=time.time()
                metrics,_=measure(model,data['development'],rows['development'],bs,True,status)
                validation_seconds=time.time()-begin;score=metrics['selection_score']
                record=dict(step=step,elapsed=time.time()-started,train_loss=last_loss,selection_score=score,
                    balanced_price_relative_mean=metrics['balanced_price_relative_mean'],activity_log_mae=metrics['balanced_activity_log_mae'],
                    worst_price_band_relative_mean=metrics['worst_price_band_relative_mean'],validation_seconds=validation_seconds)
                with (a.output/'learning.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
                write_json(a.output/'development-latest.json',metrics)
                if wb:wb.log({'train/loss':last_loss,**{'development/'+k:v for k,v in record.items() if k!='step'}},step=step)
                if score<best:best=score;checkpoint('best.pt')
                checkpoint();status('training',latest_evaluation=metrics['balanced_price_relative_mean'])
        checkpoint();status('final evaluation',loss=last_loss)
        if (a.output/'best.pt').exists():model.load_state_dict(torch.load(a.output/'best.pt',map_location=a.device,weights_only=False)['model'])
        results={};zs={};begin=time.time()
        for partition in ('train','development'):
            for full in (True,False):
                if time.time()>deadline:raise TimeoutError('Deadline before complete evaluation')
                status('final '+partition+(' all-visible' if full else ' hidden-only'))
                metrics,z=measure(model,data[partition],rows[partition],bs,full,status)
                results[partition+('_full' if full else '_masked')]=metrics
                if full:zs[partition]=z
        information=probes(zs['train'],zs['development'],rows['train'],rows['development'])
        from ..phase1_v7.finalize import rank_diagnostics
        rank=rank_diagnostics(zs['development'])
        result=dict(state='complete',steps=step,planned_steps=a.steps,training_truncated=step<a.steps,elapsed_seconds=time.time()-started,
            evaluation_seconds=time.time()-begin,evaluations=results,probes=information,embedding=rank,
            checkpoint_sha256=file_hash(a.output/('best.pt' if (a.output/'best.pt').exists() else 'last.pt')))
        if time.time()>deadline:raise TimeoutError('Final evaluation exceeded deadline')
        write_json(a.output/'results.json',result)
        if wb:
            wb.summary.update({'result/state':'complete','result/elapsed_seconds':result['elapsed_seconds'],'result/checkpoint_sha256':result['checkpoint_sha256']})
            for name,value in information.items():
                for key,item in value.items():wb.summary[f'probes/{name}/{key}']=item
            for partition,metrics in results.items():
                for frame,value in metrics['frames'].items():
                    for channel,measurement in value['channels'].items():wb.summary[f'{partition}/{frame}/{channel}/mae']=measurement['mae']
            artifact=wandb.Artifact(a.output.name+'-evidence',type='evaluation')
            for name in ('manifest.json','results.json','learning.jsonl','tiny-fit.json','roundtrip.json','profile.json'):artifact.add_file(str(a.output/name))
            wb.log_artifact(artifact)
        status('complete',latest_evaluation=results['development_full']['balanced_price_relative_mean'])
    except BaseException as error:
        checkpoint();status('failed',error=f'{type(error).__name__}: {error}');raise
    finally:
        if wb:wb.finish()


if __name__=='__main__':main()
