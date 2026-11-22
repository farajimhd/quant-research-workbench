"""One active-task, eight-arm capacity campaign. No checkpoint uploads."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,copy,gc,json,math,time,traceback
from pathlib import Path
import numpy as np
import torch
import wandb
from .model import build,optimizer,restore
from ..phase1_v21.experiment import evaluate
from ..phase1_v21.objectives import loss
from ..phase1_v21.step import backward,qualified_replay,PARTS
from ..phase1_v20.encoders import restore as parent_model
from ..phase0_v3.experiment import cache
from ..phase1_v12.data import load_teacher
from ..phase1_v12.model import positions
from ..phase1_v11.data import verify
from ..phase1_v19.objectives import calibrate
from ..phase1_v10.step import stable_clip
from ..phase1_v8.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import file_hash,digest,write_json
from research.mlops.env import discover_env_files,load_env_files


def ranking(metrics,reference):
    """Equal-group geometric MAE ratios plus balanced direction error ratios."""
    ratios=[];directions=[]
    for k,v in metrics.items():
        if k.startswith(('masked/raw_mae/','all_visible/raw_mae/')) and reference.get(k,0)>0:
            ratios.append(v/reference[k])
        if ('moving_direction_pct' in k and k.startswith(('masked/candle/','all_visible/candle/'))) or ('balanced_direction_pct' in k and k.startswith(('activity/masked/','activity/all_visible/'))):
            if k in reference:directions.append((100-v+1)/(100-reference[k]+1))
    return dict(score=float(np.exp(np.mean(np.log(np.maximum(ratios,1e-9))))*.5+np.mean(directions)*.5),
        raw_mae_geomean_ratio=float(np.exp(np.mean(np.log(np.maximum(ratios,1e-9))))),
        raw_groups_improved=int(np.sum(np.array(ratios)<1)),raw_groups=len(ratios),
        worst_raw_mae_ratio=float(max(ratios)),direction_error_mean_ratio=float(np.mean(directions)))


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--windows',type=int,default=8388608);p.add_argument('--resume',action='store_true');a=p.parse_args()
    if not 1048576<=a.windows<=16777216 or a.windows%4096:raise ValueError('Bounded exposure, multiple of4096 required')
    a.device='cuda';a.source_revision=a.revision;preflight(a)
    root=Path('D:/TradingML/runtimes/bar_gpt');out=a.output;began=time.time()
    progress=lambda **kw:write_json(out/'progress.json',dict(updated=time.time(),**kw))
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    data=root/'phase1-v8-learned-mae-20261117/dataset';verified=verify(data)
    supplement=root/'phase1-v19-training-pool-20261121';sm=json.loads((supplement/'manifest.json').read_text())
    if digest({k:v for k,v in sm.items() if k!='hash'})!=sm['hash'] or sm['split_hash']!=verified['source_split_hash'] or file_hash(supplement/'train.npy')!=sm['files']['train.npy']:raise ValueError('Supplement provenance')
    cp=root/'phase1-v21-body-20261121/body_direction/checkpoint.pt';receipt=json.loads((cp.parent/'result.json').read_text())
    if file_hash(cp)!=receipt['checkpoint_sha']:raise ValueError('Parent changed')
    saved=torch.load(cp,map_location='cpu',weights_only=False)
    # Parent template contains architecture and model only, never its optimizer.
    template=dict(model=saved['model'],contract=saved['contract']);del saved
    manifest=dict(source=a.revision,parent_sha=file_hash(cp),data_hash=verified['hash'],supplement_hash=sm['hash'],split_hash=verified['source_split_hash'],
        windows_per_arm=a.windows,masking='16 visible3/4updates,64every4th; original source masks',checkpoint_upload=False,
        normalization='fixed original training means/std; logclose/body/wicks/log1pactivity; no median/reference',
        objective='unchanged V21 joint_repaired + prefloor activity change',sampling='uniform with replacement, no weights',
        selection='equal216group geometric rawMAE ratios and mean direction error ratios, equal weights; report worst groups',
        deadline_seconds=3600,campaign_deadline_seconds=28800)
    if (out/'manifest.json').exists():
        old=json.loads((out/'manifest.json').read_text())
        for key in ('source','parent_sha','data_hash','supplement_hash','windows_per_arm'):
            if old[key]!=manifest[key]:raise ValueError('Changed scientific resume: '+key)
        manifest=old;began=old['started']
    else:manifest['started']=began;write_json(out/'manifest.json',manifest)
    with lease(out):
        progress(stage='preparing immutable GPU cache',experiment=0)
        teacher,identity=load_teacher(root/'phase1-v12-frozen16-mae-20261121/teacher/teacher.pt',verified['source_split_hash'],'cuda')
        train=tuple(torch.cat((x,y)) for x,y in zip(cache(np.load(data/'train.npy',mmap_mode='r')),cache(np.load(supplement/'train.npy',mmap_mode='r'))))
        dev=cache(np.load(data/'development.npy',mmap_mode='r'))
        def anchors(items):
            with torch.no_grad():z=torch.cat([teacher.encode(items[0][i:i+512],items[1][i:i+512]) for i in range(0,len(items[0]),512)])
            return (*items,z)
        train=anchors(train);dev=anchors(dev);activity_scale,activity_cal=calibrate(train[2],train[1],train[3])
        manifest.update(train_windows=len(train[0]),dev_windows=len(dev[0]),teacher=identity,activity_calibration=activity_cal,cache_seconds=time.time()-began)
        write_json(out/'manifest.json',manifest)
        reference_path=out/'parent-metrics.json'
        if reference_path.exists():reference=json.loads(reference_path.read_text())
        else:
            parent=parent_model(template).cuda();detail,reference=evaluate(parent,teacher,dev)
            write_json(reference_path,reference);write_json(out/'parent-detail.json',detail);del parent
        results=json.loads((out/'comparison.json').read_text()) if (out/'comparison.json').exists() else []
        specs=[dict(kind='global512',wide=False),dict(kind='global256',wide=False),dict(kind='slots8',wide=False),dict(kind='slots16',wide=False),dict(kind='slots32',wide=False),dict(kind='global512',wide=True)]
        for index in range(8):
            if time.time()-began>28000:raise TimeoutError('Campaign deadline; no new experiment')
            if index<6:spec=specs[index];seed=559
            elif index==6:
                winner=min((r for r in results if r['architecture']['kind'].startswith('slots')),key=lambda r:r['ranking']['score'])
                spec=dict(winner['architecture'],wide=True);seed=559
            else:
                winner=min(results,key=lambda r:r['ranking']['score']);spec=winner['architecture'];seed=997
            name=f"e{index+1:02d}-{spec['kind']}-{'wide16' if spec['wide'] else 'feature4'}-s{seed}"
            arm=out/name
            if (arm/'result.json').exists():continue
            arm.mkdir(exist_ok=True);start=time.time();deadline=start+3500
            prior=arm/'checkpoint_last.pt';resume=torch.load(prior,map_location='cpu',weights_only=False) if a.resume and prior.exists() else None
            if resume:deadline=start+max(0,3500-resume['elapsed_seconds'])
            def admission():
                if (out/'STOP').exists() or (out.parent/'STOP').exists():raise InterruptedError('Human STOP')
                if time.time()>deadline-120:raise TimeoutError('Reserved completion/evaluation deadline reached')
            torch.manual_seed(17);model=build(template,**spec).cuda()
            initial={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
            contract=dict(manifest,architecture=spec,seed=seed,parameters=sum(p.numel() for p in model.parameters()),decoder_parameters=sum(p.numel() for p in model.decoder.parameters()),
                representation_values=int(spec['kind'][5:])*64 if spec['kind'].startswith('slots') else int(spec['kind'][6:]),
                hypothesis='Position preservation / representation capacity / feature width ablation; eighth repeats best seed',
                learning_rates=dict(feature=3e-7,inherited_temporal=1e-5,new=3e-4),batch=None)
            runfile=arm/'wandb.json'
            wid=json.loads(runfile.read_text())['id'] if runfile.exists() else wandb.util.generate_id()
            write_json(runfile,dict(id=wid,project='bargpt-phase01-v22-capacity'))
            w=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v22-capacity',name=name,id=wid,resume='allow',config=contract,dir=str(arm),reinit=True)
            graphs={};step=0;seen=0;seconds=0.;curve=[];best=float('inf');status='failed';g=torch.Generator(device='cuda').manual_seed(seed)
            opt=None
            def save():
                payload=dict(model=model.state_dict(),optimizer=opt.state_dict(),architecture=spec,parent_template=template,contract=contract,
                    step=step,seen=seen,seconds=seconds,curve=curve,best=best,rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),sampling_rng=g.get_state(),
                    elapsed_seconds=time.time()-start+(resume['elapsed_seconds'] if resume else 0))
                tmp=arm/'checkpoint_pending.pt';torch.save(payload,tmp);os.replace(tmp,prior)
            try:
                if resume:
                    contract=resume['contract'];batch=contract['batch'];model.load_state_dict(resume['model']);opt=optimizer(model);opt.load_state_dict(resume['optimizer'])
                    step=resume['step'];seen=resume['seen'];seconds=resume['seconds'];curve=resume['curve'];best=resume['best']
                    torch.set_rng_state(resume['rng']);torch.cuda.set_rng_state_all(resume['cuda_rng']);g.set_state(resume['sampling_rng'])
                else:
                    # Tiny fit has a fixed valid example, not a favorable sampled metric.
                    progress(stage='tiny overfit',experiment=index+1,name=name,elapsed=time.time()-start,deadline=3500)
                    tiny=copy.deepcopy(model);to=optimizer(tiny);inputs=tuple(q[:1] for q in train)
                    x,v,raw,f,z=inputs;pos=positions(1,'cuda',64);tiny_curve=[]
                    for t in range(201):
                        to.zero_grad(set_to_none=True);value,_=loss(tiny,x,v,raw,f,pos,z,activity_scale)
                        if t in (0,50,100,200):tiny_curve.append(dict(step=t,loss=float(value)))
                        if not torch.isfinite(value):raise FloatingPointError('Tiny loss')
                        if t<200:value.backward();stable_clip(tiny.parameters());to.step()
                    write_json(arm/'tiny.json',tiny_curve);w.log({'qualification/tiny_initial':tiny_curve[0]['loss'],'qualification/tiny_final':tiny_curve[-1]['loss']},step=0)
                    if tiny_curve[-1]['loss']>=tiny_curve[0]['loss']*.9:raise ValueError('Tiny fit failed10percent improvement')
                    del tiny,to,inputs;gc.collect();torch.cuda.empty_cache()
                    profiles=[]
                    for batch in (512,1024,2048,4096):
                        admission();progress(stage='profiling',experiment=index+1,name=name,batch=batch,elapsed=time.time()-start,deadline=3500)
                        model.load_state_dict(initial);opt=optimizer(model);replays={};torch.cuda.reset_peak_memory_stats()
                        for t in range(12):
                            ids=torch.arange(batch,device='cuda')%len(train[0]);x,v,raw,f,z=[q[ids] for q in train];pos=positions(batch,'cuda',64 if t%4==3 else 16)
                            inp=(x,v,raw,f,pos,z);opt.zero_grad(set_to_none=True)
                            if pos.shape[1] not in replays:replays[pos.shape[1]],actual=qualified_replay(model,inp,activity_scale)
                            else:actual=replays[pos.shape[1]](inp)
                            if not bool(torch.isfinite(actual[0])&torch.isfinite(actual[2])):raise FloatingPointError('Profile')
                            opt.step()
                            if t==3:torch.cuda.synchronize();tick=time.perf_counter()
                        torch.cuda.synchronize();profiles.append(dict(batch=batch,windows_per_second=8*batch/(time.perf_counter()-tick),peak_mib=torch.cuda.max_memory_allocated()/2**20,gradient_parity=True))
                        del replays,opt,actual,inp;opt=None;gc.collect();torch.cuda.empty_cache()
                    batch=max(profiles,key=lambda p:p['windows_per_second'])['batch'];contract.update(batch=batch,profiles=profiles)
                    write_json(arm/'profile.json',profiles);w.config.update(contract,allow_val_change=True)
                    model.load_state_dict(initial);opt=optimizer(model);torch.manual_seed(17);g.manual_seed(seed)
                write_json(arm/'manifest.json',contract);del initial
                # Every arm sees exactly the same sampled-window count. Batch-size
                # choices can alter optimizer steps, which are explicitly reported.
                evaluation_stride=a.windows//4
                while True:
                    admission()
                    if seen==0 or seen%evaluation_stride==0 or seen==a.windows:
                        progress(stage='validating',experiment=index+1,name=name,seen=seen,total=a.windows,elapsed=time.time()-start,deadline=3500)
                        detail,metrics=evaluate(model,teacher,dev);rank=ranking(metrics,reference)
                        write_json(arm/f'evaluation-{seen}.json',detail);write_json(arm/'latest-metrics.json',metrics)
                        curve.append(dict(step=step,seen=seen,metrics=metrics,ranking=rank));w.log({**{'development/'+k:v for k,v in metrics.items()},**{'comparison/'+k:v for k,v in rank.items()},'train/windows_seen':seen},step=step)
                        improved=rank['score']<best;best=min(best,rank['score']);save()
                        if improved:
                            import shutil
                            shutil.copyfile(prior,arm/'checkpoint_best.pt')
                        print(name,seen,rank,'close1s',metrics.get('all_visible/raw_mae/1s/trade_close'),flush=True)
                    if seen>=a.windows:break
                    tick=time.perf_counter();ids=torch.randint(len(train[0]),(batch,),device='cuda',generator=g);x,v,raw,f,z=[q[ids] for q in train]
                    pos=positions(batch,'cuda',64 if (step+1)%4==0 else 16,g);inp=(x,v,raw,f,pos,z);opt.zero_grad(set_to_none=True)
                    if pos.shape[1] not in graphs:graphs[pos.shape[1]],actual=qualified_replay(model,inp,activity_scale)
                    else:actual=graphs[pos.shape[1]](inp)
                    value,parts,norm=actual
                    if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Training')
                    opt.step();torch.cuda.synchronize();seconds+=time.perf_counter()-tick;step+=1;seen+=batch
                    if step%100==0:
                        speed=seen/max(seconds,1e-9);progress(stage='training',experiment=index+1,name=name,step=step,seen=seen,total=a.windows,loss=float(value),speed=speed,eta_seconds=(a.windows-seen)/speed,elapsed=time.time()-start,deadline=3500)
                        w.log({'train/loss':float(value),'train/windows_seen':seen,'train/cached_windows_per_second':speed,**{'train/'+k:float(v) for k,v in zip(PARTS,parts)}},step=step)
                # Comparable train/dev gap on frozen first2048 training windows.
                _,train_metrics=evaluate(model,teacher,tuple(q[:2048] for q in train))
                w.log({'train_evaluation/'+k:v for k,v in train_metrics.items()},step=step)
                result=dict(name=name,architecture=spec,seed=seed,ranking=curve[-1]['ranking'],metrics=curve[-1]['metrics'],train_metrics=train_metrics,
                    parameters=contract['parameters'],decoder_parameters=contract['decoder_parameters'],representation_values=contract['representation_values'],
                    batch=batch,steps=step,seen=seen,seconds=time.time()-start,cached_windows_per_second=seen/seconds,checkpoint_sha=file_hash(prior),wandb_url=w.url)
                write_json(arm/'result.json',result);results.append(result);write_json(out/'comparison.json',results);status='complete'
            except BaseException:
                if opt is not None:save()
                write_json(arm/'failure.json',dict(traceback=traceback.format_exc(),step=step,seen=seen,seconds=time.time()-start));raise
            finally:
                w.summary.update(status=status,checkpoint_upload=False);w.finish(exit_code=0 if status=='complete' else 1)
                graphs.clear();del model,opt;gc.collect();torch.cuda.empty_cache()
        progress(stage='complete',experiment=8,best=min(results,key=lambda r:r['ranking']['score'])['name'])


if __name__=='__main__':main()
