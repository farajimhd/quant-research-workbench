"""Bounded causal windows for the local ResNet diagnostic, no data writes."""
import numpy as np
import torch
from research.rl_trading.v6.execution_features import bps_input,VERSION
from research.rl_trading.v6.model import INPUT_WIDTH


def prepare_windows(session, decisions, normalization, *, max_bytes=2_000_000_000):
    """Return [B,120,147] train-normalized windows and [B,120] presence.

    Callers must open the certified session and validate teacher bindings first.
    Only requested listing rows through each completed label clock are read.
    Each listing uses its previous certified tail and current actual candles;
    missing candle slots are padding, not synthesized one-second candles.
    """
    b=len(decisions)
    if b*(120*INPUT_WIDTH*4+120)>max_bytes:
        raise ValueError('Local ResNet window budget exceeded; reduce diagnostic scope')
    mean=torch.as_tensor(normalization['mean'],dtype=torch.float32)
    std=torch.as_tensor(normalization['std'],dtype=torch.float32)
    if (normalization.get('version')!=VERSION or normalization.get('scope')!='train_only' or
        mean.shape!=(INPUT_WIDTH,) or std.shape!=mean.shape or
        not torch.isfinite(mean).all() or not torch.isfinite(std).all() or (std<=0).any()):
        raise ValueError('Invalid shared train-only normalization')
    windows=np.zeros((b,120,INPUT_WIDTH),np.float32)
    present=np.zeros((b,120),bool)
    groups={}
    for row,item in enumerate(decisions):
        identity=int(item.held_index[0]) if len(item.held_index) else item.soft_tokens[1]-1
        if not 0<=identity<len(session.listings) or len(item.held_index)>1:
            raise ValueError('Invalid local teacher identity')
        groups.setdefault(identity,[]).append(row)
    for identity,rows in groups.items():
        name=session.listings[identity]
        current=session.bank.listing(name)
        sources=[]
        if session.previous and name in session.previous.manifest['offsets']:
            sources.append(session.previous.listing_tail(name))
        sources.append(current)
        clocks=np.concatenate([s.close_us for s in sources])
        if len(clocks)>1 and np.any(clocks[1:]<=clocks[:-1]):
            raise ValueError('Candle history timestamps are not strictly increasing')
        # Array views/memmaps are retained; no full-day feature concatenation.
        offsets=np.cumsum([0]+[len(s.close_us) for s in sources])
        for start in range(0,len(rows),64):
            selected=rows[start:start+64]
            ends=np.searchsorted(clocks,[decisions[i].close_us for i in selected],side='right')
            indices=ends[:,None]+np.arange(-120,0)[None,:]
            valid=indices>=0
            raw_scalar=np.zeros((len(selected),120,37),np.float32)
            raw_levels=np.zeros((len(selected),120,2,5,11),np.float32)
            for source,low,high in zip(sources,offsets[:-1],offsets[1:]):
                keep=valid&(indices>=low)&(indices<high)
                raw_scalar[keep]=source.scalar[indices[keep]-low]
                raw_levels[keep]=source.levels[indices[keep]-low]
            x=bps_input(torch.from_numpy(raw_scalar.reshape(-1,37)),
                torch.from_numpy(raw_levels.reshape(-1,2,5,11)))
            normalized=((x-mean)/std).reshape(len(selected),120,INPUT_WIDTH).numpy()
            normalized[~valid]=0
            if not np.isfinite(normalized).all():raise ValueError('Nonfinite ResNet input')
            windows[selected]=normalized;present[selected]=valid
    return windows,present
