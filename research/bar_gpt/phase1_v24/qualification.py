"""Training-only LR pilots; never consult development to select the LR."""
import gc,time
import torch
from .model import optimizer
from ..phase1_v21.objectives import loss
from ..phase1_v21.step import backward
from ..phase1_v12.model import positions


def calibrate_lr(model,initial,train,scale,admission,progress):
    probe_ids=torch.linspace(0,len(train[0])-1,128,device='cuda').long()
    probe=[x[probe_ids] for x in train]
    @torch.no_grad()
    def measure():
        x,v,raw,f,z=probe;pos=positions(len(x),'cuda',64)
        value,parts=loss(model,x,v,raw,f,pos,z,scale)
        out=model(x,v,pos)[2]['logs'];idx=[0,1,2,3,6,7,8,9,12,13,14,15]
        error=(out[...,idx]-x[...,idx]).abs()[v[...,idx]].mean()
        return dict(loss=float(value),price_log_mae=float(error),range_loss=float(parts['range']))
    records=[]
    for multiplier in (.3,1.,3.):
        admission();model.load_state_dict(initial);opt=optimizer(model,multiplier)
        g=torch.Generator(device='cuda').manual_seed(770);before=measure();curve=[];started=time.time()
        for step in range(64):
            if step%16==0:admission();progress(multiplier=multiplier,pilot_step=step)
            ids=torch.randint(len(train[0]),(128,),device='cuda',generator=g)
            x,v,raw,f,z=[q[ids] for q in train];pos=positions(128,'cuda',64 if step%4==3 else 16,g)
            opt.zero_grad(set_to_none=True);value,_,norm=backward(model,(x,v,raw,f,pos,z),scale)
            if not bool(torch.isfinite(value)&torch.isfinite(norm)):raise FloatingPointError('LR pilot nonfinite')
            opt.step()
            if step%16==15:curve.append(dict(step=step+1,**measure()))
        after=curve[-1];eligible=after['loss']<before['loss']*1.1 and after['price_log_mae']<before['price_log_mae']*1.25
        records.append(dict(multiplier=multiplier,before=before,curve=curve,eligible=eligible,seconds=time.time()-started))
        del opt;model.zero_grad(set_to_none=True);gc.collect();torch.cuda.empty_cache()
    eligible=[r for r in records if r['eligible']]
    if not eligible:raise ValueError('No stable training-only LR pilot; diagnose before full arm')
    best=min(eligible,key=lambda r:r['curve'][-1]['loss'])
    model.load_state_dict(initial)
    return best['multiplier'],records
