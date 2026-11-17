"""Deadline-bound, cached-subset training with independent native end evaluation."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',CUBLAS_WORKSPACE_CONFIG=':4096:8')
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import random
import time
import numpy as np
import torch
from .model import Config, Autoencoder, Transform, objective
from .metrics import evaluate, probes, summaries
from ..v5.transforms_v1 import BarInputTransformV1
from ..v5.shards.phase1_contract import file_hash, digest, write_json


def load_data(root):
    manifest=json.loads((root/'manifest.json').read_text())
    assert digest({k:v for k,v in manifest.items() if k!='hash'})==manifest['hash']
    result={}
    # Audit remains unopened until explicit finalist evaluation.
    for name in ('train','development','tiny'):
        p=root/f'{name}.npy'
        assert file_hash(p)==manifest['files'][p.name]
        result[name]=np.load(p,allow_pickle=False)
    return result,manifest


def calibration(rows):
    raw=torch.from_numpy(rows['values'].copy());valid=torch.from_numpy(rows['valid'].astype(bool))
    logs=Transform.logs(raw,valid)
    count=valid.sum((0,1))
    mean=logs.sum((0,1))/count
    std=(torch.where(valid,(logs-mean).square(),0).sum((0,1))/count).sqrt().clamp_min(.01)
    return mean.float(),std.float()


def cache(rows,mean,scale,device):
    raw=torch.from_numpy(rows['values'].copy());valid=torch.from_numpy(rows['valid'].astype(bool))
    target=((Transform.logs(raw,valid)-mean)/scale).float()
    chunks=[];masks=[]
    for start in range(0,len(rows),512):
        p=rows[start:start+512]
        transformed=BarInputTransformV1()(raw[start:start+512],valid[start:start+512],
            torch.from_numpy(p['end_us'].copy()),torch.from_numpy(p['timeframe_us'].copy()))
        transformed.values[...,:18]=torch.where(valid[start:start+512],target[start:start+512],0)
        chunks.append(transformed.values);masks.append(transformed.valid)
    return dict(values=torch.cat(chunks).to(device),valid=torch.cat(masks).to(device),
        target=target.to(device),mask=valid.to(device))


def batch(data,ids,visible,generator,full=False):
    x=data['values'][ids];v=data['valid'][ids]
    b=len(ids);device=x.device
    if full:pos=torch.arange(64,device=device).expand(b,-1)
    else:pos=torch.rand(b,64,device=device,generator=generator).argsort(1)[:,:visible].sort(1).values
    gather=pos[...,None].expand(-1,-1,27)
    mask=data['mask'][ids].clone()
    if not full:mask.scatter_(1,pos[...,None].expand(-1,-1,18),False)
    return x.gather(1,gather),v.gather(1,gather),pos,data['target'][ids],mask


@torch.no_grad()
def predict(model,data,batch_size,full=True):
    model.eval();pred=[];zs=[];masks=[]
    gen=torch.Generator(device=data['values'].device).manual_seed(731)
    for start in range(0,len(data['values']),batch_size):
        ids=torch.arange(start,min(start+batch_size,len(data['values'])),device=data['values'].device)
        x,v,p,y,m=batch(data,ids,model.config.visible,gen,full)
        z,out=model(x,v,p)
        pred.append(model.transform.inverse(out).cpu().numpy());zs.append(z.float().cpu().numpy());masks.append(m.cpu().numpy())
    return np.concatenate(pred),np.concatenate(zs),np.concatenate(masks)


def save_checkpoint(path,model,opt,scheduler,gen,step,started,identity,best):
    state=dict(model=model.state_dict(),optimizer=opt.state_dict(),scheduler=scheduler.state_dict(),
        generator=gen.get_state(),torch_rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),
        numpy_rng=np.random.get_state(),python_rng=random.getstate(),step=step,started=started,
        identity=identity,best=best,traversal='IID uniform selected-row sampling; generator state pins next indices and masks')
    tmp=path.with_suffix('.partial');torch.save(state,tmp);tmp.replace(path)


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--variant',choices=('pooled','scale_slots','flat_scale_slots'),default='pooled')
    p.add_argument('--minutes',type=float,default=20);p.add_argument('--steps',type=int,default=2400)
    p.add_argument('--width',type=int,default=128);p.add_argument('--layers',type=int,default=3)
    p.add_argument('--embedding',type=int,default=256);p.add_argument('--decoder',type=int,default=256)
    p.add_argument('--visible',type=int,default=16);p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--seed',type=int,default=17);p.add_argument('--resume',action='store_true')
    p.add_argument('--full-probability',type=float,default=0.)
    p.add_argument('--device',default='cuda');p.add_argument('--wandb-mode',choices=('online','disabled'),default='online')
    p.add_argument('--hypothesis',required=True);p.add_argument('--parent',default='none')
    a=p.parse_args(argv)
    assert 0<a.minutes<=60
    assert 0<=a.full_probability<=1
    if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():raise RuntimeError('STOP remains in force')
    if a.device=='cuda':
        proof=json.loads((a.output.parent/f'deployment-{a.source_revision[:9]}.json').read_text())
        if proof['revision']!=a.source_revision:raise ValueError('Source revision differs from immutable deployment receipt')
        source_root=Path(__file__).resolve().parents[3]
        for name,expected in proof['files'].items():
            if file_hash(source_root/name)!=expected:raise ValueError(f'Immutable snapshot changed: {name}')
    if a.output.exists() and not a.resume:raise FileExistsError(a.output)
    a.output.mkdir(parents=True,exist_ok=a.resume)
    import psutil
    own=psutil.Process()
    write_json(a.output/'process.json',dict(pid=own.pid,created=own.create_time(),command=own.cmdline()))
    torch.set_num_threads(4);torch.manual_seed(a.seed);np.random.seed(a.seed);random.seed(a.seed)
    torch.set_float32_matmul_precision('highest')
    device=torch.device(a.device)
    if device.type=='cuda':
        import subprocess
        gpu=subprocess.check_output(['nvidia-smi','--query-compute-apps=pid,process_name','--format=csv,noheader'],text=True)
        others=[];desktop=[]
        desktop_names={'dwm.exe','shellhost.exe','crossdeviceresume.exe','startmenuexperiencehost.exe',
            'searchhost.exe','explorer.exe','shellexperiencehost.exe','msedgewebview2.exe','lockapp.exe',
            'logioptionsplus_agent.exe','textinputhost.exe','taskmgr.exe','chatgpt.exe','codex-computer-use-swift.exe','windowsterminal.exe'}
        for line in gpu.splitlines():
            fields=line.split(',',1)
            if not fields[0].strip().isdigit():continue
            pid=int(fields[0]);name=fields[1].strip().lower() if len(fields)>1 else ''
            if pid==os.getpid():continue
            # WDDM reports desktop graphics as C+G even through query-compute-apps.
            # Only inspected OS/desktop executables qualify; unknown owners fail closed.
            if os.name=='nt' and (pid==4 or (name.startswith(('c:\\windows\\','c:\\program files\\','c:\\program files (x86)\\')) and Path(name).name in desktop_names)):
                desktop.append(dict(pid=pid,path=name));continue
            others.append(pid)
        write_json(a.output/'gpu-ownership.json',dict(desktop_clients=desktop,other_compute_pids=others))
        if others:raise RuntimeError(f'GPU owned by other processes: {others}')
    started=time.time();deadline=started+a.minutes*60
    rows,manifest=load_data(a.data)
    mean,scale=calibration(rows['train'])
    config=Config(a.variant,a.width,a.embedding,a.layers,a.decoder,a.visible)
    model=Autoencoder(config,mean,scale).to(device)
    identity=dict(version='phase1-v6-log-campaign',config=asdict(config),source_revision=a.source_revision,
        dataset_hash=manifest['hash'],seed=a.seed,learning_rate=a.lr,steps=a.steps,minutes=a.minutes,
        full_probability=a.full_probability,
        precision='FP32 encoder/readout; FP64 inverse',hypothesis=a.hypothesis,parent=a.parent,
        sampling='Equal mass per observed timeframe/last-close-price-band cell; uniform rows within cell. Calibration and metrics use unweighted selected observations.',
        selection='equal-frame/channel price relative mean + 0.1 worst price-band relative mean + 0.1 equal-frame/channel activity log1p MAE',
        parameters=sum(v.numel() for v in model.parameters()),calibration=dict(mean=mean.tolist(),scale=scale.tolist(),partition='train'),
        acceptance='Complete eval; finite predictions; compare balanced frame/channel/band relative errors, activity, scale and probes; reject worst-group regression unless justified')
    if a.resume:
        assert json.loads((a.output/'manifest.json').read_text())==identity,'Changed scientific configuration'
    else:write_json(a.output/'manifest.json',identity)
    data={k:cache(v,mean,scale,device) for k,v in rows.items()}
    groups=np.c_[rows['train']['timeframe_us'],np.digitize(rows['train']['values'][:,-1,3],[.1,1,5,20,100])]
    cells,group_ids,counts=np.unique(groups,axis=0,return_inverse=True,return_counts=True)
    sampling=torch.tensor(1/counts[group_ids],dtype=torch.float32,device=device)
    if not a.resume:
        write_json(a.output/'sampling.json',dict(cells=cells.tolist(),counts=counts.tolist(),
            per_row_probability=(1/counts/len(counts)).tolist(),interpretation='probability for each row in a cell; cells have equal total mass'))
    write_json(a.output/'startup.json',dict(seconds=time.time()-started,windows={k:len(v) for k,v in rows.items()},loader='preverified CPU preparation then complete GPU cache; no per-step loader wait',loader_wait_seconds=0))
    opt=torch.optim.AdamW(model.parameters(),lr=a.lr,weight_decay=.01)
    scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(opt,a.steps,eta_min=a.lr*.1)
    gen=torch.Generator(device=device).manual_seed(a.seed)
    step=0;best=float('inf')
    if a.resume:
        state=torch.load(a.output/'last.pt',map_location=device,weights_only=False)
        assert state['identity']==identity,'Changed scientific experiment cannot resume'
        model.load_state_dict(state['model']);opt.load_state_dict(state['optimizer']);scheduler.load_state_dict(state['scheduler'])
        gen.set_state(state['generator'].cpu());torch.set_rng_state(state['torch_rng'].cpu())
        torch.cuda.set_rng_state_all([s.cpu() for s in state['cuda_rng']]);np.random.set_state(state['numpy_rng']);random.setstate(state['python_rng'])
        step=state['step'];best=state['best'];started=state['started'];deadline=started+a.minutes*60
    def status(stage,**kw):
        write_json(a.output/'progress.json',dict(experiment=a.output.name,stage=stage,step=step,planned_steps=a.steps,
            elapsed=time.time()-started,limit_seconds=a.minutes*60,updated=time.time(),**kw))
    # Precision qualification on difficult values actually present in training.
    raw=torch.from_numpy(rows['train']['values'].copy());valid=torch.from_numpy(rows['train']['valid'].astype(bool))
    norm=((Transform.logs(raw,valid)-mean.double())/scale.double()).float()
    recovered=model.transform.cpu().inverse(norm)
    relative=((recovered-raw).abs()/raw.abs().clamp_min(1e-30))[valid & (raw>0)]
    assert torch.isclose(recovered,raw.double(),rtol=1e-4,atol=1e-5)[valid].all()
    model.to(device)
    zero_error=(recovered-raw).abs()[valid & (raw==0)]
    write_json(a.output/'roundtrip.json',dict(max_positive_relative=float(relative.max()),zero_count=len(zero_error),
        max_zero_absolute=float(zero_error.max()) if len(zero_error) else 0.,valid_count=int(valid.sum()),no_target_arguments=True,
        tolerance='rtol=1e-4, atol=1e-5 native units; zero activity has absolute error only'))
    # Fresh tiny fit has its own model/optimizer, does not contaminate candidate initialization.
    if not a.resume:
        status('tiny overfit')
        tiny=Autoencoder(config,mean,scale).to(device);to=torch.optim.AdamW(tiny.parameters(),lr=.001)
        ids=torch.arange(min(4,len(rows['tiny'])),device=device)
        x,v,pos,y,m=batch(data['tiny'],ids,config.visible,gen,True)
        first=None
        for k in range(240):
            to.zero_grad(set_to_none=True);_,prediction=tiny(x,v,pos);loss,_=objective(prediction,y,m,scale.to(device))
            if first is None:first=float(loss)
            loss.backward();assert all(torch.isfinite(q.grad).all() for q in tiny.parameters() if q.grad is not None)
            torch.nn.utils.clip_grad_norm_(tiny.parameters(),10);to.step()
        tiny_result=dict(initial=first,final=float(loss),ratio=float(loss)/first,steps=240)
        write_json(a.output/'tiny-fit.json',tiny_result)
        if tiny_result['ratio']>.25:raise RuntimeError('Tiny fit failed; diagnose before training')
        del tiny,to
    # Profile complete cached batch selection, mask construction, forward/backward and optimizer.
    profile=[]
    for bs in (() if a.resume else (128,512)):
        pm=Autoencoder(config,mean,scale).to(device);po=torch.optim.AdamW(pm.parameters(),lr=a.lr)
        if device.type=='cuda':torch.cuda.synchronize()
        begin=time.perf_counter()
        for k in range(12):
            ids=torch.randint(len(rows['train']),(bs,),device=device,generator=gen)
            full=bool(torch.rand((),device=device,generator=gen)<a.full_probability) if a.full_probability else False
            x,v,pos,y,m=batch(data['train'],ids,config.visible,gen,full)
            po.zero_grad(set_to_none=True);_,pred=pm(x,v,pos);loss,_=objective(pred,y,m,scale.to(device));loss.backward();po.step()
        if device.type=='cuda':torch.cuda.synchronize()
        elapsed=time.perf_counter()-begin
        profile.append(dict(batch=bs,seconds=elapsed,windows_per_second=bs*12/elapsed))
        del pm,po
    if a.resume:bs=json.loads((a.output/'profile.json').read_text())['selected_batch']
    else:
        bs=max(profile,key=lambda v:v['windows_per_second'])['batch']
        write_json(a.output/'profile.json',dict(configurations=profile,selected_batch=bs,precision='fp32',loader_workers=0,caching='complete fixed subset'))
    # Untrained controls, only development access.
    if not a.resume:
        _,ztrain,_=predict(model,data['train'],bs)
        _,zdev,_=predict(model,data['development'],bs)
        write_json(a.output/'untrained-probes.json',probes(ztrain,zdev,rows['train'],rows['development']))
        sy,_=summaries(rows['train']);dy,_=summaries(rows['development'])
        write_json(a.output/'raw-summary-probes.json',probes(sy,dy,rows['train'],rows['development']))
        constant=model.transform.inverse(torch.zeros(1,64,18,device=device)).cpu().numpy()
        write_json(a.output/'constant-development.json',evaluate(np.broadcast_to(constant,(len(rows['development']),64,18)),rows['development']))
        _,_,hidden=predict(model,data['development'],bs,False)
        visible=rows['development']['valid'].astype(bool)&~hidden
        control=np.empty_like(rows['development']['values'],dtype=np.float64)
        for j in range(18):
            vals=np.ma.array(rows['development']['values'][...,j],mask=~visible[...,j])
            med=np.ma.median(vals,axis=1).filled(float(constant[0,0,j]))
            control[...,j]=med[:,None]
        write_json(a.output/'visible-median-development.json',evaluate(control,rows['development'],hidden))
    wb=None
    if a.wandb_mode=='online':
        from research.mlops.env import discover_env_files,load_env_files
        load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
        import wandb
        ident=a.output/'wandb-identity.json'
        wid=json.loads(ident.read_text())['id'] if ident.exists() else wandb.util.generate_id()
        write_json(ident,dict(id=wid,project='bargpt-encoder-campaign-20261117',entity='mehdifaraji'))
        wb=wandb.init(entity='mehdifaraji',project='bargpt-encoder-campaign-20261117',id=wid,name=a.output.name,
            resume='must' if a.resume else 'never',config=identity,dir=str(a.output))
    elapsed_eval=0.;last_loss=None;training_start=time.time();initial_step=step
    try:
        while step<a.steps and time.time()<deadline-max(120,elapsed_eval*3):
            if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():break
            model.train();begin=time.perf_counter()
            ids=torch.multinomial(sampling,bs,replacement=True,generator=gen)
            full=bool(torch.rand((),device=device,generator=gen)<a.full_probability) if a.full_probability else False
            x,v,pos,y,m=batch(data['train'],ids,config.visible,gen,full)
            opt.zero_grad(set_to_none=True);_,pred=model(x,v,pos);loss,_=objective(pred,y,m,scale.to(device))
            if not torch.isfinite(loss):raise FloatingPointError('Nonfinite objective')
            loss.backward();norm=torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True)
            opt.step();scheduler.step();step+=1;last_loss=float(loss)
            if step%25==0:
                rate=(step-initial_step)*bs/max(time.time()-training_start,1e-9)
                status('training',loss=last_loss,windows_per_second=rate,gpu_memory_bytes=torch.cuda.max_memory_allocated() if device.type=='cuda' else 0)
            if step%200==0 or step==a.steps:
                status('development validation',loss=last_loss)
                begin=time.time();pred,z,_=predict(model,data['development'],bs)
                metrics=evaluate(pred,rows['development']);elapsed_eval=time.time()-begin
                score=metrics['selection_score']
                record=dict(step=step,elapsed=time.time()-started,train_loss=last_loss,selection_score=score,
                    balanced_price_relative_mean=metrics['balanced_price_relative_mean'],activity_log_mae=metrics['balanced_activity_log_mae'],
                    worst_price_band_relative_mean=metrics['worst_price_band_relative_mean'],validation_seconds=elapsed_eval)
                with (a.output/'learning.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
                if wb:wb.log({'train/loss':last_loss,'development/selection_score':score,
                    'development/balanced_price_relative_mean':metrics['balanced_price_relative_mean'],'development/activity_log_mae':metrics['balanced_activity_log_mae'],
                    'development/worst_price_band_relative_mean':record['worst_price_band_relative_mean'],'system/validation_seconds':elapsed_eval},step=step)
                if score<best:
                    best=score;save_checkpoint(a.output/'best.pt',model,opt,scheduler,gen,step,started,identity,best)
                save_checkpoint(a.output/'last.pt',model,opt,scheduler,gen,step,started,identity,best)
        save_checkpoint(a.output/'last.pt',model,opt,scheduler,gen,step,started,identity,best)
        status('final evaluation',loss=last_loss)
        if (a.output/'best.pt').exists():model.load_state_dict(torch.load(a.output/'best.pt',map_location=device,weights_only=False)['model'])
        evaluations={};embeddings={};eval_started=time.time()
        for name in ('train','development'):
            for full in (True,False):
                if time.time()>=deadline:raise TimeoutError('Deadline before complete evaluation')
                pred,z,mask=predict(model,data[name],bs,full)
                evaluations[name+('_full' if full else '_masked')]=evaluate(pred,rows[name],mask)
                if full:embeddings[name]=z
        information=probes(embeddings['train'],embeddings['development'],rows['train'],rows['development'])
        result=dict(state='complete',steps=step,planned_steps=a.steps,training_truncated=step<a.steps,
            elapsed_seconds=time.time()-started,evaluation_seconds=time.time()-eval_started,evaluations=evaluations,probes=information,
            embedding_variance=embeddings['development'].var(0).tolist(),constant_dimensions=int((embeddings['development'].std(0)<1e-7).sum()),
            gpu_peak_bytes=torch.cuda.max_memory_allocated() if device.type=='cuda' else 0,
            checkpoint_sha256=file_hash(a.output/('best.pt' if (a.output/'best.pt').exists() else 'last.pt')))
        if time.time()>=deadline:raise TimeoutError('Evaluation exceeded experiment deadline')
        write_json(a.output/'results.json',result)
        if wb:
            wb.summary.update({'result/state':'complete','result/development_relative_mean':evaluations['development_full']['balanced_price_relative_mean'],
                'result/elapsed_seconds':result['elapsed_seconds'],'result/checkpoint_sha256':result['checkpoint_sha256']})
            for name,measurement in information.items():
                for key,value in measurement.items():wb.summary[f'probes/{name}/{key}']=value
            for partition,measurement in evaluations.items():
                for frame,group in measurement['frames'].items():
                    for channel,value in group['channels'].items():
                        wb.summary[f'{partition}/{frame}/{channel}/mae']=value['mae']
            artifact=wandb.Artifact(a.output.name+'-evidence',type='evaluation')
            for name in ('manifest.json','results.json','profile.json','roundtrip.json','tiny-fit.json','learning.jsonl'):
                if (a.output/name).exists():artifact.add_file(str(a.output/name))
            wb.log_artifact(artifact)
        status('complete',latest_evaluation=evaluations['development_full']['balanced_price_relative_mean'])
    finally:
        if wb:wb.finish()


if __name__=='__main__':main()
