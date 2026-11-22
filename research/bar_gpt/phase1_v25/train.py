"""Eight bounded experiments, one GPU owner, immutable data, no W&B weights."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,gc,json,math,shutil,time,traceback
from pathlib import Path
import torch
import wandb
from .model import Model
from .data import load
from .objectives import loss
from .metrics import evaluate,summary
from .step import backward,qualified,rate
from ..phase1_v12.model import positions
from ..phase1_v24.runtime import preflight
from ..phase2_v2.train import lease
from ..v5.shards.phase1_contract import write_json,file_hash
from research.mlops.env import discover_env_files,load_env_files

SPECS=[('shared','legacy'),('shared','light'),('shared','none'),('price','light'),('activity','light'),('separate','light'),('fusion','light')]
PARTS=('price','activity','body','range','range_bins','candle_direction','activity_direction')


def count(mask,step):return 64 if mask=='none' or (mask=='legacy' and (step+1)%4==0) else 16 if mask=='legacy' else 56


def optimizer(model,rates):
    groups=model.parameter_groups()
    return torch.optim.AdamW([dict(params=g,lr=rates[min(i,len(rates)-1)]) for i,g in enumerate(groups)],weight_decay=0.,fused=True)


def sample(data,batch,visible,g):
    # Separate sample and mask RNGs preserve identical window draws across masks.
    ids=torch.randint(len(data[0]),(batch,),device='cuda',generator=g[0]);p=positions(batch,'cuda',visible,g[1])
    return (*[t[ids] for t in data],p)


def generators(seed):return [torch.Generator(device='cuda').manual_seed(seed+i*100003) for i in range(2)]


def ranking(metrics,reference):
    keys=[k for k in metrics if k.startswith('all_visible/raw_mae/') and reference.get(k,0)>0]
    ratios=[metrics[k]/reference[k] for k in keys]
    direction=[(100-v+1)/(100-reference[k]+1) for k,v in metrics.items() if k in reference and (k.startswith('all_visible/candle/') and k.endswith('/moving_direction_pct') or k.startswith('activity/all_visible/') and k.endswith('/balanced_direction_pct'))]
    geo=math.exp(sum(math.log(max(v,1e-12)) for v in ratios)/len(ratios))
    return dict(score=.5*geo+.5*sum(direction)/len(direction),raw_geomean_ratio=geo,worst_raw_ratio=max(ratios),worst_raw_groups=sorted([dict(metric=k,ratio=metrics[k]/reference[k]) for k in keys],key=lambda x:-x['ratio'])[:10])


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--windows',type=int,default=1048576);p.add_argument('--resume',action='store_true');a=p.parse_args()
    if not 262144<=a.windows<=1048576 or a.windows%262144:raise ValueError('Bounded matched exposure')
    a.device='cuda';a.source_revision=a.revision;preflight(a);out=a.output;started=time.time()
    torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    progress=lambda **kw:write_json(out/'progress.json',dict(updated=time.time(),**kw))
    with lease(out):
        progress(stage='preparing immutable GPU cache')
        train,dev,cal,provenance=load(Path('D:/TradingML/runtimes/bar_gpt'))
        manifest=dict(source=a.revision,started=started,**provenance,windows=a.windows,batch=256,experiments=8,checkpoint_upload=False,
            transform='frozen V24 buffers; price coordinates and fixed activity reference; no local median',
            initialization='fresh independent parameters; no pretrained weights or teacher anchor',
            loss='V21 family terms, family-normalized coordinate error; no anchor; source-valid masks',
            deadline_seconds=3600,campaign_deadline_seconds=28800)
        if (out/'manifest.json').exists():
            prior=json.loads((out/'manifest.json').read_text())
            for k in ('source','data_hash','supplement_hash','calibration_hash'):
                if prior[k]!=manifest[k]:raise ValueError('Scientific resume mismatch: '+k)
            manifest=prior;started=prior['started'];a.windows=prior['windows']
        else:write_json(out/'manifest.json',manifest)
        results=json.loads((out/'comparison.json').read_text()) if (out/'comparison.json').exists() else []
        rates_by_kind=manifest.get('selected_rates',{})
        for index in range(8):
            if time.time()-started>28000:raise TimeoutError('Campaign deadline')
            if index==7:
                eligible=[r for r in results if r['kind'] in ('shared','separate','fusion')]
                best=min(eligible,key=lambda r:r['ranking']['score']);kind,mask=best['kind'],best['mask'];seed=1559
            else:kind,mask=SPECS[index];seed=559
            name=f'e{index+1:02d}-{kind}-{mask}-s{seed}';arm=out/name
            if (arm/'result.json').exists():continue
            arm.mkdir(exist_ok=True);begin=time.time();saved=None;checkpoint=arm/'checkpoint_last.pt'
            if a.resume and checkpoint.exists():saved=torch.load(checkpoint,map_location='cpu',weights_only=False)
            prior_elapsed=saved['elapsed'] if saved else 0.;deadline=begin+3500-prior_elapsed
            def admit(reserve=240):
                if (out/'STOP').exists() or (out.parent/'STOP').exists():raise InterruptedError('Human STOP preserved')
                if time.time()>deadline-reserve:raise TimeoutError('Evaluation/checkpoint reserve reached')
            def update(stage,**kw):progress(name=name,stage=stage,elapsed=time.time()-begin+prior_elapsed,**kw)
            model=Model(cal,kind,seed).cuda();initial={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
            contract=dict(manifest,name=name,kind=kind,mask=mask,seed=seed,parameters=sum(p.numel() for p in model.parameters()),decoder_parameters=sum(p.numel() for p in model.heads.parameters()),
                representation='64x64' if kind in ('shared','separate','fusion') else '64x32',hypothesis=[
                    'Fresh shared baseline under legacy mask schedule','Eight hidden bars improve reconstruction and direction','No masking bounds reconstruction difficulty',
                    'Price family can learn independently','Activity family can learn independently','Independent joint branches reproduce standalone behavior',
                    'Encoder fusion helps retain price/activity relationships','Second seed tests provisional winner'][index])
            rid=json.loads((arm/'wandb.json').read_text())['id'] if (arm/'wandb.json').exists() else wandb.util.generate_id()
            write_json(arm/'wandb.json',dict(id=rid));w=wandb.init(entity='mehdifaraji',project='bargpt-phase01-v25-family-mask',id=rid,name=name,dir=str(arm),resume='allow',config=contract,settings=wandb.Settings(x_disable_stats=False))
            graphs={};opt=None;step=0;seconds=0.;curve=[];g=generators(seed);peaks=None;status='failed';main_started=False
            def save():
                state=dict(model=model.state_dict(),optimizer=opt.state_dict(),calibration=cal,kind=kind,seed=seed,mask=mask,step=step,seconds=seconds,
                    rng=[v.get_state() for v in g],torch_rng=torch.get_rng_state(),cuda_rng=torch.cuda.get_rng_state_all(),peaks=peaks,curve=curve,elapsed=time.time()-begin+prior_elapsed,contract=contract)
                temp=arm/'checkpoint.tmp';torch.save(state,temp);os.replace(temp,checkpoint)
            try:
                if saved:
                    model.load_state_dict(saved['model']);peaks=saved['peaks'];opt=optimizer(model,peaks);opt.load_state_dict(saved['optimizer'])
                    step=saved['step'];seconds=saved['seconds'];curve=saved['curve']
                    for gg,ss in zip(g,saved['rng']):gg.set_state(ss)
                    torch.set_rng_state(saved['torch_rng']);torch.cuda.set_rng_state_all(saved['cuda_rng']);del saved
                else:
                    # Full-pipeline graph qualification, including preparation copies and optimizer.
                    profiles=[]
                    for batch in (128,256,512):
                        admit();update('profiling',message=f'Batch {batch}, all 64 visible')
                        model.load_state_dict(initial);opt=optimizer(model,[3e-4]*len(model.parameter_groups()));b=sample(train,batch,64,generators(991))
                        replay=qualified(model,b);torch.cuda.reset_peak_memory_stats();torch.cuda.synchronize();tick=time.perf_counter()
                        for _ in range(12):
                            actual=sample(train,batch,64,generators(991));value,_,norm=replay(actual);opt.step()
                        torch.cuda.synchronize();profiles.append(dict(batch=batch,speed=12*batch/(time.perf_counter()-tick),peak_mib=torch.cuda.max_memory_allocated()/2**20,gradient_parity=True))
                        del replay,opt;opt=None;model.zero_grad(set_to_none=True);gc.collect();torch.cuda.empty_cache()
                    write_json(arm/'profile.json',profiles)
                    # Keep common batch for equal updates and standalone/joint parity.
                    if index==0:
                        speed=next(v['speed'] for v in profiles if v['batch']==256)
                        a.windows=max(262144,min(a.windows,int(speed*1200)//262144*262144));manifest['windows']=a.windows;write_json(out/'manifest.json',manifest)
                    if a.windows/next(v['speed'] for v in profiles if v['batch']==256)>1800:raise TimeoutError('Matched exposure exceeds profile budget')
                    if kind=='shared' and 'shared' in rates_by_kind:peaks=rates_by_kind['shared']
                    elif kind=='separate':peaks=[rates_by_kind['price'][0],rates_by_kind['activity'][0]]
                    elif index==7:peaks=best['peaks']
                    else:
                        pilots=[]
                        probe=sample(train,128,56,generators(1701))
                        for peak in (1e-4,3e-4,1e-3):
                            model.load_state_dict(initial);opt=optimizer(model,[peak]);gg=generators(1702);replay=None;values=[]
                            with torch.no_grad():
                                before,_=model(probe[0],probe[1],probe[-1]);_,base_parts=loss(model,before,*probe[:3],probe[-1])
                            for t in range(96):
                                admit();b=sample(train,256,56,gg)
                                if replay is None:replay=qualified(model,b)
                                for group in opt.param_groups:group['lr']=rate(t,96,peak)
                                value,parts,norm=replay(b)
                                if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('LR pilot')
                                opt.step()
                                if t%16==0:update('LR qualification',done=t,total=96,loss=float(value),lr=peak)
                                if t in (0,95):values.append(float(value))
                            with torch.no_grad():
                                output,_=model(probe[0],probe[1],probe[-1]);val,terms=loss(model,output,*probe[:3],probe[-1])
                            pilots.append(dict(peak=peak,probe_loss=float(val),parts={k:float(v) for k,v in terms.items()},initial_parts={k:float(v) for k,v in base_parts.items()},training_endpoints=values,finite=bool(torch.isfinite(val))))
                            del replay,opt;opt=None;model.zero_grad(set_to_none=True);gc.collect();torch.cuda.empty_cache()
                        families=['price'] if kind=='price' else ['activity'] if kind=='activity' else ['price','activity']
                        eligible=[p for p in pilots if p['finite'] and p['training_endpoints'][-1]<p['training_endpoints'][0] and all(p['parts'][k]<1.25*max(p['initial_parts'][k],1e-12) for k in families)]
                        if not eligible:raise ValueError('No LR learns')
                        # Minimize worst family ratio to its best pilot, not total loss alone.
                        minima={k:min(p['parts'][k] for p in eligible) for k in families}
                        for p in eligible:p['balanced_score']=max(p['parts'][k]/max(minima[k],1e-12) for k in families)
                        peaks=[min(eligible,key=lambda p:p['balanced_score'])['peak']];write_json(arm/'lr-pilots.json',pilots)
                        rates_by_kind[kind]=peaks;manifest['selected_rates']=rates_by_kind;write_json(out/'manifest.json',manifest)
                    # Easy 32-window learning gate; reset all qualification updates afterward.
                    model.load_state_dict(initial);opt=optimizer(model,peaks);tiny=tuple(v[:32] for v in train);pos=positions(32,'cuda',64);b=(*tiny,pos);replay=qualified(model,b);tiny_curve=[]
                    for t in range(160):
                        admit();value,_,norm=replay(b)
                        if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Tiny fit')
                        opt.step()
                        if t in (0,39,79,159):tiny_curve.append(dict(step=t,loss=float(value)))
                    write_json(arm/'tiny.json',tiny_curve)
                    if tiny_curve[-1]['loss']>=.9*tiny_curve[0]['loss']:raise ValueError('Tiny fit did not improve 10%')
                    del replay,opt;model.zero_grad(set_to_none=True);model.load_state_dict(initial);opt=optimizer(model,peaks);g=generators(seed)
                del initial;contract.update(peaks=peaks,windows=a.windows,batch=256);write_json(arm/'manifest.json',contract);w.config.update(contract,allow_val_change=True)
                total_steps=a.windows//256;stride=total_steps//4;main_started=True
                def validation(label,items):
                    def vp(done,total,elapsed):update('validation '+label,done=done,total=total,speed=done/max(elapsed,1e-6),eta=(total-done)*elapsed/max(done,1))
                    return evaluate(model,items,vp)
                while step<=total_steps:
                    admit()
                    if step%stride==0 and (not curve or curve[-1]['step']!=step):
                        detail,metrics=validation('development',dev);write_json(arm/f'evaluation-{step}.json',detail);write_json(arm/'latest-metrics.json',metrics)
                        ref=results[0]['metrics'] if results else metrics;rank=ranking(metrics,ref) if kind not in ('price','activity') else None
                        curve.append(dict(step=step,metrics=metrics,ranking=rank));write_json(arm/'curve.json',curve)
                        w.log({'development/'+k:v for k,v in metrics.items()},step=step);save()
                        scores=[q['ranking']['score'] for q in curve if q['ranking']]
                        if rank and rank['score']<=min(scores):shutil.copyfile(checkpoint,arm/'checkpoint_best.pt')
                        print(name,step,summary(metrics),flush=True)
                    if step==total_steps:break
                    tick=time.perf_counter();visible=count(mask,step);b=sample(train,256,visible,g)
                    if visible not in graphs:graphs[visible]=qualified(model,b)
                    for group,peak in zip(opt.param_groups,peaks):group['lr']=rate(step,total_steps,peak)
                    value,parts,norm=graphs[visible](b)
                    if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('Main training')
                    opt.step();torch.cuda.synchronize();seconds+=time.perf_counter()-tick;step+=1
                    if step%32==0:
                        speed=step*256/max(seconds,1e-6);update('training',done=step*256,total=a.windows,speed=speed,eta=(total_steps-step)*256/speed,loss=float(value),lr=opt.param_groups[0]['lr'])
                        w.log({'train/loss':float(value),'train/learning_rate':opt.param_groups[0]['lr'],'train/windows_per_second':speed,**{'train/'+k:float(v) for k,v in zip(PARTS,parts)}},step=step)
                _,training_metrics=validation('training sample',tuple(t[:2048] for t in train));w.log({'train_evaluation/'+k:v for k,v in training_metrics.items()},step=step)
                metrics=curve[-1]['metrics'];rank=ranking(metrics,results[0]['metrics'] if results else metrics) if kind not in ('price','activity') else None
                result=dict(name=name,kind=kind,mask=mask,seed=seed,peaks=peaks,parameters=contract['parameters'],decoder_parameters=contract['decoder_parameters'],steps=step,seen=step*256,metrics=metrics,train_metrics=training_metrics,summary=summary(metrics),ranking=rank,speed=step*256/max(seconds,1e-6),seconds=time.time()-begin+prior_elapsed,checkpoint_sha=file_hash(checkpoint),wandb_url=w.url)
                write_json(arm/'result.json',result);results.append(result);write_json(out/'comparison.json',results);status='complete'
            except BaseException:
                if opt is not None and peaks is not None and main_started:save()
                write_json(arm/'failure.json',dict(traceback=traceback.format_exc(),step=step,elapsed=time.time()-begin+prior_elapsed));update('failed');raise
            finally:
                w.summary.update(dict(status=status,checkpoint_upload=False));w.finish(exit_code=0 if status=='complete' else 1)
                graphs.clear();del model,opt;gc.collect();torch.cuda.empty_cache()
        progress(stage='complete',done=8,total=8,message='Eight experiments complete; review family errors and worst groups before promotion.')

if __name__=='__main__':main()
