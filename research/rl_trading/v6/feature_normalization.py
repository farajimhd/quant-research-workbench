"""Training-only streaming normalization for the bps adapter; no new banks."""
import json
from pathlib import Path
import numpy as np
import torch
from research.rl_trading.v6.execution_features import VERSION,bps_input,CANDLE_NORMALIZATION_VERSION
from research.rl_trading.v6.features import SCALAR_NAMES,LEVEL_NAMES


def fit_normalization(sessions,*,dataset_sha256,include_context=False):
    count=0;mean=None;m2=None;certificates={};context_receipts={};context_count=0
    for session in sessions:
        if session.role!='train':raise ValueError('Normalization must not read development or held-out')
        certificates[str(session.day)]=session.source_certificate_sha256
        if include_context:
            if not session.context_split_receipt_sha256:raise ValueError('Split-adjusted context receipt required')
            context_receipts[str(session.day)]=session.context_split_receipt_sha256
            if not len(session.bank.close_us):raise ValueError('Current bank is empty for context normalization')
        opening_fence=int(session.bank.close_us.min()) if include_context else None
        def sources():
            for start in range(0,len(session.bank.scalar),65536):
                yield session.bank.scalar[start:start+65536],session.bank.levels[start:start+65536]
            if include_context and session.previous:
                for identity in session.listings:
                    if identity in session.previous.manifest['offsets']:
                        tail=session.previous.listing_tail(identity)
                        if len(tail.close_us):
                            if tail.close_us[-1]>=opening_fence:raise ValueError('Prior context crossed current-session fence')
                            yield tail.scalar,tail.levels
        current=len(session.bank.scalar);seen=0
        for raw_scalar,raw_levels in sources():
            scalar=torch.from_numpy(np.asarray(raw_scalar).copy())
            levels=torch.from_numpy(np.asarray(raw_levels).copy())
            x=bps_input(scalar,levels).numpy().astype(np.float64)
            if not np.isfinite(x).all():raise ValueError('Nonfinite normalized feature source')
            n=len(x);batch_mean=x.mean(0);batch_m2=((x-batch_mean)**2).sum(0)
            if count==0:mean=batch_mean;m2=batch_m2
            else:
                delta=batch_mean-mean;total=count+n
                m2+=batch_m2+delta**2*count*n/total;mean+=delta*n/total
            count+=n;seen+=n
        context_count+=seen-current
    if not count:raise ValueError('No training observations')
    std=np.sqrt(m2/count);std=np.where(std>1e-6,std,1.)
    # Preserve explicit masks as 0/1; do not center them into ambiguous values.
    for i,name in enumerate(SCALAR_NAMES):
        if name.endswith(('valid','present','available')) or name in ('premarket','regular','after_hours'):
            mean[i]=0.;std[i]=1.
    for slot in range(10):
        for name in ('role_support','role_resistance','role_transition','historical_origin','present'):
            i=len(SCALAR_NAMES)+slot*len(LEVEL_NAMES)+LEVEL_NAMES.index(name);mean[i]=0.;std[i]=1.
    result=dict(version=VERSION,scope='train_only',dataset_sha256=dataset_sha256,
        training_bank_certificates=certificates,observations=count,mean=mean.tolist(),std=std.tolist(),
        price_reference='OHLC_geometry_and_indicators_close; execution_costs_mid; absolute_close_log_USD_retained')
    if include_context:result.update(version=CANDLE_NORMALIZATION_VERSION,units='bps-v1',context_split_receipts=context_receipts,
        context_observations=context_count,current_observations=count-context_count,execution_observations_added=False)
    return result
