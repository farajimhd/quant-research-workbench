import json
import numpy as np
from pathlib import Path
from hashlib import sha256
from .features import CATALOG
PROBE_VERSION="v42-training-activation-probe-v1"
MARKET_INPUTS=3*len(CATALOG)
INPUTS=MARKET_INPUTS+18
def load_probe(path,training):
    """The immutable probe must name this exact training manifest and receipts."""
    with np.load(path,allow_pickle=False) as z:
        meta=json.loads(str(z['metadata']))
        if meta.get('version') not in (PROBE_VERSION,'v60-training-boundary-probe-v1') or meta.get('training_sha256')!=sha256(Path(training).read_bytes()).hexdigest():
            raise ValueError('Calibration training identity mismatch')
        manifest=json.loads(Path(training).read_text(encoding='utf-8-sig'))
        if not meta.get('days') or any(d not in manifest['days'] for d in meta['days']) or meta.get('validation_opened') is not False:
            raise ValueError('Calibration must use training only')
        if meta['receipts']!={d:manifest['receipts'][d] for d in meta['days']}:
            raise ValueError('Calibration receipt binding mismatch')
        x=np.array(z['inputs']);known=np.array(z['known']);eligible=np.array(z['eligible']);held=np.array(z['held_states'])
        extra={k:np.array(z[k]) for k in ('ask','bid','quote_valid','ask_size','swings') if k in z}
    if x.ndim!=4 or x.shape[-1]!=INPUTS or x.dtype!=np.float32 or not np.isfinite(x).all() or np.abs(x).max()>1:
        raise ValueError('Bounded normalized activation inputs required')
    if known.shape!=x.shape[:-1]+(len(CATALOG),) or eligible.shape!=x.shape[:-1] or known.dtype!=bool or eligible.dtype!=bool:
        raise ValueError('Calibration mask shape/type mismatch')
    if held.shape!=x.shape[:-1]+(INPUTS-MARKET_INPUTS,) or held.dtype!=np.float32 or not np.isfinite(held).all() or np.abs(held).max()>1:
        raise ValueError('Representative held-state probe required')
    if not 1<=x.shape[0]<=8 or not 2<=x.shape[1]<=64 or not 1<=x.shape[2]<=128 or not eligible.any():
        raise ValueError('Finite calibration envelope exceeded')
    if meta.get('version')=='v60-training-boundary-probe-v1':
        from .authority import REGISTRY_SHA256,SWING_WINDOWS
        if meta.get('registry')!=REGISTRY_SHA256 or len(extra)!=5:raise ValueError('V60 boundary probe contract changed')
        if any(v.shape!=eligible.shape for k,v in extra.items() if k!='swings') or extra['swings'].shape!=eligible.shape+(len(SWING_WINDOWS)**2,):raise ValueError('Boundary probe shape mismatch')
    for name,array in dict(inputs=x,known=known,eligible=eligible,held_states=held,**extra).items():
        if meta.get('slice_sha256',{}).get(name)!=sha256(array.tobytes()).hexdigest():raise ValueError('Calibration slice bytes changed')
    return dict(inputs=x,known=known,eligible=eligible,held_states=held,metadata=meta,sha256=sha256(Path(path).read_bytes()).hexdigest(),**extra)
