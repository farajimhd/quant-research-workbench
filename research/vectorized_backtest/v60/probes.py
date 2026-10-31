from hashlib import sha256
import json
from pathlib import Path
import numpy as np
from .probe_reader import load_probe,PROBE_VERSION,INPUTS,MARKET_INPUTS
from .features import CATALOG

def global_probe(training,days,folder):
    """One window from each of at most eight evenly spaced training sessions."""
    folder=Path(folder);path=folder/'global.npz'
    chosen=[days[i] for i in np.unique(np.linspace(0,len(days)-1,min(8,len(days)),dtype=int))]
    sources=[load_probe(folder/(d+'.npz'),training) for d in chosen]
    if any(s['metadata']['days']!=[d] for d,s in zip(chosen,sources)):raise ValueError('Global probe session label differs from local source')
    if path.exists():
        probe=load_probe(path,training)
        if probe['metadata']['days']!=chosen or probe['metadata']['local_probe_hashes']!=[s['sha256'] for s in sources]:raise ValueError('Global calibration inputs changed')
        return path
    width=max(s['inputs'].shape[2] for s in sources);steps=sources[0]['inputs'].shape[1]
    arrays=dict(inputs=np.zeros((len(sources),steps,width,INPUTS),np.float32),
        known=np.zeros((len(sources),steps,width,len(CATALOG)),bool),eligible=np.zeros((len(sources),steps,width),bool),
        held_states=np.zeros((len(sources),steps,width,INPUTS-MARKET_INPUTS),np.float32))
    for i,s in enumerate(sources):
        if s['inputs'].shape[1]!=steps:raise ValueError('Global calibration window length differs')
        n=s['inputs'].shape[2]
        for key in arrays:arrays[key][i,:,:n]=s[key][0]
    manifest=json.loads(Path(training).read_text(encoding='utf-8-sig'))
    meta=dict(version=PROBE_VERSION,training_sha256=sha256(Path(training).read_bytes()).hexdigest(),days=chosen,
        receipts={d:manifest['receipts'][d] for d in chosen},validation_opened=False,
        local_probe_hashes=[s['sha256'] for s in sources],slice_sha256={k:sha256(v.tobytes()).hexdigest() for k,v in arrays.items()})
    temporary=path.with_suffix('.tmp.npz');np.savez(temporary,**arrays,metadata=np.asarray(json.dumps(meta,sort_keys=True)));temporary.replace(path)
    load_probe(path,training)
    return path
