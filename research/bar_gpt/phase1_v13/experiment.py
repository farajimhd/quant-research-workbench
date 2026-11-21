"""Four matched bounded arms; no mutation or resume of the stopped full run."""
import os
os.environ.update(PYTHONDONTWRITEBYTECODE='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
import argparse,copy,gc,json,time
from pathlib import Path
import numpy as np
import torch
import wandb
from .model import Autoencoder
from ..phase1_v12.model import Autoencoder as Original,Config,positions,objective
from ..phase1_v12.data import load_teacher,prepare
from ..phase1_v10.step import stable_clip
from ..phase1_v7.full_data import prepare_raw
from ..phase1_v11.data import verify
from ..phase2_v2.train import load_checkpoint
from ..v5.shards.phase1_contract import write_json,file_hash
from research.mlops.env import discover_env_files,load_env_files


@torch.no_grad()
def cache(path,teacher):
    rows=np.load(path,mmap_mode='r');parts=[]
    for i in range(0,len(rows),512):
        b=prepare_raw(rows[i:i+512],True).to('cuda')
        parts.append((*prepare(teacher,b),b.frames))
    return tuple(torch.cat([p[j] for p in parts]) for j in range(6))


@torch.no_grad()
def evaluate(m,teacher,data):
    z,meta,valid,x,v,frames=data;g=torch.Generator(device='cuda').manual_seed(917)
    result={};m.eval()
    for mode in ('masked','all_visible'):
        loss_sum=0.;count=0;mae=torch.zeros(2,device='cuda',dtype=torch.float64)
        for i in range(0,len(z),512):
            zz=z[i:i+512];pos=positions(len(zz),'cuda',16 if mode=='masked' else 64,g)
            pred=m(zz,meta[i:i+512],pos)
            known=torch.zeros_like(valid[i:i+512]).scatter(1,pos,True)
            mask=valid[i:i+512]&(~known if mode=='masked' else True)
            element=torch.nn.functional.smooth_l1_loss(pred,(zz-m.encoder.mean)/m.encoder.scale,reduction='none').mean(-1)
            loss_sum+=float(torch.where(mask,element,0).sum());count+=int(mask.sum())
            logs=teacher.decode(m.denormalize(pred))['logs'][...,3].double()
            target=x[i:i+512,:,3].double().exp();raw=logs.exp()
            if not torch.isfinite(raw).all():raise FloatingPointError('Nonfinite raw predictions')
            rawmask=mask&v[i:i+512,:,3]&(frames[i:i+512,None]==1000000)
            mae+=torch.stack((torch.where(rawmask,(raw-target).abs(),0).sum(),rawmask.sum()))
        result[mode+'/latent_loss']=loss_sum/count
        result[mode+'/1s_close_mae']=float(mae[0]/mae[1].clamp_min(1))
    m.train();return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--revision',required=True)
    p.add_argument('--steps',type=int,default=3000);p.add_argument('--batch',type=int,default=1024);a=p.parse_args()
    if a.output.exists():raise ValueError('Use a new immutable experiment directory')
    a.output.mkdir(parents=True);torch.set_num_threads(4);torch.set_float32_matmul_precision('highest')
    root=Path('D:/TradingML/runtimes/bar_gpt/phase1-v12-frozen16-mae-20261121')
    run=root/'full-frozen16-mae256-20ep'
    if json.loads((run/'progress.json').read_text())['state']!='stopped':raise ValueError('Full run must remain stopped')
    import psutil
    owner=json.loads((run/'process.json').read_text())
    if psutil.pid_exists(owner['pid']) and abs(psutil.Process(owner['pid']).create_time()-owner['created'])<.1:raise ValueError('Old owner live')
    cal=json.loads((root/'teacher/calibration.json').read_text());teacher,_=load_teacher(root/'teacher/teacher.pt',cal['split_hash'],'cuda')
    path=run/'checkpoint_best.pt';peek=torch.load(path,map_location='cpu',weights_only=False);state=load_checkpoint(path,peek['contract'])
    dataset=Path('D:/TradingML/runtimes/bar_gpt/phase1-v8-learned-mae-20261117/dataset');verified=verify(dataset)
    if verified['hash']!=cal['data_hash']:raise ValueError('Frozen subset changed')
    began=time.perf_counter();train=cache(dataset/'train.npy',teacher);dev=cache(dataset/'development.npy',teacher)
    contract=dict(source=a.revision,teacher_sha=cal['teacher']['checkpoint_sha256'],parent_sha=file_hash(path),data_hash=verified['hash'],split_hash=cal['split_hash'],train_windows=len(train[0]),dev_windows=len(dev[0]),steps=a.steps,batch=a.batch,lr=3e-5,seed=123,cache_seconds=time.perf_counter()-began,scope='Frozen subset ablation, not full population. GPU cached FP32 teacher targets; throughput excludes raw loader and teacher.')
    write_json(a.output/'manifest.json',contract)
    load_env_files(discover_env_files(Path(__file__).resolve().parents[3]),verbose=False)
    all_results=[]
    for initialization in ('best','fresh'):
        torch.manual_seed(17);initial=Original(Config(**state['contract']['config']),cal['mean'],cal['scale']).state_dict()
        for stable in (False,True):
            name=initialization+('-stable' if stable else '-original');out=a.output/name;out.mkdir()
            torch.manual_seed(123);m=(Autoencoder if stable else Original)(Config(**state['contract']['config']),cal['mean'],cal['scale']).cuda()
            m.load_state_dict(state['model'] if initialization=='best' else initial)
            opt=torch.optim.AdamW(m.parameters(),lr=3e-5,weight_decay=.01,fused=True)
            if initialization=='best':opt.load_state_dict(copy.deepcopy(state['optimizer']))
            for group in opt.param_groups:group['lr']=3e-5
            w=wandb.init(entity='mehdifaraji',project='bargpt-phase1-v13-attention-stability',name=a.output.name+'-'+name,dir=str(out),config=dict(contract,initialization=initialization,stable=stable),reinit=True)
            start=time.perf_counter();curve=[];training_seconds=0.;status='failed'
            try:
                measured=evaluate(m,teacher,dev);w.log({'development/'+k:v for k,v in measured.items()},step=0)
                curve.append(dict(step=0,metrics=measured));print(name,0,measured,flush=True)
                g=torch.Generator(device='cuda').manual_seed(555)
                for step in range(1,a.steps+1):
                    if time.perf_counter()-start>900:raise TimeoutError('15 minute per-arm deadline')
                    if (a.output/'STOP').exists():raise InterruptedError('Human STOP')
                    tick=time.perf_counter();ids=torch.randint(len(train[0]),(a.batch,),device='cuda',generator=g)
                    z,meta,valid=[x[ids] for x in train[:3]];pos=positions(len(z),'cuda',16,g)
                    opt.zero_grad(set_to_none=True);pred=m(z,meta,pos);loss,_=objective(m,pred,z,valid,pos)
                    loss.backward();norm=stable_clip(m.parameters())
                    if not bool(torch.isfinite(loss)&torch.isfinite(norm)):raise FloatingPointError('Nonfinite gradient')
                    opt.step();torch.cuda.synchronize();training_seconds+=time.perf_counter()-tick
                    if step%100==0:w.log({'train/loss':float(loss.detach()),'train/gradient_norm':float(norm),'train/cached_windows_per_second':step*a.batch/training_seconds},step=step)
                    if step%1000==0 or step==a.steps:
                        measured=evaluate(m,teacher,dev);w.log({'development/'+k:v for k,v in measured.items()},step=step)
                        curve.append(dict(step=step,metrics=measured));print(name,step,measured,flush=True)
                        write_json(out/'progress.json',dict(step=step,curve=curve))
                torch.save(dict(model=m.state_dict(),optimizer=opt.state_dict(),contract=dict(contract,stable=stable,initialization=initialization),step=a.steps),out/'checkpoint.pt')
                result=dict(name=name,curve=curve,seconds=time.perf_counter()-start,cached_windows_per_second=a.steps*a.batch/training_seconds,checkpoint_sha=file_hash(out/'checkpoint.pt'),wandb_url=w.url,parameters=sum(p.numel() for p in m.parameters()))
                write_json(out/'result.json',result);all_results.append(result);write_json(a.output/'comparison.json',all_results);status='complete'
            finally:
                w.summary['status']=status;w.finish(exit_code=0 if status=='complete' else 1)
                del m,opt;gc.collect();torch.cuda.empty_cache()


if __name__=='__main__':main()
