"""Training-only range-bin weights; no new model inputs or decoder state."""
import torch
from .objectives import range_edges


def bins(target):
    return torch.where(target==0,0,1+torch.bucketize(target.contiguous(),range_edges(target.device),right=False))


def weights_from_counts(counts):
    c=counts.double()
    if c.shape!=(3,6) or not torch.isfinite(c).all() or (c<0).any() or (c.sum(-1)==0).any():raise ValueError('Range counts')
    w=torch.where(c>0,c.clamp_min(1).rsqrt(),0)
    return w/(w*c).sum(-1,keepdim=True)*c.sum(-1,keepdim=True)


@torch.no_grad()
def calibrate(raw,valid):
    counts=torch.zeros(3,6,device=raw.device,dtype=torch.float64)
    for i in range(0,len(raw),2048):
        r=raw[i:i+2048];v=valid[i:i+2048]
        for family,b in enumerate((0,6,12)):
            m=v[...,b+1]&v[...,b+2]
            t=torch.log(torch.where(m,r[...,b+1].double(),1)/torch.where(m,r[...,b+2].double(),1))
            if (m&(t<0)).any():raise ValueError('Negative source range')
            counts[family]+=torch.bincount(bins(t)[m],minlength=6)
    return dict(counts=counts.cpu().tolist(),weights=weights_from_counts(counts).cpu().tolist(),
        scope='Training pool only; per-family inverse-sqrt counts normalized to mean weight1. Empty bins receive zero weight; no valid training samples discarded.')


def attach(model,calibration):
    counts=torch.tensor(calibration['counts'],dtype=torch.float64)
    w=weights_from_counts(counts)
    torch.testing.assert_close(w,torch.tensor(calibration['weights'],dtype=torch.float64),rtol=1e-12,atol=1e-12)
    model.register_buffer('range_bin_weights',w.to(next(model.parameters()).device),persistent=False)
