"""Bounded preservation campaign; native GPU cache, qualified graphs, local weights."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,gc,json,math,shutil,time,traceback
from pathlib import Path
import torch
import wandb
from .model import Hybrid,parity
from .data import load
from .objectives import loss
from .step import qualified,rate
from .gates import check
from ..phase1_v25.metrics import evaluate,summary
from ..phase1_v12.model import positions
from ..phase1_v24.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import write_json,file_hash
from research.mlops.env import discover_env_files,load_env_files

PARTS=('price','activity','anchor','candle','range','activity_direction')


def generators(seed):return [torch.Generator(device='cuda').manual_seed(seed+i*100003) for i in range(3)]


def sample(data,batch,step,g,all_visible=False):
    ids=torch.randint(len(data[0]),(batch,),device='cuda',generator=g[0])
    pp=positions(batch,'cuda',64 if all_visible or (step+1)%4==0 else 16,g[1])
    ap=positions(batch,'cuda',64 if all_visible else 56,g[2])
    return (*[t[ids] for t in data],pp,ap)


def optimizer(model,peaks):
    inherited=[];readout=[]
    for name,p in model.price.temporal.named_parameters():
        (inherited if name.startswith(('stem.','blocks.')) or name in ('position','cls') else readout).append(p)
    groups=[dict(params=list(model.price.bar.parameters()),peak=peaks['price']*.03,name='price_bar'),
        dict(params=inherited,peak=peaks['price']*.2,name='price_core'),
        dict(params=readout+list(model.heads['price'].parameters()),peak=peaks['price'],name='price_readout'),
        dict(params=list(model.activity.parameters())+list(model.heads['activity'].parameters()),peak=peaks['activity'],name='activity')]
    if model.has_fusion:groups.append(dict(params=list(model.fusion.parameters()),peak=peaks['fusion'],name='fusion'))
    for group in groups:group['lr']=group['peak']
    return torch.optim.AdamW(groups,weight_decay=0.,fused=True)


def terms(model,b):
    with torch.no_grad():
        out,_=model(b[0],b[1],(b[-2],b[-1]));value,parts=loss(model,out,*b[:3],b[4],b[5],b[6])
    return float(value),{k:float(v) for k,v in parts.items()}


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--windows',type=int,default=1048576);p.add_argument('--resume',action='store_true');a=p.parse_args()
    if a.windows not in (262144,524288,1048576):raise ValueError('Bounded exposure required')
    a.device='cuda';a.source_revision=a.revision;preflight(a);out=a.output;started=time.time()
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    progress=lambda **kw:write_json(out/'progress.json',dict(updated=time.time(),**kw))
    with lease(out):
        progress(stage='Preparing immutable GPU cache')
        train,dev,parents,provenance=load(Path('D:/TradingML/runtimes/bar_gpt'))
        manifest=dict(source=a.revision,started=started,**provenance,windows=a.windows,experiments=6,checkpoint_upload=False,
            initialization='V24 compact price path plus V25 dedicated activity; price path keeps all-feature inputs',
            representation='64x64 learned slots, 32 per branch; decoder embedding only',
            transform='Parent frozen training calibration; no local target references',
            loss='V25 independent family-normalized objectives plus original V24 price bar-latent anchor weight one',
            masks='Price16 visible three updates,64 fourth; activity56 visible; full validation64/56/16 common visibility',
            deadlines=dict(experiment_seconds=3600,campaign_seconds=28800),preparation_seconds=time.time()-started)
        if (out/'manifest.json').exists():
            prior=json.loads((out/'manifest.json').read_text())
            for k in ('source','data_hash','supplement_hash','calibration_hash'):
                if prior[k]!=manifest[k]:raise ValueError('Resume identity mismatch '+k)
            manifest=prior;started=prior['started'];a.windows=prior['windows']
        else:write_json(out/'manifest.json',manifest)
        results=json.loads((out/'comparison.json').read_text()) if (out/'comparison.json').exists() else []
        selected=manifest.get('selected_rates',{})
        for index,base_mode in enumerate(('assembly','activity','independent','fusion','gradual','repeat')):
            if time.time()-started>28000:raise TimeoutError('Campaign deadline')
            seed=1559 if base_mode=='repeat' else 559;mode=base_mode;repeat=None
            if base_mode=='repeat':
                candidates=[r for r in results[1:] if r['best_step']>0 and r['best_gate']['accepted']]
                if not candidates:
                    write_json(out/'repeat-skipped.json',dict(reason='No nonzero update retained both families; no qualified training configuration to repeat'))
                    break
                repeat=min(candidates,key=lambda r:r['best_gate']['score']);mode=repeat['mode']
            name=f'e{index+1:02d}-{base_mode}-s{seed}';arm=out/name
            if (arm/'result.json').exists():continue
            arm.mkdir(exist_ok=True);begin=time.time();saved=None;checkpoint=arm/'checkpoint_last.pt'
            if a.resume and checkpoint.exists():saved=torch.load(checkpoint,map_location='cpu',weights_only=False)
            if (arm/'failure.json').exists() and saved is None:raise RuntimeError('Inspect failed qualification before a new immutable attempt')
            elapsed_before=saved['elapsed'] if saved else 0.;deadline=begin+3500-elapsed_before
            def admit(reserve=240):
                if (out/'STOP').exists() or (out.parent/'STOP').exists():raise InterruptedError('Human STOP preserved')
                if time.time()>deadline-reserve:raise TimeoutError('Evaluation/checkpoint reserve')
            def update(stage,**kw):progress(name=name,stage=stage,elapsed=time.time()-begin+elapsed_before,**kw)
            fusion=mode in ('fusion','gradual');model=Hybrid(parents,fusion,seed).cuda();parent_arm=None
            if mode=='gradual':
                parent_arm=next(r['name'] for r in results if r['mode']=='fusion')
                parent_state=torch.load(out/parent_arm/'checkpoint_best.pt',map_location='cpu',weights_only=False)
                model.load_state_dict(parent_state['model']);del parent_state
            model.configure(mode);initial={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
            contract=dict(manifest,name=name,mode=mode,seed=seed,fusion=fusion,parent_arm=parent_arm,repeat_of=repeat['name'] if repeat else None,
                parameters=sum(p.numel() for p in model.parameters()),decoder_parameters=sum(p.numel() for p in model.heads.parameters()),
                hypothesis=('Exact assembled parent predictions','Activity continuation preserves frozen price','Separate updates preserve both families',
                    'Identity residual adds useful cross-family information','Low-LR unfreezing improves frozen fusion','Second seed checks strongest retained continuation')[index])
            rid=json.loads((arm/'wandb.json').read_text())['id'] if (arm/'wandb.json').exists() else wandb.util.generate_id()
            write_json(arm/'wandb.json',dict(id=rid));w=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v26-preserve',id=rid,name=name,dir=str(arm),resume='allow',config=contract)
            graphs={};opt=None;step=0;seconds=0.;curve=[];g=generators(seed);peaks=None;status='failed';best_step=0;best_gate=None;batch=256;unfrozen=False
            def save():
                state=dict(model=model.state_dict(),parents=parents,fusion=fusion,seed=seed,mode=mode,optimizer=opt.state_dict() if opt else None,
                    step=step,seconds=seconds,rng=[v.get_state() for v in g],torch_rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),
                    peaks=peaks,curve=curve,elapsed=time.time()-begin+elapsed_before,contract=contract,batch=batch,unfrozen=unfrozen,best_step=best_step,best_gate=best_gate)
                temp=arm/'checkpoint.tmp';torch.save(state,temp);os.replace(temp,checkpoint)
            def validation(label,data):
                def vp(done,total,elapsed):update('Validation '+label,done=done,total=total,speed=done/max(elapsed,1e-6),eta=(total-done)*elapsed/max(done,1))
                return evaluate(model,data[:4],vp)
            try:
                if saved:
                    model.load_state_dict(saved['model']);unfrozen=saved['unfrozen'];model.configure(mode,unfrozen);peaks=saved['peaks']
                    batch=saved['batch'];opt=optimizer(model,peaks) if peaks else None
                    if opt:opt.load_state_dict(saved['optimizer'])
                    step=saved['step'];seconds=saved['seconds'];curve=saved['curve'];best_step=saved['best_step'];best_gate=saved['best_gate']
                    for gg,ss in zip(g,saved['rng']):gg.set_state(ss)
                    torch.set_rng_state(saved['torch_rng']);torch.cuda.set_rng_state_all(saved['cuda_rng']);del saved
                elif mode=='assembly':
                    records=[]
                    for visible in (16,56,64):
                        admit();pos=positions(32,'cuda',visible,generators(991)[1]);records.append(dict(visible=visible,**parity(model,parents,train[0][:32],train[1][:32],pos)))
                    write_json(arm/'native-parity.json',records)
                else:
                    # Full pipeline profile; all tensors already reside in the GPU cache.
                    profiles=[]
                    default=dict(price=1e-6,activity=1e-6,fusion=1e-6)
                    for bs in (128,256,512):
                        admit();update('Profiling',message=f'Complete update, batch {bs}')
                        model.load_state_dict(initial);opt=optimizer(model,default);gg=generators(991);b=sample(train,bs,3,gg)
                        replay=qualified(model,b);torch.cuda.reset_peak_memory_stats();torch.cuda.synchronize();tick=time.perf_counter()
                        for t in range(16):value,_,norm=replay(sample(train,bs,3,gg));opt.step()
                        torch.cuda.synchronize();profiles.append(dict(batch=bs,speed=16*bs/(time.perf_counter()-tick),peak_mib=torch.cuda.max_memory_allocated()/2**20,gradient_parity=True))
                        del replay,opt;opt=None;model.zero_grad(set_to_none=True);gc.collect();torch.cuda.empty_cache()
                    write_json(arm/'profile.json',profiles)
                    # Fixed batch preserves matched updates across arms; cache avoids loader waiting.
                    batch=256
                    if a.windows/next(q['speed'] for q in profiles if q['batch']==batch)>1800:raise TimeoutError('Exposure does not fit profiled budget')
                    if repeat:peaks=repeat['peaks']
                    elif mode=='gradual':peaks=dict(price=selected['independent']['price']*.1,activity=selected['activity']['activity']*.1,fusion=selected['fusion']['fusion'])
                    else:
                        pilots=[];probe=sample(train,128,3,generators(1701),True)
                        candidates=(1e-6,3e-6,1e-5) if mode=='activity' else (3e-7,1e-6,3e-6) if mode=='independent' else (1e-6,1e-5,1e-4)
                        families=['activity'] if mode=='activity' else ['price','activity']
                        for peak in candidates:
                            model.load_state_dict(initial);trial=dict(price=peak,activity=selected.get('activity',{}).get('activity',peak),fusion=peak)
                            if mode=='activity':trial['activity']=peak
                            opt=optimizer(model,trial);gg=generators(1702);replays={};before,base=terms(model,probe)
                            for t in range(64):
                                admit();b=sample(train,batch,t,gg);key=b[-2].shape[1]
                                if key not in replays:replays[key]=qualified(model,b)
                                for group in opt.param_groups:group['lr']=rate(t,64,group['peak'])
                                value,_,norm=replays[key](b)
                                if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('LR pilot')
                                opt.step()
                                if t%16==0:update('LR qualification',done=t,total=64,loss=float(value),lr=peak)
                            after,parts=terms(model,probe);ratios={k:parts[k]/max(base[k],1e-12) for k in families}
                            pilots.append(dict(peaks=trial,before=base,after=parts,score=max(ratios.values()),finite=math.isfinite(after)))
                            replays.clear();del opt;opt=None;model.zero_grad(set_to_none=True);gc.collect();torch.cuda.empty_cache()
                        eligible=[q for q in pilots if q['finite'] and q['score']<1.25]
                        write_json(arm/'lr-pilots.json',pilots)
                        if not eligible:raise ValueError('All learning rates destabilize pretrained families')
                        peaks=min(eligible,key=lambda q:q['score'])['peaks'];selected[mode]=peaks;manifest['selected_rates']=selected;write_json(out/'manifest.json',manifest)
                    model.load_state_dict(initial);opt=optimizer(model,peaks);tiny=tuple(v[:32] for v in train);pos=positions(32,'cuda',64);b=(*tiny,pos,pos)
                    replay=qualified(model,b);tiny_curve=[]
                    for t in range(96):
                        admit();value,_,norm=replay(b)
                        if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Tiny fit')
                        opt.step()
                        if t in (0,31,63,95):tiny_curve.append(dict(step=t,loss=float(value)))
                    write_json(arm/'tiny.json',tiny_curve)
                    if tiny_curve[-1]['loss']>tiny_curve[0]['loss']*1.05:raise ValueError('Tiny fit worsened pretrained model')
                    del replay,opt;model.zero_grad(set_to_none=True);model.load_state_dict(initial);opt=optimizer(model,peaks);g=generators(seed)
                del initial;contract.update(peaks=peaks,windows=0 if mode=='assembly' else a.windows,batch=batch);write_json(arm/'manifest.json',contract);w.config.update(contract,allow_val_change=True)
                total_steps=0 if mode=='assembly' else a.windows//batch;stride=256;status='complete'
                while step<=total_steps:
                    admit()
                    if (step%stride==0 or step==total_steps) and (not curve or curve[-1]['step']!=step):
                        detail,metrics=validation('development',dev);write_json(arm/f'evaluation-{step}.json',detail);write_json(arm/'latest-metrics.json',metrics)
                        gate=check(metrics,results[0]['metrics'] if results else metrics);curve.append(dict(step=step,metrics=metrics,gate=gate));write_json(arm/'curve.json',curve)
                        if gate['accepted'] and (best_gate is None or gate['score']<best_gate['score']):best_gate=gate;best_step=step;save();shutil.copyfile(checkpoint,arm/'checkpoint_best.pt')
                        w.log({'development/'+k:v for k,v in metrics.items()}|{'retention/accepted':int(gate['accepted']),'retention/score':gate['score']},step=step);save()
                        print(name,step,summary(metrics),gate['accepted'],flush=True)
                        if not gate['accepted']:status='quality_rejected';break
                    if step==total_steps:break
                    if mode=='gradual' and not unfrozen and step>=total_steps//4:
                        graphs.clear();model.zero_grad(set_to_none=True);unfrozen=True;model.configure(mode,True);gc.collect();torch.cuda.empty_cache()
                    tick=time.perf_counter();b=sample(train,batch,step,g);key=(b[-2].shape[1],unfrozen)
                    if key not in graphs:graphs[key]=qualified(model,b)
                    for group in opt.param_groups:
                        offset=total_steps//4 if mode=='gradual' and group['name']!='fusion' else 0
                        group['lr']=rate(max(0,step-offset),total_steps-offset,group['peak'])
                    value,parts,norm=graphs[key](b)
                    if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Main training')
                    opt.step();torch.cuda.synchronize();seconds+=time.perf_counter()-tick;step+=1
                    if step%32==0:
                        speed=step*batch/max(seconds,1e-6);update('Training',done=step*batch,total=a.windows,speed=speed,eta=(total_steps-step)*batch/speed,loss=float(value),lr=max(q['lr'] for q in opt.param_groups))
                        w.log({'train/loss':float(value),'train/windows_per_second':speed,**{'train/'+k:float(v) for k,v in zip(PARTS,parts)},**{'lr/'+q['name']:q['lr'] for q in opt.param_groups}},step=step)
                _,training_metrics=validation('training sample',tuple(t[:2048] for t in train));w.log({'train_evaluation/'+k:v for k,v in training_metrics.items()},step=step)
                metrics=curve[-1]['metrics'];result=dict(name=name,mode=mode,status=status,seed=seed,peaks=peaks,parameters=contract['parameters'],decoder_parameters=contract['decoder_parameters'],steps=step,seen=step*batch,
                    metrics=metrics,train_metrics=training_metrics,summary=summary(metrics),best_step=best_step,best_gate=best_gate,speed=step*batch/max(seconds,1e-6),seconds=time.time()-begin+elapsed_before,
                    checkpoint_sha=file_hash(checkpoint),best_checkpoint_sha=file_hash(arm/'checkpoint_best.pt'),wandb_url=w.url)
                write_json(arm/'result.json',result);results.append(result);write_json(out/'comparison.json',results)
            except BaseException:
                if curve:save()
                write_json(arm/'failure.json',dict(traceback=traceback.format_exc(),step=step,elapsed=time.time()-begin+elapsed_before));update('failed');raise
            finally:
                w.summary.update(dict(status=status,checkpoint_upload=False,best_step=best_step));w.finish(exit_code=0 if status!='failed' else 1)
                graphs.clear();del model,opt;gc.collect();torch.cuda.empty_cache()
        progress(stage='complete',done=len(results),total=6,message='Bounded campaign complete; retained checkpoints passed both-family gates.')


if __name__=='__main__':main()
