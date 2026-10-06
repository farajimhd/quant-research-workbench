"""Read-only, hash-certified V6-schema features; no database or label reads."""
from dataclasses import dataclass
from pathlib import Path
import json
import numpy as np
import torch
from .runtime import file_hash

SCALARS=('log_open','log_high','log_low','log_close','bar_vwap_rel','session_vwap_rel','bar_vwap_valid','session_vwap_valid','log_volume','log_trades','log_volume_60s','log_trades_60s','log_rvol_10s_prev_session','rvol_10s_available','macd_line_rel','macd_signal_rel','rsi_14','atr_14_rel','ema_7_rel','ema_26_rel','indicator_available','time_of_day_sin','time_of_day_cos','premarket','regular','after_hours','log_inter_candle_gap','log_float_shares','float_present','log_shares_outstanding','shares_present','log_days_since_split','split_present','log_days_since_reverse_split','reverse_split_present','bar_price_valid','bar_extremes_valid')
LEVELS=('center_distance_rel','lower_distance_rel','upper_distance_rel','log_observation_count','log_today_observation_count','log_age_since_confirmation','role_support','role_resistance','role_transition','historical_origin','present')

@dataclass(frozen=True)
class Feature:
    name:str
    unit:str
    lower:float
    upper:float
    mask:int|None=None

def catalog():
    out=[]
    for i,name in enumerate(SCALARS):
        mask=None;unit='ratio';lo=-2.;hi=2.
        if name in ('log_open','log_high','log_low','log_close'):unit='log_price';lo=-5.;hi=10.;mask=35 if i in (0,3) else 36
        elif name.endswith(('_valid','_available','_present')) or name in ('premarket','regular','after_hours'):unit='bool';lo=0.;hi=1.
        elif name.startswith('log_'):
            unit='log_'+('shares' if 'volume' in name or 'shares' in name else 'count' if 'trades' in name else 'duration');lo=0.;hi=30.
        if i in (4,5):mask=6 if i==4 else 7
        elif i==12:mask=13;lo=0.;hi=20.
        elif 14<=i<=19:mask=20
        elif i in (27,29,31,33):mask=i+1
        if i==16:lo=0.;hi=1.
        out.append(Feature(name,unit,lo,hi,mask))
    for side in ('below','above'):
        for slot in range(1,6):
            start=len(out)
            for i,name in enumerate(LEVELS):
                unit='ratio' if i<3 else 'log_count' if i<5 else 'log_duration' if i==5 else 'bool'
                out.append(Feature(f'v7.{side}.{slot}.{name}',unit,-10. if i<3 else 0.,10. if i<3 else 30. if i<6 else 1.,None if i==10 else start+10))
    # Derived consumer inputs; the original 147 bank channels keep their IDs.
    out.extend(Feature(name,'bool',0.,1.) for name in ('split_this_session','reverse_split_this_session'))
    return tuple(out)

CATALOG=catalog()

def validity(features):
    valid=torch.isfinite(features)
    for i,f in enumerate(CATALOG):
        if f.mask is not None:valid[:,i]&=features[:,f.mask]==1
    return valid

class CertifiedBank:
    def __init__(self,root,*,expected_day=None):
        self.root=Path(root);self.day=json.loads((self.root/'plan.json').read_text(encoding='utf-8'))
        cert=json.loads((self.root/'complete.json').read_text(encoding='utf-8'))
        self.manifest=json.loads((self.root/'bank'/'complete.json').read_text(encoding='utf-8'))
        from hashlib import sha256
        plan={k:v for k,v in self.day.items() if k!='hash'}
        # Producer's canonical digest uses compact separators and sorted keys.
        from research.rl_trading.v1.common import digest
        if digest(plan)!=self.day.get('hash') or cert.get('status')!='complete' or cert.get('plan_hash')!=self.day['hash']:
            raise ValueError('Feature producer plan/certificate mismatch')
        if expected_day and self.day['day']!=expected_day:raise ValueError('Wrong feature day')
        if tuple(self.manifest['scalar_names'])!=SCALARS or tuple(self.manifest['level_names'])!=LEVELS or self.manifest['source_hash']!=self.day['hash']:
            raise ValueError('V6 feature schema/source mismatch')
        if cert['bank_file_hashes']!=self.manifest['files_sha256']:raise ValueError('Feature bank certificate changed')
        arrays=[]
        for name in ('close_us.npy','scalar.npy','levels.npy'):
            path=self.root/'bank'/name
            if file_hash(path)!=self.manifest['files_sha256'][name]:raise ValueError('Feature bank bytes changed')
            arrays.append(np.load(path,mmap_mode='r',allow_pickle=False))
        self.clocks,self.scalar,self.levels=arrays
        c=len(self.clocks)
        if self.clocks.dtype!=np.int64 or self.scalar.shape!=(c,37) or self.levels.shape!=(c,2,5,11) or self.scalar.dtype!=np.float32 or self.levels.dtype!=np.float32:
            raise ValueError('Malformed feature bank shapes/types')
        cursor=0
        for identity,(left,right) in sorted(self.manifest['offsets'].items()):
            if left!=cursor or right<left or right>c or np.any(np.diff(self.clocks[left:right])<=0):raise ValueError('Feature bank identity offsets/clocks invalid')
            if self.day['census'][identity]!=right-left:raise ValueError('Feature census mismatch')
            cursor=right
        if cursor!=c:raise ValueError('Feature rows outside identity census')
        self.certificate_hash=file_hash(self.root/'complete.json')

    def listing(self,identity,*,device='cpu',previous=None,start_us=None,end_us=None):
        left,right=self.manifest['offsets'][identity]
        clocks=np.array(self.clocks[left:right],copy=True)
        x=np.concatenate((self.scalar[left:right],self.levels[left:right].reshape(-1,110)),axis=1)
        basis=getattr(self,'split_basis',None)
        if basis is not None:
            from .splits import rvol_view
            x=rvol_view(x,basis[identity]['rvol_price_factor'])
        from .splits import append_session_flags
        x=append_session_flags(x,basis[identity] if basis is not None else None)
        if previous is not None and identity in previous.manifest['offsets']:
            a,b=previous.manifest['offsets'][identity];a=max(a,b-119)
            old=np.concatenate((previous.scalar[a:b],previous.levels[a:b].reshape(-1,110)),axis=1)
            previous_basis=getattr(previous,'split_basis',None)
            old=append_session_flags(old,previous_basis[identity] if previous_basis is not None else None)
            if basis is not None:
                from .splits import history_view,rvol_view
                old=rvol_view(old,previous.split_basis[identity]['rvol_price_factor'])
                old=history_view(old,basis[identity]['history_price_factor'])
            if len(clocks) and b>a and previous.clocks[b-1]>=clocks[0]:raise ValueError('Previous context reaches future')
            x=np.concatenate((old,x));clocks=np.concatenate((previous.clocks[a:b],clocks))
        if end_us is not None:
            stop=int(np.searchsorted(clocks,end_us,side='right'));x=x[:stop];clocks=clocks[:stop]
        if start_us is not None:
            begin=max(0,int(np.searchsorted(clocks,start_us,side='left'))-119);x=x[begin:];clocks=clocks[begin:]
        tensor=torch.from_numpy(np.array(x,copy=True)).to(device)
        return torch.from_numpy(clocks).to(device),tensor,validity(tensor)
