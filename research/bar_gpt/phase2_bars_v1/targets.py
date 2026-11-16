"""V4-compatible normalized bars plus causally anchored future reference changes."""
import torch
from ..v5.pretraining_v1 import PRICES,ACTIVITY
from ..v5.pretraining_v3 import local_references
from ..phase2_v2.tokenizer import normalize

BAR_WIDTH=18*64
REFERENCES=7
OUTPUTS=BAR_WIDTH+REFERENCES
REFERENCE_NAMES=('price','trade_volume','trade_count','bid_size','bid_count','ask_size','ask_count')


def log_references(price,quantity):
    return torch.cat((torch.log(price.reshape(-1,1)),torch.log1p(quantity)),dim=1)


@torch.no_grad()
def from_raw(raw,valid,history=None,history_valid=None,*,_indices=None,
             _future_reference=None,_origin_reference=None):
    if (raw.ndim!=3 or raw.shape[1:]!=(64,18) or not raw.is_floating_point()
        or valid.shape!=raw.shape or valid.dtype!=torch.bool or not len(raw)):
        raise ValueError('Expected matching future/history [B,64,18] values and bool masks')
    checks=[]
    if _future_reference is not None:
        if _future_reference.shape!=(len(raw),7):raise ValueError('Invalid future reference cache shape')
        refs=(_future_reference[:,:1,None],_future_reference[:,1:])
    else:refs=None
    normalized,_,price,quantity=normalize(raw,valid,_indices=_indices,_checks=checks,
        return_raw_references=True,_raw_references=refs)
    if _origin_reference is None:
        if (history is None or history.shape!=raw.shape or not history.is_floating_point()
            or history_valid is None or history_valid.shape!=raw.shape or history_valid.dtype!=torch.bool):
            raise ValueError('Historical source window or bound reference cache required')
        past_price,past_quantity=local_references(history,history_valid,_checks=checks,
            _indices=None if _indices is None else (_indices[0],_indices[2]))
        price_ids,activity_ids=(PRICES,ACTIVITY) if _indices is None else _indices[1:]
        historical_ok=((torch.isfinite(history)|~history_valid).all()
            &~((history[...,price_ids]<=0)&history_valid[...,price_ids]).any()
            &~((history[...,activity_ids]<0)&history_valid[...,activity_ids]).any())
    else:
        if _origin_reference.shape!=(len(raw),7) or _origin_reference.dtype!=torch.float64:
            raise ValueError('Invalid origin source reference cache')
        past_price,past_quantity=_origin_reference[:,:1,None],_origin_reference[:,1:]
        historical_ok=(torch.isfinite(_origin_reference).all()&(past_price>0).all()&(past_quantity>=0).all())
    bars=normalized.transpose(1,2).contiguous()
    anchor=log_references(past_price,past_quantity)
    changes=log_references(price,quantity)-anchor
    changes[:,0]*=100
    ref_valid=torch.cat((torch.ones(len(raw),1,device=raw.device,dtype=torch.bool),
        valid[...,ACTIVITY].any(1)),dim=1)
    ok=torch.stack([c[0] for c in checks]+[historical_ok,
        torch.isfinite(bars).all(),torch.isfinite(changes).all()]).all()
    if not ok:raise ValueError('Invalid future-bar/reference source evidence')
    return dict(bars=bars,valid=valid.transpose(1,2).contiguous(),references=changes.float(),
        reference_valid=ref_valid,raw=raw.detach().transpose(1,2).contiguous(),
        origin_log_reference=anchor,actual_reference=torch.cat((price.reshape(-1,1),quantity),1))


def unpack(predicted):
    if predicted.ndim!=2 or predicted.shape[1]!=OUTPUTS:raise ValueError('Expected [B,1159] predictions')
    return predicted[:,:BAR_WIDTH].reshape(-1,18,64),predicted[:,BAR_WIDTH:]


def reconstruct(predicted,origin_log_reference):
    bars,changes=unpack(predicted)
    if origin_log_reference.shape!=(len(predicted),7):raise ValueError('Expected seven known historical log references')
    logs=origin_log_reference.double()+torch.cat((changes[:,:1].double()/100,changes[:,1:].double()),1)
    result=bars.double().clone()
    result[:,PRICES]=torch.exp(logs[:,:1,None]+result[:,PRICES]/100)
    result[:,ACTIVITY]=torch.expm1(logs[:,1:,None]+result[:,ACTIVITY])
    references=torch.cat((torch.exp(logs[:,:1]),torch.expm1(logs[:,1:])),1)
    return result,references


def loss(predicted,target,ids=None):
    bars,refs=unpack(predicted)
    bmask,rmask=target['valid'],target['reference_valid']
    be=(bars.float()-target['bars'].detach().float()).square()
    re=(refs.float()-target['references'].detach().float()).square()
    return ((be*bmask).sum()/bmask.sum().clamp_min(1)
        +(re*rmask).sum()/rmask.sum().clamp_min(1))
