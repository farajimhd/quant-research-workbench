"""Fail-closed GPU binding for certified, compressed causal history."""
import json
from pathlib import Path
import numpy as np
from .history_bank import VERSION,SWING_WINDOWS,LAYOUT
from .runtime import file_hash
from .compact_prepare import KEY_STRIDE


def load_history(inputs,root,maximum_gib=16.):
    root=Path(root);record=json.loads((root/'complete.json').read_text())
    union=np.unique(inputs.arrays['top_indices']);union=union[union>=0]
    if (record.get('version')!=VERSION or record.get('status')!='complete' or record.get('validation_opened') is not False
            or record.get('input_receipt_sha256')!=file_hash(inputs.root/'complete.json')
            or record.get('listing_ids')!=union.tolist() or record.get('swing_windows')!=list(SWING_WINDOWS)
            or record.get('implementation_sha256')!=file_hash(Path(__file__).with_name('history_bank.py'))
            or record.get('layout')!={k:list(v) for k,v in LAYOUT.items()} or record.get('clocks')!=len(inputs.arrays['clocks'])):
        raise ValueError('Persisted history contract/identity mismatch')
    if record['resident_bytes']>maximum_gib*1024**3:raise MemoryError('History residency exceeds explicit envelope')
    arrays={}
    for name,digest in record['files'].items():
        if Path(name).name!=name or file_hash(root/name)!=digest:raise ValueError('Persisted history integrity mismatch')
        arrays[name[:-4]]=np.load(root/name,mmap_mode='r',allow_pickle=False)
    n=len(inputs.arrays['clocks']);count=len(union);rows=record['compressed_rows']
    if sum(v.nbytes for v in arrays.values())!=record['resident_bytes']:
        raise ValueError('Persisted history declared memory differs from arrays')
    if (set(arrays)!={'values','ids','source_rows'} or arrays['values'].shape!=(rows,332) or arrays['values'].dtype!=np.float64
            or arrays['ids'].shape!=(n,count) or arrays['ids'].dtype!=np.int32
            or arrays['source_rows'].shape!=(n,count) or arrays['source_rows'].dtype!=np.int64
            or arrays['ids'].min()<0 or arrays['ids'].max()>=rows
            or arrays['source_rows'].min()<-1 or arrays['source_rows'].max()>=len(inputs.arrays['market_keys'])):
        raise ValueError('Persisted history row dimensions invalid')
    keys=inputs.arrays['market_keys'];clocks=inputs.arrays['clocks']
    for begin in range(0,n,256):
        source=arrays['source_rows'][begin:begin+256];known=source>=0;mapped=keys[source.clip(0)]
        if np.any(known&((mapped//KEY_STRIDE!=union[None])|(mapped%KEY_STRIDE>clocks[begin:begin+256,None]))):
            raise ValueError('Persisted market lookup violates identity or causality')
    return arrays,record


def bind_history(inputs,root,maximum_gib=16.):
    arrays,record=load_history(inputs,root,maximum_gib)
    root=Path(root)
    inputs.history_binding={k:inputs._transfer(v) for k,v in arrays.items()}
    inputs.history_binding['receipt_sha256']=file_hash(root/'complete.json')
    return inputs.history_binding
