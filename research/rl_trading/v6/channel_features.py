"""Own-channel causal changes alongside the preserved V6 candle features.

Only adjacent observed candles in one ordered listing history are compared.
No target labels enter this adapter. V7 slot identities are unavailable in the
bank: structural changes are deliberately unavailable rather than fabricated.
"""
import torch
from torch import nn
from research.rl_trading.v6.features import SCALAR_NAMES
from research.rl_trading.v6.execution_features import bps_input
from research.rl_trading.v6.temporal_encoders import StructuredProjection, TemporalCandleEncoder

VERSION = 'rl-v6-own-channel-history-v1'
CHANGE_NAMES = ('open', 'high', 'low', 'close', 'bar_vwap', 'session_vwap',
                'ema_7', 'ema_26', 'atr_14', 'volume', 'trades', 'volume_60s',
                'trades_60s', 'rvol_10s', 'macd_line', 'macd_signal', 'rsi_14')
EXTRA_NAMES = tuple('change_'+n for n in CHANGE_NAMES) + tuple(
    'change_available_'+n for n in CHANGE_NAMES) + ('session_boundary',)
INDEX = {n:i for i,n in enumerate(SCALAR_NAMES)}


def engineer(raw, present):
    """[B,T,147] -> own-channel changes, masks; missing predecessors are zero.

    Prices/positive indicators use log ratios; counts use log1p differences.
    MACD uses bounded signed relative change, RSI uses a difference. Indicator
    physical values are reconstructed from their certified relative channels.
    Original log price, log activity and gap remain separate base channels.
    """
    if raw.ndim != 3 or raw.shape[-1] != 147 or present.shape != raw.shape[:2] or present.dtype != torch.bool:
        raise ValueError('Ordered raw histories and Boolean presence required')
    scalar = raw[...,:37]
    prev = torch.cat((scalar[:,:1], scalar[:,:-1]),1)
    adjacent = present & torch.cat((torch.zeros_like(present[:,:1]),present[:,:-1]),1)
    same_session = (scalar[...,23:26] == prev[...,23:26]).all(-1)
    # A same-regime overnight boundary is exposed by the retained elapsed gap.
    boundary = adjacent & (~same_session | (scalar[...,26] > torch.log(scalar.new_tensor(3601.))))
    pair = adjacent & ~boundary
    price_ok = pair & scalar[...,35].bool() & prev[...,35].bool()
    indicator_ok = price_ok & scalar[...,20].bool() & prev[...,20].bool()
    changes=[]; masks=[]
    def add(value, mask):
        changes.append(torch.where(mask,value,0.)); masks.append(mask.to(raw.dtype))
    for name in ('log_open','log_high','log_low','log_close'):
        mask = price_ok
        if name in ('log_high', 'log_low'):
            mask = mask & scalar[...,36].bool() & prev[...,36].bool()
        add(scalar[...,INDEX[name]]-prev[...,INDEX[name]], mask)
    for name, flag, positive_offset in (
        ('bar_vwap_rel','bar_vwap_valid',True),('session_vwap_rel','session_vwap_valid',True),
        ('ema_7_rel','indicator_available',True),('ema_26_rel','indicator_available',True),
        ('atr_14_rel','indicator_available',False)):
        a=scalar[...,INDEX[name]]+(1. if positive_offset else 0.)
        b=prev[...,INDEX[name]]+(1. if positive_offset else 0.)
        mask=price_ok & scalar[...,INDEX[flag]].bool() & prev[...,INDEX[flag]].bool() & (a>0) & (b>0)
        add(scalar[...,3]-prev[...,3]+torch.log(a.clamp_min(1e-20))-torch.log(b.clamp_min(1e-20)),mask)
    for name in ('log_volume','log_trades','log_volume_60s','log_trades_60s'):
        add(scalar[...,INDEX[name]]-prev[...,INDEX[name]],pair)
    add(scalar[...,12]-prev[...,12],pair & scalar[...,13].bool() & prev[...,13].bool())
    for name in ('macd_line_rel','macd_signal_rel'):
        # Divide both physical values by current close to avoid exp(log price).
        a=scalar[...,INDEX[name]]
        b=prev[...,INDEX[name]]*torch.exp((prev[...,3]-scalar[...,3]).clamp(-40,40))
        add((a-b)/(a.abs()+b.abs()+1e-8),indicator_ok)
    add(scalar[...,16]-prev[...,16],indicator_ok)
    return torch.stack(changes+masks+[boundary.to(raw.dtype)],-1)


class ChannelProjection(nn.Module):
    in_features = 147  # Raw state storage retains the certified bank schema.
    def __init__(self, width, base_normalization, extra_normalization):
        super().__init__()
        self.base=StructuredProjection(width)
        self.extra=nn.Sequential(nn.Linear(len(EXTRA_NAMES),width),nn.GELU(),nn.LayerNorm(width))
        self.fuse=nn.Sequential(nn.Linear(2*width,width),nn.GELU(),nn.LayerNorm(width))
        for prefix,normalization in (('base',base_normalization),('extra',extra_normalization)):
            for name in ('mean','std'):
                values=torch.tensor(normalization[name],dtype=torch.float32)
                expected=147 if prefix=='base' else len(EXTRA_NAMES)
                if values.shape!=(expected,) or not torch.isfinite(values).all() or (name=='std' and (values<=0).any()):
                    raise ValueError('Invalid TRAIN normalization')
                self.register_buffer(prefix+'_'+name,values)

    def forward(self, raw):
        if raw.ndim!=2:raise ValueError('Use project_history for channel changes')
        return self.history(raw[:,None],torch.ones(raw.shape[0],1,dtype=torch.bool,device=raw.device))[:,0]

    def history(self, raw, present):
        shape=raw.shape; flat=raw.reshape(-1,147)
        base=bps_input(flat[:,:37],flat[:,37:].reshape(-1,2,5,11)).reshape(shape)
        base=(base-self.base_mean)/self.base_std
        extra=(engineer(raw,present)-self.extra_mean)/self.extra_std
        return self.fuse(torch.cat((self.base(base),self.extra(extra)),-1))*present[...,None]


class ChannelEncoder(TemporalCandleEncoder):
    def encode_listing(self, scalar, levels):
        raw=self._input(scalar,levels)
        if not len(raw):return raw.new_zeros((0,self.width))
        raw=torch.nn.functional.pad(raw,(0,0,119,0))
        present=torch.arange(len(raw),device=raw.device)>=119
        outputs=[]
        for start in range(0,len(scalar),8):
            indices=torch.arange(start,min(start+8,len(scalar)),device=raw.device)[:,None]+torch.arange(120,device=raw.device)[None]
            mask=present[indices]
            outputs.append(self.encode_history(self.project_history(raw[indices],mask),mask))
        return torch.cat(outputs)

    def observe(self, *args, **kwargs):
        raise ValueError('Own-channel streaming requires refreshable SparseCandleState')


def install(policy, base_normalization, extra_normalization):
    old=policy.encoder
    encoder=ChannelEncoder(old.width,architecture='tcn',structured=True).to(next(policy.parameters()).device)
    policy.encoder=encoder
    encoder.project=ChannelProjection(encoder.width,base_normalization,extra_normalization).to(next(policy.parameters()).device)
    # Keep raw absolute/relative bank fields for predecessor reconstruction.
    encoder.feature_contract='legacy'
    encoder.project_history=encoder.project.history
    encoder.requires_ordered_projection=True
    encoder.encoder_version=VERSION
    return policy


def fit_changes(sessions):
    """Fit changes from unique strictly prior TRAIN history rows only."""
    import numpy as np
    total=0; summed=torch.zeros(len(EXTRA_NAMES),dtype=torch.float64); squares=summed.clone()
    for session,targets in sessions:
        if session.role!='train':raise ValueError('TRAIN-only normalization')
        clocks={}
        for d in targets:
            index=int(d.held_index[0]) if len(d.held_index) else d.soft_tokens[1]-1
            clocks.setdefault(index,[]).append(d.close_us)
        for index,ends in clocks.items():
            identity=session.listings[index]; sources=[]
            if session.previous and identity in session.previous.manifest['offsets']:sources.append(session.previous.listing_tail(identity))
            sources.append(session.bank.listing(identity))
            times=np.concatenate([s.close_us for s in sources])
            if len(times)>1 and (np.diff(times)<=0).any():raise ValueError('Unordered source candles')
            positions=np.searchsorted(times,np.unique(ends),side='left')[:,None]-np.arange(120,0,-1)[None]
            used=np.unique(positions[positions>=0])
            if not len(used):continue
            scalar=np.concatenate([s.scalar for s in sources]); levels=np.concatenate([s.levels for s in sources])
            # Whole causal prefix ensures each selected row uses its predecessor.
            raw=torch.from_numpy(np.concatenate((scalar,levels.reshape(-1,110)),1))
            values=engineer(raw[None],torch.ones(1,len(raw),dtype=torch.bool))[0,used].double()
            if not torch.isfinite(values).all():raise ValueError('Nonfinite change features')
            total+=len(values);summed+=values.sum(0);squares+=(values*values).sum(0)
    if not total:raise ValueError('No TRAIN history observations')
    mean=summed/total;std=(squares/total-mean.square()).clamp_min(0).sqrt()
    std=torch.where(std>1e-6,std,1.)
    mean[len(CHANGE_NAMES):]=0.;std[len(CHANGE_NAMES):]=1.
    return dict(version=VERSION,names=EXTRA_NAMES,mean=mean.tolist(),std=std.tolist(),observations=total,
                scope='unique_strictly_prior_TRAIN_histories',structural_changes='unavailable_without_stable_level_identity')
