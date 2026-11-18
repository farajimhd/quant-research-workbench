"""Deadline-bounded joint architecture experiments on fixed causal sessions."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,json,random,time
from dataclasses import asdict
from pathlib import Path
import numpy as np
import torch
from .data import load,cache,calibrate,anchor
from .model import Config,JointModel,objective
from .metrics import measure
from ..phase1_v8.runtime import preflight
from ..v5.shards.phase1_contract import file_hash,write_json


def loss_for(model,data,reconstruction_weight,masked_probability=0.,forecast_weight=1.):
    output=(model(data['x'],data['valid'],data['ages'],reconstruct=False) if forecast_weight else
        dict(windows=None,forecast=None,future_summary=None))
    z=output['windows']
    if masked_probability>0:
        shape=data['valid'].shape[:-1]
        visible=torch.rand(shape,device=data['x'].device).argsort(-1).argsort(-1)<16
        masked=torch.rand((*shape[:2],1),device=data['x'].device)<masked_probability
        v=data['valid']&(~masked|visible)[...,None]
        z=model.encode_windows(data['x'],v)
    elif z is None:z=model.encode_windows(data['x'],data['valid'])
    if output['windows'] is None:output['windows']=z
    detached=z.detach()
    # Same decoder training weight across candidates; lambda controls only the
    # reconstruction gradient entering the encoder. At zero it is a trained probe.
    output['reconstruction']=model.reconstruct(detached+(reconstruction_weight/.1)*(z-detached))
    _,parts=objective(model,output,data,.1)
    return forecast_weight*parts['forecast']+.1*parts['reconstruction']+model.config.summary_weight*parts['future_summary'],parts


def main():
    p=argparse.ArgumentParser();p.add_argument('--data',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--source-revision',required=True);p.add_argument('--hypothesis',required=True);p.add_argument('--parent',default='none')
    p.add_argument('--architecture',choices=('patch','bar','variable','conv','mlp'),default='patch')
    p.add_argument('--width',type=int,default=128);p.add_argument('--embedding',type=int,default=256)
    p.add_argument('--layers',type=int,default=2);p.add_argument('--global-layers',type=int,default=2)
    p.add_argument('--patch',type=int,default=8);p.add_argument('--dropout',type=float,default=.05)
    p.add_argument('--precision',choices=('bf16','fp32'),default='bf16');p.add_argument('--reconstruction-weight',type=float,default=.1)
    p.add_argument('--summary-weight',type=float,default=0.);p.add_argument('--masked-reconstruction-probability',type=float,default=0.)
    p.add_argument('--price-huber-beta',type=float,default=1.);p.add_argument('--calibration-manifest',type=Path)
    p.add_argument('--readout',choices=('cls','flatten'),default='cls');p.add_argument('--linear-context',action='store_true')
    p.add_argument('--reconstruction-price-scale',type=float,default=.02)
    p.add_argument('--anchor-mode',choices=('historical','learned'),default='historical')
    p.add_argument('--last-context',action='store_true')
    p.add_argument('--context-embedding',type=int,default=0)
    p.add_argument('--horizon-queries',action='store_true')
    p.add_argument('--activity-basis',action='store_true')
    p.add_argument('--initial-checkpoint',type=Path)
    p.add_argument('--forecast-weight',type=float,default=1.)
    p.add_argument('--freeze-encoder',action='store_true')
    p.add_argument('--freeze-reconstruction',action='store_true')
    p.add_argument('--examples',type=int,default=262144);p.add_argument('--minutes',type=float,default=45)
    p.add_argument('--batch-sizes',type=int,nargs='+',default=[32,64,128,256]);p.add_argument('--lr',type=float,default=.0003)
    p.add_argument('--seed',type=int,default=17);p.add_argument('--tiny-steps',type=int,default=600)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda');p.add_argument('--wandb-mode',choices=('online','disabled'),default='online')
    p.add_argument('--resume',action='store_true');a=p.parse_args()
    if not 0<a.minutes<=60 or a.examples<=0 or a.tiny_steps<1 or not 0<=a.masked_reconstruction_probability<=1 or a.summary_weight<0 or a.price_huber_beta<=0:raise ValueError('Invalid finite experiment')
    if a.forecast_weight<0 or (a.forecast_weight==0 and (a.reconstruction_weight<=0 or a.summary_weight!=0)):raise ValueError('Invalid reconstruction pretext objective')
    if a.freeze_encoder and (not a.initial_checkpoint or a.anchor_mode!='learned' or a.forecast_weight<=0):raise ValueError('Frozen forecasting requires a pretrained learned-anchor encoder')
    if a.freeze_reconstruction and not a.freeze_encoder:raise ValueError('Fixed reconstruction requires frozen encoder')
    started=time.time();deadline=started+a.minutes*60;preflight(a)
    torch.set_num_threads(4);torch.manual_seed(a.seed);np.random.seed(a.seed);random.seed(a.seed)
    torch.set_float32_matmul_precision('highest');step=0;progress={};wb=None
    def status(stage=None,**kw):
        if stage is not None:progress['stage']=stage
        progress.update(experiment=a.output.name,updated=time.time(),elapsed=time.time()-started,limit_seconds=a.minutes*60,step=step,**kw)
        write_json(a.output/'progress.json',progress)
    status('verifying data and preparing causal cache')
    rows,manifest=load(a.data);cpu={k:cache(v) for k,v in rows.items()}
    calibration_parent=None
    if a.calibration_manifest:
        prior=json.loads(a.calibration_manifest.read_text())
        if prior['dataset_hash'] not in (manifest['hash'],manifest.get('expansion',{}).get('base_hash')):raise ValueError('Calibration must come from same or byte-exact retained training subset')
        parameters={k:torch.tensor(v) for k,v in prior['parameters'].items()}
        calibration_parent=dict(manifest_sha256=file_hash(a.calibration_manifest),dataset_hash=prior['dataset_hash'],source_revision=prior['source_revision'])
    else:parameters=calibrate(cpu['train'])
    config=Config(a.architecture,a.width,a.embedding,a.layers,a.global_layers,a.patch,a.dropout,a.precision,a.summary_weight,a.price_huber_beta,
        a.readout,a.linear_context,a.reconstruction_price_scale,a.anchor_mode,a.last_context,a.context_embedding,a.horizon_queries,a.activity_basis)
    model=JointModel(config,**parameters).to(a.device)
    initialization=None
    if a.initial_checkpoint:
        initial=torch.load(a.initial_checkpoint,map_location=a.device,weights_only=False)
        prior=initial['identity']
        if Config(**prior['config'])!=config or prior['dataset_hash']!=manifest['hash'] or prior['parameters']!={k:v.tolist() for k,v in parameters.items()}:
            raise ValueError('Fine-tuning requires identical architecture, dataset and calibration')
        model.load_state_dict(initial['model'])
        initialization=dict(checkpoint_sha256=file_hash(a.initial_checkpoint),parent_step=initial['step'],parent_source_revision=prior['source_revision'],
            policy='New scientific experiment: parent model weights only, fresh optimizer, scheduler and seeded traversal. Not an engineering resume.')
        del initial
    if a.freeze_encoder:
        model.encoder.requires_grad_(False)
        frozen_state={k:v.detach().cpu().clone() for k,v in model.encoder.state_dict().items()}
    if a.freeze_reconstruction:
        model.reconstruction.requires_grad_(False)
        frozen_reconstruction={k:v.detach().cpu().clone() for k,v in model.reconstruction.state_dict().items()}
    identities={name:json.loads((a.data/(name+'-identities.json')).read_text()) for name in ('train','development','novel_development')}
    identity=dict(version='joint-v9-1',config=asdict(config),source_revision=a.source_revision,dataset_hash=manifest['hash'],
        parameters={k:v.tolist() for k,v in parameters.items()},seed=a.seed,examples=a.examples,minutes=a.minutes,lr=a.lr,
        reconstruction_encoder_weight=a.reconstruction_weight,reconstruction_decoder_weight=.1,
        masked_reconstruction_probability=a.masked_reconstruction_probability,masked_visible_bars=16,
        calibration_parent=calibration_parent,
        initialization=initialization,
        forecast_weight=a.forecast_weight,freeze_encoder=a.freeze_encoder,freeze_reconstruction=a.freeze_reconstruction,
        hypothesis=a.hypothesis,parent=a.parent,parameter_count=sum(v.numel() for v in model.parameters()),
        window_encoder_parameters=sum(v.numel() for v in model.encoder.parameters()),reconstruction_decoder_parameters=sum(v.numel() for v in model.reconstruction.parameters()),
        forecasting=('Direct next 64 observed 1s bars. Historical last-bar anchor is a declared causal control.' if a.anchor_mode=='historical' else
            'Direct next 64 observed 1s bars. Last-bar anchor decoded from learned window embedding; Phase 2 receives only embeddings and causal ages.')+' Future values/masks/clocks never prediction inputs.',
        reconstruction='All 16 input windows from learned window latents only. No input/reference decoder bypass. Lambda zero means detached trained reconstruction probe.',
        selection=('Development balanced reconstruction selection score; forecasting head is untrained, not a forecasting candidate.' if a.forecast_weight==0 else 'Average development and unseen-ticker development balanced price/activity MAE ratios versus causal persistence; inspect confidence intervals, groups and reconstruction jointly.'),
        sampling='Uniform frozen origins, up to four per session; training-only calibration, unweighted grouped evaluation.',
        acceptance='Tiny future overfit, finite valid outputs, native and persistence/mean controls, paired session confidence intervals; reconstruction improvement is supporting evidence, not future skill proof.')
    if a.resume:
        if json.loads((a.output/'manifest.json').read_text())!=identity:raise ValueError('Changed experiment cannot resume')
    else:write_json(a.output/'manifest.json',identity)
    data={name:{k:v.to(a.device) for k,v in d.items()} for name,d in cpu.items()};del cpu
    write_json(a.output/'startup.json',dict(seconds=time.time()-started,examples={k:len(v) for k,v in rows.items()},cache='GPU cached causal inputs and separate targets',loader_wait_seconds=0))
    def batch(d,ids):return {k:v[ids] for k,v in d.items()}
    if not a.resume:
        status('tiny overfit: four training examples')
        tiny=JointModel(config,**parameters).to(a.device);optimizer=torch.optim.AdamW(tiny.parameters(),lr=.001,fused=a.device=='cuda')
        b=batch(data['tiny'],torch.arange(4,device=a.device));first=None
        for k in range(a.tiny_steps):
            optimizer.param_groups[0]['lr']=.00001+.00099*.5*(1+np.cos(np.pi*k/max(1,a.tiny_steps-1)))
            optimizer.zero_grad(set_to_none=True);loss,parts=loss_for(tiny,b,a.reconstruction_weight,a.masked_reconstruction_probability,a.forecast_weight)
            if first is None:
                first=float(parts['forecast'].detach());first_reconstruction=float(parts['reconstruction'].detach())
            if not torch.isfinite(loss):raise FloatingPointError('Nonfinite tiny fit')
            loss.backward();torch.nn.utils.clip_grad_norm_(tiny.parameters(),1.,error_if_nonfinite=True);optimizer.step()
            if k%100==0:status(tiny_step=k,tiny_forecast=float(parts['forecast'].detach()))
        tiny.eval()
        with torch.no_grad():_,last=loss_for(tiny,b,a.reconstruction_weight,a.masked_reconstruction_probability,a.forecast_weight)
        tiny_result=dict(initial_forecast=first,final_forecast=float(last['forecast']),ratio=float(last['forecast'])/max(first,1e-12),steps=a.tiny_steps,
            initial_reconstruction=first_reconstruction,final_reconstruction=float(last['reconstruction']),
            reconstruction_ratio=float(last['reconstruction'])/max(first_reconstruction,1e-12))
        write_json(a.output/'tiny-fit.json',tiny_result)
        if a.forecast_weight>0 and tiny_result['ratio']>.2 and tiny_result['final_forecast']>.01:raise RuntimeError('Tiny future fit failed; diagnose before scaling')
        if a.reconstruction_weight>0 and tiny_result['reconstruction_ratio']>.5:raise RuntimeError('Tiny reconstruction failed to halve error; diagnose before scaling')
        del tiny,optimizer
        profile=[];pg=torch.Generator(device=a.device).manual_seed(917)
        for bs in a.batch_sizes:
            status('profiling complete training step',profile_batch=bs)
            pm=JointModel(config,**parameters).to(a.device)
            if a.freeze_encoder:pm.encoder.requires_grad_(False);pm.encoder.eval()
            if a.freeze_reconstruction:pm.reconstruction.requires_grad_(False)
            po=torch.optim.AdamW(pm.parameters(),lr=a.lr,fused=a.device=='cuda')
            for k in range(12):
                if k==3:
                    if a.device=='cuda':torch.cuda.synchronize();torch.cuda.reset_peak_memory_stats()
                    begin=time.perf_counter()
                ids=torch.randint(len(rows['train']),(bs,),device=a.device,generator=pg);b=batch(data['train'],ids)
                po.zero_grad(set_to_none=True);loss,_=loss_for(pm,b,a.reconstruction_weight,a.masked_reconstruction_probability,a.forecast_weight)
                if not torch.isfinite(loss):raise FloatingPointError('Nonfinite profile')
                loss.backward();torch.nn.utils.clip_grad_norm_(pm.parameters(),1.,error_if_nonfinite=True);po.step()
            if a.device=='cuda':torch.cuda.synchronize()
            profile.append(dict(batch=bs,origins_per_second=bs*9/(time.perf_counter()-begin),peak_bytes=torch.cuda.max_memory_allocated() if a.device=='cuda' else 0))
            del pm,po
        bs=max(profile,key=lambda x:x['origins_per_second'])['batch']
        write_json(a.output/'profile.json',dict(configurations=profile,selected_batch=bs,precision=a.precision,includes_finite_and_clip=True))
        status('parent initialization development control' if a.initial_checkpoint else 'untrained development control')
        control=measure(model,data['development'],rows['development'],identities['development'],128,False)
        write_json(a.output/('initialization-control.json' if a.initial_checkpoint else 'untrained-control.json'),control)
    else:bs=json.loads((a.output/'profile.json').read_text())['selected_batch']
    steps=int(np.ceil(a.examples/bs));interval=max(1,steps//8)
    opt=torch.optim.AdamW(model.parameters(),lr=a.lr,weight_decay=.01,fused=a.device=='cuda')
    scheduler=torch.optim.lr_scheduler.CosineAnnealingLR(opt,steps,eta_min=a.lr*.05);gen=torch.Generator(device=a.device).manual_seed(a.seed)
    best=float('inf');seen=0
    if a.resume:
        state=torch.load(a.output/'last.pt',map_location=a.device,weights_only=False)
        if state['identity']!=identity:raise ValueError('Checkpoint identity mismatch')
        model.load_state_dict(state['model']);opt.load_state_dict(state['optimizer']);scheduler.load_state_dict(state['scheduler'])
        gen.set_state(state['generator'].cpu());torch.set_rng_state(state['torch_rng'].cpu());np.random.set_state(state['numpy_rng']);random.setstate(state['python_rng'])
        if a.device=='cuda':torch.cuda.set_rng_state_all([v.cpu() for v in state['cuda_rng']])
        step=state['step'];seen=state['seen'];started=state['started'];deadline=started+a.minutes*60;best=state['best']
    def save(name='last.pt'):
        state=dict(model=model.state_dict(),optimizer=opt.state_dict(),scheduler=scheduler.state_dict(),generator=gen.get_state(),
            torch_rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),numpy_rng=np.random.get_state(),python_rng=random.getstate(),
            identity=identity,step=step,seen=seen,started=started,best=best,traversal='IID uniform frozen origins; exact generator and RNG continuation')
        path=a.output/name;temp=path.with_suffix('.partial');torch.save(state,temp);temp.replace(path)
    try:
        if a.wandb_mode=='online':
            from research.mlops.env import discover_env_files,load_env_files
            load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
            import wandb
            path=a.output/'wandb-identity.json';wid=json.loads(path.read_text())['id'] if path.exists() else wandb.util.generate_id()
            write_json(path,dict(id=wid,entity='mehdifaraji',project='bargpt-joint-v9-20261117'))
            wb=wandb.init(entity='mehdifaraji',project='bargpt-joint-v9-20261117',id=wid,name=a.output.name,resume='must' if a.resume else 'never',config=identity,dir=str(a.output))
        training=time.time();initial_seen=seen;validation_seconds=0
        while step<steps and time.time()<deadline-max(180,validation_seconds*4):
            if (a.output/'STOP').exists() or (a.output.parent/'STOP').exists():save();status('stopped');return
            model.train();
            if a.freeze_encoder:model.encoder.eval()
            n=min(bs,a.examples-seen);ids=torch.randint(len(rows['train']),(n,),device=a.device,generator=gen);b=batch(data['train'],ids)
            opt.zero_grad(set_to_none=True);loss,parts=loss_for(model,b,a.reconstruction_weight,a.masked_reconstruction_probability,a.forecast_weight)
            if not torch.isfinite(loss):raise FloatingPointError('Nonfinite training loss')
            loss.backward();torch.nn.utils.clip_grad_norm_(model.parameters(),1.,error_if_nonfinite=True);opt.step();scheduler.step();step+=1;seen+=n
            if step%10==0:status('training',planned_steps=steps,seen=seen,origins_per_second=(seen-initial_seen)/max(time.time()-training,.001),
                forecast_loss=float(parts['forecast'].detach()),reconstruction_loss=float(parts['reconstruction'].detach()),future_summary_loss=float(parts['future_summary'].detach()),gpu_memory_bytes=torch.cuda.max_memory_allocated() if a.device=='cuda' else 0)
            if step%interval==0 or step in (128,256) or step==steps:
                begin=time.time();metrics={}
                for partition in ('development','novel_development'):
                    status('validation '+partition)
                    metrics[partition]=measure(model,data[partition],rows[partition],identities[partition],128,True,status)
                validation_seconds=time.time()-begin;score=np.mean([r['reconstruction']['selection_score'] if a.forecast_weight==0 else r['selection_score'] for r in metrics.values()])
                record=dict(step=step,examples=seen,elapsed=time.time()-started,selection_score=float(score),validation_seconds=validation_seconds,
                    metrics={k:{n:v[n] for n in ('price_mae_skill','activity_mae_skill','selection_score')} for k,v in metrics.items()},
                    reconstruction={k:v['reconstruction']['balanced_price_relative_mean'] for k,v in metrics.items()})
                with (a.output/'learning.jsonl').open('a') as f:f.write(json.dumps(record)+'\n')
                write_json(a.output/'development-latest.json',metrics)
                if score<best:best=float(score);save('best.pt')
                save()
                if wb:wb.log({'selection/score':score,'loss/forecast':float(parts['forecast'].detach()),'loss/reconstruction':float(parts['reconstruction'].detach()),'loss/future_summary':float(parts['future_summary'].detach()),
                    **{k+'/'+n:v[n] for k,v in metrics.items() for n in ('price_mae_skill','activity_mae_skill','selection_score')},
                    **{k+'/reconstruction_price_relative':v['reconstruction']['balanced_price_relative_mean'] for k,v in metrics.items()}},step=step)
        save();model.load_state_dict(torch.load(a.output/'best.pt',map_location=a.device,weights_only=False)['model']);results={}
        for partition in ('train','development','novel_development'):
            if time.time()>deadline:raise TimeoutError('Evaluation deadline')
            status('final evaluation '+partition)
            ids=np.random.default_rng(417).choice(len(rows[partition]),min(1024,len(rows[partition])),replace=False) if partition=='train' else np.arange(len(rows[partition]))
            d=batch(data[partition],torch.as_tensor(ids,device=a.device));r=rows[partition][ids];meta=[identities[partition][i] for i in ids]
            results[partition]=measure(model,d,r,meta,128,True,status)
        result=dict(state='complete',step=step,examples=seen,planned_examples=a.examples,truncated=seen<a.examples,elapsed_seconds=time.time()-started,
            train_evaluation='Fixed random up to 1024 training origins; full development sets',evaluations=results,checkpoint_sha256=file_hash(a.output/'best.pt'))
        if time.time()>deadline:raise TimeoutError('Incomplete final evaluation')

        if a.freeze_encoder:
            for key,value in model.encoder.state_dict().items():torch.testing.assert_close(value.cpu(),frozen_state[key],rtol=0,atol=0)
            result['frozen_encoder_exactly_preserved']=True
        if a.freeze_reconstruction:
            for key,value in model.reconstruction.state_dict().items():torch.testing.assert_close(value.cpu(),frozen_reconstruction[key],rtol=0,atol=0)
            result['frozen_reconstruction_exactly_preserved']=True
        result['forecast_head_trained']=a.forecast_weight>0
        write_json(a.output/'results.json',result);status('complete',seen=seen,planned_steps=steps)
        if wb:
            wb.summary.update({'result/state':'complete','result/checkpoint_sha256':result['checkpoint_sha256'],'result/seconds':result['elapsed_seconds']})
            artifact=wandb.Artifact(a.output.name+'-evidence',type='evaluation')
            for name in ('manifest.json','results.json','learning.jsonl','profile.json','tiny-fit.json'):artifact.add_file(str(a.output/name))
            wb.log_artifact(artifact)
    except BaseException as error:save();status('failed',error=f'{type(error).__name__}: {error}');raise
    finally:
        if wb:wb.finish()


if __name__=='__main__':main()
