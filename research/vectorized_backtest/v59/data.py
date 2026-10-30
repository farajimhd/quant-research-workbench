"""Resident, source-bound top-of-book execution quotes; no runtime lookup per tick."""
import numpy as np
import torch
from research.vectorized_backtest.v42.data import SessionData as BaseSessionData

QUOTE_VERSION = 'v57-asof-top-of-book-1s-v1'


def quote_arrays(market, source_rows, clocks, maximum_age=1.):
    if maximum_age not in (1.,None):
        raise ValueError('V59 uses the existing one-second quote validity contract')
    source = np.asarray(source_rows)
    known = source >= 0; safe = source.clip(0)
    bid = np.asarray(market['bid'])[safe].astype(np.float64)
    ask = np.asarray(market['ask'])[safe].astype(np.float64)
    stamps = np.asarray(market['quote_us'])[safe]
    age = np.asarray(clocks)[:, None] - stamps / 1e6
    valid = (known & np.isfinite(bid) & np.isfinite(ask) & (bid > 0) &
             (ask >= bid) & (stamps > 0) & (age >= 0))
    if maximum_age is not None:valid&=age<=maximum_age
    # Neutral unused values avoid 0*NaN in financial arithmetic or action feedback.
    bid[~valid] = 0.; ask[~valid] = 0.
    return dict(bid=bid, ask=ask, quote_valid=valid)


class SessionData(BaseSessionData):
    def __init__(self, *args, maximum_quote_age_seconds=1., execution_tail=None, **kwargs):
        super().__init__(*args, **kwargs)
        arrays = quote_arrays(self.market, self.bank['source_rows'], self.arrays['clocks'], maximum_quote_age_seconds)
        if execution_tail is not None:
            from pathlib import Path
            import json,hashlib
            self.execution_tail=Path(execution_tail)
            proof=(self.execution_tail/'complete.json').read_bytes()
            identity=json.loads(proof)['identity']
            if identity['input_receipt_sha256']!=self.identity['input_sha256']:raise ValueError('Execution tail input mismatch')
            clock=np.asarray(self.arrays['clocks'])[:,None]
            if not (np.diff(clock[:,0])==1).all():raise ValueError('Persistent execution requires the certified one-second clock')
            from .order_tail import decision_quotes
            arrays=decision_quotes(self.execution_tail,self)
            self.identity['execution_tail_sha256']=hashlib.sha256(proof).hexdigest()
        added = sum(v.nbytes for v in arrays.values())
        if self.bytes + added > kwargs.get('maximum_gib', 4.) * 2**30:
            self.close(); raise MemoryError('Execution quote tensors exceed price/rank admission')
        self.bytes += added
        self.host_tensors.update({k: torch.from_numpy(v) for k, v in arrays.items()})
        self.tensors.update({k: v.to(self.device) for k, v in self.host_tensors.items()
                             if k in arrays})
        valid = arrays['quote_valid']; observed = self.host_tensors['observed'].numpy()
        version='v59-causal-displayed-book-no-age-v2' if execution_tail else QUOTE_VERSION
        self.quote_coverage = dict(version=version, maximum_age_seconds=maximum_quote_age_seconds,
            cells=int(valid.size), valid_cells=int(valid.sum()), observed_cells=int(observed.sum()),
            observed_with_valid_quote=int((valid & observed).sum()),
            terminal_valid_quotes=int(valid[-1].sum()), listings=int(valid.shape[1]),
            additional_host_bytes=added, missing_quote_fill='pending; displayed size consumed once per snapshot' if execution_tail else 'reject; never close-price fallback')
        self.identity['execution_quotes'] = dict(version=version, maximum_age_seconds=maximum_quote_age_seconds)
        from .swing_projection import prepare,VERSION
        swings=prepare(self)
        if self.bytes+sum(v.nbytes for v in swings.values())>kwargs.get('maximum_gib',4.)*2**30:
            self.close();raise MemoryError('Entry history tensors exceed data admission')
        self.host_tensors.update({k:torch.from_numpy(v) for k,v in swings.items()})
        self.tensors.update({k:v.to(self.device) for k,v in self.host_tensors.items() if k in swings})
        self.bytes+=sum(v.nbytes for v in swings.values())
        self.identity['entry_history']=VERSION
