"""Streaming reconstruction diagnostics. Targets remain original source bars."""
import torch
from .transforms import inverse, references, decode
from ..v5.shards.phase1_contract import FRAMES
from ..phase1_v5_raw.engine import FRAME_NAMES

BINS=['unchanged','down_0_to_0.05pct','down_0.05_to_0.1pct','down_0.1_to_0.25pct','down_0.25_to_0.5pct','down_above_0.5pct',
      'up_0_to_0.05pct','up_0.05_to_0.1pct','up_0.1_to_0.25pct','up_0.25_to_0.5pct','up_above_0.5pct']
FIELDS=['count','absolute_error_pct','signed_error_pct','direction_correct','actual_pct','predicted_pct']


class Diagnostics:
    def __init__(self,device='cuda'):
        self.device=device;self.edges=torch.tensor([.05,.1,.25,.5],device=device,dtype=torch.float64)
        self.tables={};self.confusions={}
        self.raw=torch.zeros(6,18,6,device=device,dtype=torch.float64)
        self.refs=torch.zeros(6,9,3,device=device,dtype=torch.float64)
        self.nonfinite=torch.zeros((),device=device,dtype=torch.int64)

    def bucket(self,x):
        return torch.where(x==0,0,1+torch.bucketize(x.abs().contiguous(),self.edges,right=False)+5*(x>0).long())

    def moves(self,name,actual,predicted,mask,frame):
        # Input B x T x 3; masks exclude invalid source endpoints, never based on error size.
        shape=actual.shape;group=frame[:,None,None]*3+torch.arange(3,device=actual.device)[None,None,:]
        group=group.expand(shape);a=self.bucket(actual);p=self.bucket(predicted)
        ix=(group*11+a).reshape(-1)
        values=torch.stack((torch.ones_like(actual),(predicted-actual).abs(),predicted-actual,
                            (predicted.sign()==actual.sign()).double(),actual,predicted),-1)
        values=torch.where(mask[...,None],values,0).reshape(-1,6)
        if name not in self.tables:
            self.tables[name]=torch.zeros(18*11,6,device=actual.device,dtype=torch.float64)
            self.confusions[name]=torch.zeros(18*121,device=actual.device,dtype=torch.float64)
        self.tables[name].scatter_add_(0,ix[:,None].expand(-1,6),values)
        self.confusions[name].scatter_add_(0,(group*121+a*11+p).reshape(-1),mask.double().reshape(-1))

    @torch.no_grad()
    def add(self,model,out,values,valid,hidden,b,visible):
        raw=b.raw.double();v=valid[...,:18];selected=hidden if visible==16 else torch.ones_like(hidden)
        frame=(b.frames[:,None]==torch.tensor(FRAMES,device=raw.device)).long().argmax(1)
        group=torch.nn.functional.one_hot(frame,6).T.double()
        observed=v&(~hidden[...,None]) if visible==16 else v
        ref,rv=references(values[...,:18],observed,model.encoder.reference_mean)
        pred=inverse(out['logs']);oracle=inverse(decode(out['code'],ref))
        self.nonfinite+=(~torch.isfinite(pred)).sum()+(~torch.isfinite(oracle)).sum()
        delta=out['references'].double()-ref.double()
        delta[:,0::3]=100*torch.expm1(delta[:,0::3])
        for i,z in enumerate((torch.ones_like(delta),delta,delta.abs())):
            self.refs[:,:,i]+=group@torch.where(rv,z,0)
        mask=v&selected[...,None];err=pred-raw
        safe=torch.where(v&(raw!=0),raw,1)
        # All18 raw units; percentage signed errors only meaningful for nonzero targets.
        fields=(mask.double(),torch.where(mask,err,0),torch.where(mask,err.abs(),0),
                torch.where(mask,(oracle-raw).abs(),0),torch.where(mask&(raw!=0),100*err/safe,0),(mask&(raw!=0)).double())
        for i,z in enumerate(fields):self.raw[:,:,i]+=group@z.sum(1)
        for suffix,p in (('',pred),('_oracle_reference_DIAGNOSTIC',oracle)):
            y=raw.reshape(-1,64,3,6);p=p.reshape(-1,64,3,6);vm=v.reshape(-1,64,3,6)
            o,h,l,c=y[...,:4].unbind(-1);po,ph,pl,pc=p[...,:4].unbind(-1)
            def ratio(a,b):return 100*(a/torch.where(b>0,b,1)-1)
            self.moves('candle_close_vs_open'+suffix,ratio(c,o),ratio(pc,po),vm[...,0]&vm[...,3]&selected[...,None],frame)
            self.moves('consecutive_close'+suffix,ratio(c[:,1:],c[:,:-1]),ratio(pc[:,1:],pc[:,:-1]),
                       vm[:,1:,:,3]&vm[:,:-1,:,3]&(selected[:,1:]&selected[:,:-1])[...,None],frame)
            self.moves('window_last_vs_first_close'+suffix,ratio(c[:,-1:],c[:,:1]),ratio(pc[:,-1:],pc[:,:1]),
                       vm[:,-1:,:,3]&vm[:,:1,:,3]&(selected[:,-1:]&selected[:,:1])[...,None],frame)
            self.moves('high_low_range'+suffix,ratio(h,l),ratio(ph,pl),vm[...,1]&vm[...,2]&selected[...,None],frame)
        # Last observed bar reconstruction offset is the most direct Phase2 input check.
        self.moves('last_close_offset',torch.zeros_like(raw[:,-1:,[3,9,15]]),
                   100*(pred[:,-1:,[3,9,15]]/torch.where(v[:,-1:,[3,9,15]],raw[:,-1:,[3,9,15]],1)-1),
                   v[:,-1:,[3,9,15]]&selected[:,-1:,None],frame)

    def finish(self):
        return dict(timeframes=FRAME_NAMES,families=['trade','bid','ask'],bins=BINS,move_fields=FIELDS,
                    moves={k:v.reshape(6,3,11,6).cpu().tolist() for k,v in self.tables.items()},
                    confusion={k:v.reshape(6,3,11,11).cpu().tolist() for k,v in self.confusions.items()},
                    raw_fields=['count','signed_error_sum','absolute_error_sum','oracle_absolute_error_sum','signed_relative_pct_sum','nonzero_count'],
                    raw=self.raw.cpu().tolist(),reference_fields=['count','signed_error_sum','absolute_error_sum'],
                    reference_units='Price references: percent; activity references: log1p units',references=self.refs.cpu().tolist(),
                    nonfinite=int(self.nonfinite))
