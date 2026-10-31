"""Quote-size execution uses causal history independently of observed trade bars."""
import numpy as np
import torch
from research.vectorized_backtest.v60.authority import SWING_WINDOWS

VERSION='v60-entry-history-no-observed-gate-v1'

def prepare(data,maximum_gib=2.):
    membership=data.host_tensors['membership'].numpy()
    eligible=membership.copy()
    eligible[-1]=False
    slots=np.flatnonzero(eligible)
    rows,inverse=np.unique(np.asarray(data.bank['ids']).reshape(-1)[slots],return_inverse=True)
    width=len(SWING_WINDOWS)**2
    if (len(rows)+1)*width*8>maximum_gib*2**30:raise MemoryError('Entry history exceeds admitted envelope')
    values=np.full((len(rows)+1,width),np.nan,dtype=np.float64)
    values[1:]=data.bank['values'][rows[:,None],211+np.arange(width)[None]]
    ids=np.zeros_like(data.bank['ids'],dtype=np.int32)
    ids.reshape(-1)[slots]=inverse+1
    return dict(v60_entry_swing_values=values,v60_entry_swing_ids=ids)

def entry_swings(data,members):
    if not hasattr(data,'bank'):
        values,columns=data.swing_bank(members)
        return values,columns,data.tensors['history_ids']
    columns=[SWING_WINDOWS.index(m.policy.swing_left)*len(SWING_WINDOWS)+SWING_WINDOWS.index(m.policy.swing_right) if m.policy.swing_left else 0 for m in members]
    return data.tensors['v60_entry_swing_values'],torch.tensor(columns,dtype=torch.int64,device=data.device),data.tensors['v60_entry_swing_ids']
