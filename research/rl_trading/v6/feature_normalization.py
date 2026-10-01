"""Training-only streaming normalization for the bps adapter; no new banks."""
import json
from pathlib import Path
import numpy as np
import torch
from research.rl_trading.v6.execution_features import VERSION,bps_input
from research.rl_trading.v6.features import SCALAR_NAMES,LEVEL_NAMES


def fit_normalization(sessions,*,dataset_sha256):
    count=0;mean=None;m2=None;certificates={}
    for session in sessions:
        if session.role!='train':raise ValueError('Normalization must not read development or held-out')
        certificates[str(session.day)]=session.source_certificate_sha256
        for start in range(0,len(session.bank.scalar),65536):
            scalar=torch.from_numpy(np.asarray(session.bank.scalar[start:start+65536]).copy())
            levels=torch.from_numpy(np.asarray(session.bank.levels[start:start+65536]).copy())
            x=bps_input(scalar,levels).numpy().astype(np.float64)
            if not np.isfinite(x).all():raise ValueError('Nonfinite normalized feature source')
            n=len(x);batch_mean=x.mean(0);batch_m2=((x-batch_mean)**2).sum(0)
            if count==0:mean=batch_mean;m2=batch_m2
            else:
                delta=batch_mean-mean;total=count+n
                m2+=batch_m2+delta**2*count*n/total;mean+=delta*n/total
            count+=n
    if not count:raise ValueError('No training observations')
    std=np.sqrt(m2/count);std=np.where(std>1e-6,std,1.)
    # Preserve explicit masks as 0/1; do not center them into ambiguous values.
    for i,name in enumerate(SCALAR_NAMES):
        if name.endswith(('valid','present','available')) or name in ('premarket','regular','after_hours'):
            mean[i]=0.;std[i]=1.
    for slot in range(10):
        for name in ('role_support','role_resistance','role_transition','historical_origin','present'):
            i=len(SCALAR_NAMES)+slot*len(LEVEL_NAMES)+LEVEL_NAMES.index(name);mean[i]=0.;std[i]=1.
    return dict(version=VERSION,scope='train_only',dataset_sha256=dataset_sha256,
        training_bank_certificates=certificates,observations=count,mean=mean.tolist(),std=std.tolist(),
        price_reference='OHLC_geometry_and_indicators_close; execution_costs_mid; absolute_close_log_USD_retained')
