import torch
from .diagnose_inheritance import paired_sums,FIELDS


def test_paired_signed_errors_and_masks():
    last=torch.full((2,18),10.,dtype=torch.float64);raw=last[:,None].expand(-1,64,-1).clone();raw[0,0]=11.
    target=dict(last_raw=last,raw=raw,last_valid=torch.ones_like(last,dtype=torch.bool),valid=torch.ones_like(raw,dtype=torch.bool))
    target['last_valid'][1,9]=False
    rec=last[:,None].expand(-1,64,-1).log();pred=raw.log()
    s=paired_sums(rec,pred,target)
    assert s[:,0].tolist()==[2,1,2]
    for field in ('phase1_absolute_pct','phase2_absolute_pct','phase1_raw_mae','phase2_raw_mae'):
        torch.testing.assert_close(s[:,FIELDS.index(field)],torch.zeros(3,dtype=torch.float64),atol=1e-12,rtol=0)
    assert s[:,FIELDS.index('actual_up')].tolist()==[1,1,1]
    assert s[:,FIELDS.index('actual_flat')].tolist()==[1,0,1]
