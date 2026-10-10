"""Project certified swing stops only where entry can execute."""
import numpy as np
import torch
from research.vectorized_backtest.v6.torch_backtest.history_bank import SWING_WINDOWS


def prepare_entry_swings(data,maximum_gib=2.):
    """CPU preparation runs with the other resident banks in the I/O loader."""
    membership=data.host_tensors['membership'].numpy()
    observed=data.host_tensors['observed'].numpy()
    eligible=np.zeros_like(observed)
    eligible[1:]=membership[:-1]&observed[1:]
    eligible[-1]=False  # Terminal liquidation forbids new entries.
    slots=np.flatnonzero(eligible)
    rows,inverse=np.unique(np.asarray(data.bank['ids']).reshape(-1)[slots],return_inverse=True)
    width=len(SWING_WINDOWS)**2
    required=(len(rows)+1)*width*8
    if required>maximum_gib*1024**3:raise MemoryError('V7 entry swing projection exceeds explicit envelope')
    bank=np.full((len(rows)+1,width),np.nan,dtype=np.float64)
    bank[1:]=data.bank['values'][rows[:,None],211+np.arange(width)[None]]
    ids=np.zeros_like(data.bank['ids'],dtype=np.int32)
    ids.reshape(-1)[slots]=inverse+1
    return dict(entry_swing_values=bank,entry_swing_ids=ids)


def entry_swings(data,members,maximum_gib=2.):
    # Small test/diagnostic adapters retain their explicit swing-bank contract.
    if not hasattr(data,'bank'):
        values,columns=data.swing_bank(members)
        return values,columns,data.tensors['history_ids']
    def transfer(array):
        host=torch.from_numpy(np.array(array,copy=True))
        if data.device.type=='cuda':host=host.pin_memory()
        return host.to(data.device,non_blocking=True)
    resident=getattr(data,'resident_features',None)
    if resident is not None and resident.device==data.device:
        values=resident.tensors['entry_swing_values'];ids=resident.tensors['entry_swing_ids']
    else:
        arrays=prepare_entry_swings(data,maximum_gib)
        values=transfer(arrays['entry_swing_values']);ids=transfer(arrays['entry_swing_ids'])
    if values.numel()*values.element_size()>maximum_gib*1024**3:
        raise MemoryError('V7 entry swing projection exceeds explicit envelope')
    columns=np.array([SWING_WINDOWS.index(m.policy.swing_left)*len(SWING_WINDOWS)+SWING_WINDOWS.index(m.policy.swing_right)
                      if m.policy.swing_left else 0 for m in members],dtype=np.int64)
    return values,transfer(columns),ids
