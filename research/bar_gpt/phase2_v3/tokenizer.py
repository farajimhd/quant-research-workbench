"""Exact V11 preprocessing; causal context cache, no future tokenizer target."""
import json
from pathlib import Path
import numpy as np
import torch
from ..phase1_v11.model import Encoder
from ..phase1_v11.config import Config
from ..phase1_v11.transforms import PRICES,ACTIVITY
from ..phase2_v2.train import load_checkpoint
from ..phase2_v2.tokenizer import SessionTokens as BaseSession
from ..v5.shards.contract import file_hash,digest


def load_teacher(path,sha256,calibration,device='cpu'):
    path=Path(path)
    if file_hash(path)!=sha256:raise ValueError('Phase1 checkpoint SHA differs')
    saved=torch.load(path,map_location='cpu',weights_only=False);saved=load_checkpoint(path,saved['contract'])
    c=saved['contract']
    if saved['epoch']!=25 or saved['phase']!='train' or saved['batch_cursor']!=0 or saved['history'][-1]['epoch']!=25:
        raise ValueError('Require completed phase1 epoch25 last checkpoint')
    if file_hash(calibration)!=c['calibration_sha256']:raise ValueError('Phase1 calibration differs')
    config=Config(**c['config'])
    if config.embedding!=256 or config.readout!='split_scale':raise ValueError('Require approved256 split-scale encoder')
    teacher=Encoder(config,json.loads(Path(calibration).read_text())['parameters'])
    teacher.load_state_dict({k.removeprefix('encoder.'):v for k,v in saved['model'].items() if k.startswith('encoder.')},strict=True)
    teacher.requires_grad_(False).eval().to(device)
    if file_hash(path)!=sha256:raise ValueError('Phase1 checkpoint changed during load')
    return teacher,dict(sha256=sha256,epoch=25,step=saved['step'],config=c['config'],calibration_sha256=c['calibration_sha256']),(
        saved['model']['reference_head.weight'],saved['model']['reference_head.bias'])


def raw_logs(raw,valid):
    safe=torch.where(valid,raw,1).double();cols=[]
    for j in range(18):cols.append(safe[...,j].log() if j in PRICES else safe[...,j].log1p())
    return torch.where(valid,torch.stack(cols,-1),0).float()


class SessionTokens(BaseSession):
    requires_target_embeddings=False

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        counts=[len(p[0]) for p in self.pools]
        # Shared base admission conservatively reserves the larger legacy bank.
        # Only these256-dimensional V11 vectors are populated or consumed.
        self.token_bank=torch.empty((sum(counts),256),device=self.device)
        self.bank=list(self.token_bank.split(counts))
        self.identity=digest(dict(base=self.identity,version='phase2-v3-v11-raw-log-full64'))
        self.reference_bank.zero_()
        self.positions=torch.arange(64,device=self.device)

    @torch.no_grad()
    def ensure(self,frame,endpoints):
        ends=np.unique(np.asarray(endpoints,dtype=np.int64))
        if len(ends) and (ends[0]<63 or ends[-1]>=len(self.ready[frame])):raise ValueError('Context endpoint outside pool')
        values,valid,_=self.pools[frame]
        for tile in np.unique((ends-63)//self.token_batch):
            start=63+int(tile)*self.token_batch;selected=np.arange(start,min(start+self.token_batch,len(self.ready[frame])))
            if self.ready[frame][selected].all():continue
            ids=torch.as_tensor(selected.copy(),device=self.device);gather=ids[:,None]-self.positions.flip(0)
            raw=values[gather];mask=valid[gather].bool();logs=raw_logs(raw,mask)
            good=(torch.isfinite(raw).all()&~((raw[...,list(PRICES)]<=0)&mask[...,list(PRICES)]).any()
                  &~((raw[...,list(ACTIVITY)]<0)&mask[...,list(ACTIVITY)]).any())
            time,tv=self.window_time(frame,gather)
            encoded=self.teacher(torch.cat((logs,time),-1),torch.cat((mask,tv),-1),self.positions.expand(len(ids),-1))
            good=good&torch.isfinite(encoded).all()&torch.isfinite(logs).all()
            if self.validation_checks is None:
                if not good:raise ValueError('Invalid source or encoded context')
            else:self.validation_checks.append((good,ValueError,'Invalid source or encoded context'))
            self.bank[frame][ids]=encoded;self.ready[frame][selected]=True;self.encoded_windows+=len(selected)

    @torch.no_grad()
    def batch(self,plan,start,stop):
        selected=[]
        for f,ends in enumerate(plan.endpoints):
            if not self.ready[f][ends[start:stop]].all():raise ValueError('Context not encoded')
            selected.append(ends[start:stop]+self.offsets[f])
        current=np.concatenate(selected+[plan.targets[start:stop,None],plan.origin_us[start:stop,None]],1)
        ids=self.batch_indices(plan,start,stop,current);context=ids[:,:16];origin=ids[:,17,None]
        ages=torch.stack((torch.log1p((origin-self.clock_bank[context-63]).double()/1e6),
                          torch.log1p((origin-self.clock_bank[context]).double()/1e6)),-1).float()
        values,valid,clocks=self.pools[0];future=ids[:,16,None]-self.positions.flip(0)
        raw=values[future];mask=valid[future].bool();logs=raw_logs(raw,mask)
        if not (torch.isfinite(raw).all()&torch.isfinite(logs).all()&
                ~((raw[...,list(PRICES)]<=0)&mask[...,list(PRICES)]).any()&
                ~((raw[...,list(ACTIVITY)]<0)&mask[...,list(ACTIVITY)]).any()):raise ValueError('Invalid future source evidence')
        if not (clocks[future]>origin).all():raise ValueError('Target is not strictly future')
        past=ids[:,7,None]-self.positions.flip(0);pv=valid[past].bool();px=raw_logs(values[past],pv)
        count=pv.sum(1);mean=torch.where(pv,px,0).sum(1)/count.clamp_min(1)
        sorted_values=torch.where(pv,px,float('inf')).sort(1).values
        lo=((count-1).clamp_min(0)//2)[:,None];hi=(count//2)[:,None]
        med=.5*(sorted_values.gather(1,lo).squeeze(1)+sorted_values.gather(1,hi).squeeze(1))
        return self.token_bank[context],ages,dict(logs=logs,valid=mask,raw=raw,
            last=px[:,-1],last_valid=pv[:,-1],mean=mean,median=torch.where(count>0,med,0),history_valid=count>0)
