import copy
import torch
from torch import nn
from ..phase1_v24.model import restore as restore_price
from ..phase1_v25.model import restore as restore_activity
from ..phase0_v2.model import AttentionBlock
from ..phase1_v11.transforms import decode,PRICES,ACTIVITY
from ..phase1_v12.model import gather


class Fusion(nn.Module):
    """A zero-output residual starts at the exact assembled representation."""
    def __init__(self,seed):
        super().__init__()
        with torch.random.fork_rng():
            torch.manual_seed(seed);self.block=AttentionBlock(64,4);self.output=nn.Linear(64,64)
            nn.init.zeros_(self.output.weight);nn.init.zeros_(self.output.bias)
    def forward(self,z):
        with torch.autocast(z.device.type,dtype=torch.bfloat16,enabled=z.is_cuda):h=self.block(z)
        return z+self.output(h.float())


class Hybrid(nn.Module):
    def __init__(self,parents,fusion=False,seed=559):
        super().__init__();self.parents=parents;self.has_fusion=fusion;self.seed=seed
        self.price=restore_price(parents['price']);self.activity=restore_activity(parents['activity'])
        if self.activity.kind!='activity':raise ValueError('Dedicated activity parent required')
        torch.testing.assert_close(self.price.mean,self.activity.mean,rtol=0,atol=0)
        torch.testing.assert_close(self.price.scale,self.activity.scale,rtol=0,atol=0)
        self.heads=nn.ModuleDict(dict(price=self.price.decoder,activity=self.activity.heads['activity']))
        del self.price.decoder;del self.activity.heads
        for k in ('mean','scale','loss_factor','price_indices','activity_indices','range_edges'):
            self.register_buffer(k,getattr(self.activity,k).detach().clone())
        self.active=tuple(range(18));self.kind='hybrid';self.mode='assembly';self.unfrozen=False
        if fusion:self.fusion=Fusion(seed)

    def _encode(self,x,v,pos):
        pp,ap=pos if isinstance(pos,tuple) else (pos,pos)
        latent,meta=self.price.features(gather(x,pp),gather(v,pp))
        pz=self.price.temporal(latent,meta,pp);az=self.activity.encode(x,v,ap)
        z=torch.cat((pz,az),-1)
        if self.has_fusion:z=self.fusion(z)
        return z,latent,pp

    def encode(self,x,v,pos):return self._encode(x,v,pos)[0]

    def decode(self,z):
        # Preserve the parent's18-row GEMM, then replace ONLY activity rows.
        # Six old activity rows are unused; keeping them avoids numerical kernel
        # changes in the proven price head. Total stored heads:792parameters.
        n=self.heads['price'](z[...,:32].float().contiguous())
        a=self.heads['activity'](z[...,32:].float().contiguous())
        n=n.index_copy(-1,self.activity_indices,a);code=n*self.scale+self.mean
        c=torch.stack([code[...,j].clamp_min(0) if j%6 in (2,3,4,5) else code[...,j] for j in range(18)],-1)
        return dict(normalized=n,code=code,logs=decode(c,code.new_zeros(len(code),9),constrain=False))

    def forward(self,x,v,pos):
        z,latent,pp=self._encode(x,v,pos);out=self.decode(z)
        out['price_latent']=latent;out['price_positions']=pp
        return out,z

    def configure(self,mode,unfrozen=False):
        self.mode=mode;self.unfrozen=unfrozen;self.requires_grad_(False)
        if mode in ('activity','independent') or mode=='gradual' and unfrozen:
            self.activity.requires_grad_(True);self.heads['activity'].requires_grad_(True)
        if mode=='independent' or mode=='gradual' and unfrozen:
            self.price.requires_grad_(True);self.heads['price'].requires_grad_(True)
        if mode in ('fusion','gradual'):self.fusion.requires_grad_(True)

    def groups(self):
        groups=[]
        if any(p.requires_grad for p in self.price.parameters()):
            groups.append(('price',list(self.price.parameters())+list(self.heads['price'].parameters())))
        if any(p.requires_grad for p in self.activity.parameters()):
            groups.append(('activity',list(self.activity.parameters())+list(self.heads['activity'].parameters())))
        if self.has_fusion and any(p.requires_grad for p in self.fusion.parameters()):groups.append(('fusion',list(self.fusion.parameters())))
        return groups


def restore(saved):
    model=Hybrid(saved['parents'],saved['fusion'],saved['seed']);model.load_state_dict(saved['model']);return model


@torch.no_grad()
def parity(model,parents,x,v,pos):
    p=restore_price(parents['price']).to(x.device).eval();a=restore_activity(parents['activity']).to(x.device).eval()
    model.eval();out,z=model(x,v,pos);expected_p=p(x,v,pos)[2];expected_a=a(x,v,pos)[0]
    records={}
    for key in ('normalized','code','logs'):
        for family,expected,indices in [('price',expected_p,PRICES),('activity',expected_a,ACTIVITY)]:
            got=out[key][...,list(indices)];target=expected[key][...,list(indices)]
            torch.testing.assert_close(got,target,rtol=0,atol=0)
            records[family+'/'+key+'_max_error']=float((got-target).abs().max())
    torch.testing.assert_close(model.decode(z)['logs'],out['logs'],rtol=0,atol=0)
    return records
