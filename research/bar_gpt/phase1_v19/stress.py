"""Read-only known-failure stress checks; never a training data source."""
import torch


@torch.no_grad()
def load_cases(path):
    cases=torch.load(path,map_location='cpu',weights_only=False)
    return {k:torch.stack([c[k] for c in cases]).cuda() for k in ('latent','metadata','positions','raw','valid')}


@torch.no_grad()
def evaluate(model,teacher,cases):
    z,meta,pos,raw,valid=(cases[k] for k in ('latent','metadata','positions','raw','valid'))
    hidden=~torch.zeros(z.shape[:2],device=z.device,dtype=torch.bool).scatter(1,pos,True)
    result={}
    for name,p,mask in [('masked',pos,valid[...,10]&hidden),('all_visible',torch.arange(64,device=z.device).expand(len(z),-1),valid[...,10])]:
        logs=teacher.decode(model.denormalize(model(z,meta,p)))['logs'].double()
        estimate=logs[...,10].expm1();error=torch.where(mask,(estimate-raw[...,10]).abs(),0)
        result.update({f'stress/{name}/bid_size_mae':float(error.sum()/mask.sum().clamp_min(1)),
                       f'stress/{name}/bid_size_max_error':float(error.max()),
                       f'stress/{name}/bid_size_max_prediction':float(torch.where(mask,estimate,0).max())})
    return result
