"""Fresh feature/temporal attention; the decoder sees only exported slots."""
import torch
from torch import nn
from torch.nn import functional as F
from ..phase0_v2.model import AttentionBlock
from ..phase0_v3.model import relative_input
from ..phase1_v11.transforms import coordinates,decode,PRICES,ACTIVITY
from ..phase1_v12.model import gather


class Branch(nn.Module):
    def __init__(self,channels,width,embedding):
        super().__init__();self.channels=tuple(channels);self.width=width;self.embedding=embedding
        self.register_buffer('indices',torch.tensor(channels));n=len(channels);self.ident=nn.Parameter(torch.randn(n,32)*.02)
        self.value=nn.Parameter(torch.randn(n,32)*.1)
        self.meta=nn.Linear(n+18,32)
        self.feature=AttentionBlock(32,4);self.bar=nn.Linear(n*32,32)
        self.stem=nn.Linear(32+n+18,width)
        self.position=nn.Parameter(torch.randn(1,64,width)*.02)
        heads=12 if width==384 else 8
        self.temporal=nn.Sequential(*[AttentionBlock(width,heads) for _ in range(4)])
        self.norm=nn.LayerNorm(width);self.query=nn.Linear(width,width)
        self.kv=nn.Linear(width,width*2);self.attended=nn.Linear(width,width)
        self.readout=nn.Linear(width,embedding);self.heads=heads

    def forward(self,scaled,valid,pos):
        x=gather(scaled,pos);v=gather(valid,pos)
        values=x.index_select(-1,self.indices);mask=v.index_select(-1,self.indices)
        meta=torch.cat((mask.float(),x[...,18:],v[...,18:].float()),-1)
        b,t,n=values.shape
        h=values[...,None]*self.value+self.ident+self.meta(meta)[...,None,:]
        h=self.feature(h.reshape(b*t,n,32)).reshape(b,t,n*32)
        z=self.bar(h);h=self.stem(torch.cat((z,meta),-1))+self.position[0,pos]
        h=self.norm(self.temporal(h))
        aligned=h.mean(1,keepdim=True).expand(-1,64,-1).scatter(1,pos[...,None].expand_as(h),h)
        q=self.query(self.position.expand(b,-1,-1)).reshape(b,64,self.heads,-1).transpose(1,2)
        k,v=self.kv(h).reshape(b,t,2,self.heads,-1).permute(2,0,3,1,4).unbind(0)
        context=F.scaled_dot_product_attention(q,k,v).transpose(1,2).reshape(b,64,self.width)
        return self.readout((aligned+self.attended(context)).float())


class Model(nn.Module):
    def __init__(self,calibration,kind='shared',seed=559):
        super().__init__();self.kind=kind;self.seed=seed;self.calibration=calibration
        for key in ('mean','scale','input_mean','input_scale','reference','activity_mean','activity_scale'):
            self.register_buffer(key,torch.tensor(calibration[key],dtype=torch.float32))
        self.register_buffer('loss_factor',torch.tensor([1000. if j in PRICES else 1. for j in range(18)]))
        self.register_buffer('price_indices',torch.tensor(PRICES));self.register_buffer('activity_indices',torch.tensor(ACTIVITY))
        self.register_buffer('range_edges',torch.tensor([.0005,.001,.0025,.005],dtype=torch.float64).log1p())
        self.branches=nn.ModuleDict();self.heads=nn.ModuleDict()
        definitions=[('shared',tuple(range(18)),384,64)] if kind=='shared' else [
            (name,channels,272,32) for name,channels in [('price',PRICES),('activity',ACTIVITY)] if kind in (name,'separate','fusion')]
        for name,channels,width,embedding in definitions:
            # Family initialization is identical alone and jointly, without RNG coupling.
            with torch.random.fork_rng():
                torch.manual_seed(seed+{'shared':0,'price':1,'activity':2}[name])
                self.branches[name]=Branch(channels,width,embedding)
                self.heads[name]=nn.Linear(embedding,len(channels))
                nn.init.normal_(self.heads[name].weight,std=.001);nn.init.zeros_(self.heads[name].bias)
        if kind=='fusion':
            with torch.random.fork_rng():
                torch.manual_seed(seed+3);self.fusion=AttentionBlock(64,4)
        self.active=tuple(range(18)) if kind in ('shared','separate','fusion') else PRICES if kind=='price' else ACTIVITY

    def inputs(self,x,v):
        code,cm=coordinates(x[...,:18],v[...,:18]);features=torch.cat((code,x[...,18:]),-1)
        mask=torch.cat((cm,v[...,18:]),-1);scaled=(features-self.input_mean)/self.input_scale
        act=relative_input(code.index_select(-1,self.activity_indices).double(),self.reference.double()).float()
        scaled=scaled.clone();scaled.index_copy_(-1,self.activity_indices,(act-self.activity_mean)/self.activity_scale)
        return torch.where(mask,scaled,0)

    def encode(self,x,v,pos):
        scaled=self.inputs(x,v)
        with torch.autocast(x.device.type,dtype=torch.bfloat16,enabled=x.is_cuda):
            embeddings={k:b(scaled,v,pos) for k,b in self.branches.items()}
            if self.kind=='fusion':
                fused=self.fusion(torch.cat((embeddings['price'],embeddings['activity']),-1))
                embeddings=dict(price=fused[...,:32],activity=fused[...,32:])
        return torch.cat(list(embeddings.values()),-1).float()

    def decode(self,z):
        # No target, metadata, positional tensor, or raw input enters this method.
        normalized=z.new_zeros((*z.shape[:-1],18));offset=0
        for name,head in self.heads.items():
            n=head.in_features;normalized.index_copy_(-1,self.branches[name].indices,head(z[...,offset:offset+n].float()));offset+=n
        code=normalized*self.scale+self.mean
        constrained=torch.stack([code[...,j].clamp_min(0) if j%6 in (2,3,4,5) else code[...,j] for j in range(18)],-1)
        return dict(normalized=normalized,code=code,logs=decode(constrained,code.new_zeros(len(code),9),constrain=False))

    def forward(self,x,v,pos):
        z=self.encode(x,v,pos);return self.decode(z),z

    def parameter_groups(self):
        if self.kind=='shared':return [list(self.parameters())]
        if self.kind=='fusion':return [list(self.parameters())]
        return [list(self.branches[k].parameters())+list(self.heads[k].parameters()) for k in self.branches]


def restore(saved):
    model=Model(saved['calibration'],saved['kind'],saved['seed']);model.load_state_dict(saved['model']);return model
