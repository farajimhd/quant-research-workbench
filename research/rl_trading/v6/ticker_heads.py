"""Independent ticker supervision; portfolio sizing/critic are deliberately absent.

Inputs are causal representations. Future episode outcomes occur only in
targets. Class order and physical output units are a checkpoint contract.
"""
from dataclasses import dataclass
import torch
from torch import nn
from torch.nn import functional as F

VERSION = 'rl-v6-ticker-four-action-netbps-brackets-v1'
ACTION_NAMES = ('entry', 'wait', 'hold', 'exit')


@dataclass
class TickerOutputs:
    logits: torch.Tensor  # [...,4], raw masked logits, never pre-softmax.
    value_bps: torch.Tensor  # [...], signed opportunity return; not PPO V(s).
    stop_bps: torch.Tensor  # [...], positive entry-relative distance.
    target_bps: torch.Tensor  # [...], positive entry-relative distance.


class TickerHeads(nn.Module):
    """[...,D] -> independent per-ticker outputs; no ticker/time reductions.

    Position state chooses the valid action pair. Stop magnitudes remain below
    10,000 bps, ensuring a positive long stop price. Distances are attached to
    entry intents, not sampled as separate SET_STOP/SET_TARGET actions.
    """
    def __init__(self, width):
        super().__init__()
        self.width = width
        self.action = nn.Linear(width, 4)
        self.value = nn.Linear(width, 1)
        self.stop = nn.Linear(width, 1)
        self.target = nn.Linear(width, 1)
        # Initialize bracket magnitudes near 100 bps, not a 50% stop.
        nn.init.constant_(self.stop.bias, -4.59502)
        nn.init.constant_(self.target.bias, 100.)

    def forward(self, representation, held):
        if representation.shape[-1] != self.width or held.shape != representation.shape[:-1] or held.dtype != torch.bool:
            raise ValueError('Ticker representation/position shape mismatch')
        features = F.layer_norm(representation, (self.width,))
        allowed = torch.stack((~held, ~held, held, held), -1)
        logits = self.action(features).masked_fill(~allowed, -torch.inf)
        return TickerOutputs(logits, self.value(features).squeeze(-1),
            self.stop(features).squeeze(-1).sigmoid()*9999.,
            F.softplus(self.target(features).squeeze(-1)))


def supervised_loss(outputs, probabilities, *, value_bps, value_valid,
                    stop_bps, target_bps, bracket_valid, weight=None):
    """Soft CE from logits plus independently masked bps regressions.

    Every regression target has an explicit availability bit. WAIT examples
    cannot supervise value/brackets. NaNs in masked targets are never read.
    Each task normalizes its own valid label mass; no dilution by missing rows.
    Regression is preconditioned per 100 bps; reported errors stay in bps.
    """
    shape = outputs.value_bps.shape
    if probabilities.shape != (*shape, 4) or any(t.shape != shape for t in
            (value_bps, value_valid, stop_bps, target_bps, bracket_valid)):
        raise ValueError('Ticker target shape mismatch')
    if value_valid.dtype != torch.bool or bracket_valid.dtype != torch.bool:
        raise ValueError('Target availability must be boolean')
    if not torch.isfinite(probabilities).all() or (probabilities < 0).any() or not torch.allclose(probabilities.sum(-1), torch.ones_like(value_bps)):
        raise ValueError('Invalid soft action target')
    allowed = torch.isfinite(outputs.logits)
    if (probabilities.masked_select(~allowed) != 0).any():
        raise ValueError('Action target contradicts position state')
    hard = probabilities.argmax(-1)
    if (value_valid & (hard == 1)).any() or (bracket_valid & (hard != 0)).any():
        raise ValueError('WAIT value or non-entry bracket supervision')
    weight = torch.ones_like(value_bps) if weight is None else weight
    if weight.shape != shape or not torch.isfinite(weight).all() or (weight <= 0).any():
        raise ValueError('Invalid ticker sample weight')
    logp = F.log_softmax(outputs.logits, -1)
    action = -(probabilities*logp.masked_fill(~allowed, 0)).sum(-1)
    action_loss = (action*weight).sum()/weight.sum()

    def regression(prediction, target, valid, positive=False):
        selected = target[valid]
        if not torch.isfinite(selected).all() or (positive and (selected <= 0).any()):
            raise ValueError('Invalid available regression target')
        if not valid.any():
            return prediction.sum()*0., prediction.new_tensor(float('nan'))
        loss = F.smooth_l1_loss(prediction[valid]/100, selected/100, reduction='none')
        mass = weight[valid]
        return (loss*mass).sum()/mass.sum(), ((prediction[valid]-selected).abs()*mass).sum()/mass.sum()

    value_loss, value_mae = regression(outputs.value_bps, value_bps, value_valid)
    if (stop_bps[bracket_valid] >= 10000).any():
        raise ValueError('Long stop must be above zero')
    stop_loss, stop_mae = regression(outputs.stop_bps, stop_bps, bracket_valid, True)
    target_loss, target_mae = regression(outputs.target_bps, target_bps, bracket_valid, True)
    return action_loss+value_loss+stop_loss+target_loss, dict(action_loss=action_loss.detach(),
        value_loss=value_loss.detach(), stop_loss=stop_loss.detach(), target_loss=target_loss.detach(),
        value_mae_bps=value_mae.detach(), stop_mae_bps=stop_mae.detach(), target_mae_bps=target_mae.detach())


class TickerDecoder(nn.Module):
    """Four local classes mapped to the existing identity transport axis.

    Legacy STOP/TARGET transport slots are permanently unavailable, preserving
    shared replay identity decoding without making them policy actions. Entry
    and exit margins are relative to their ticker's WAIT/HOLD logit. Portfolio
    categorical selection is separate from the local teacher cross-entropy.
    """
    def __init__(self, width):
        super().__init__()
        self.width=width;self.wait_hold=True;self.action_version=VERSION
        self.heads=TickerHeads(width)
        self.account=nn.Sequential(nn.Linear(7,width),nn.LayerNorm(width))
        self.holding=nn.Sequential(nn.Linear(11,width),nn.LayerNorm(width))
        self.size_head=nn.Linear(width,1)  # PPO only; absent from teacher loss.
        self.ticker_outputs=None
        self.supervision_index=None

    def forward_batch(self,listings,account,held_index,held_features,*,
                      enter_allowed,exit_allowed,stop_allowed,target_allowed):
        b,n,d=listings.shape;h=held_index.shape[1]
        if held_features.shape==(b,h,9):held_features=F.pad(held_features,(0,2))
        if d!=self.width or account.shape!=(b,7) or held_features.shape!=(b,h,11):
            raise ValueError('Ticker decoder observation shape mismatch')
        scaled=account.sign()*torch.log1p(account.abs())
        context=listings+self.account(scaled)[:,None]
        features=held_features.clone()
        features[:,:,:3]=torch.sign(features[:,:,:3])*torch.log1p(features[:,:,:3].abs())
        features[:,:,3:6]*=10  # Returns -> physical bps preconditioned per 1,000.
        if h:context=context.scatter_add(1,held_index[:,:,None].expand(-1,-1,d),self.holding(features))
        held=torch.zeros((b,n),dtype=torch.bool,device=listings.device)
        held.scatter_(1,held_index,True)
        out=self.heads(context,held);self.ticker_outputs=out
        # Global no-order baseline; per-ticker probabilities are learned from
        # local logits. A proposal race samples one identity per policy second.
        entry=(out.logits[:,:,0]-out.logits[:,:,1]).masked_fill(~enter_allowed,-torch.inf)
        held_logits=out.logits.gather(1,held_index[:,:,None].expand(-1,-1,4))
        exit_=(held_logits[:,:,3]-held_logits[:,:,2]).masked_fill(~exit_allowed,-torch.inf)
        absent=listings.new_full((b,h),-torch.inf)
        def race(margins):
            # Total class mass is exp(best local margin), not the sum of all
            # tickers' odds. Conditional identity probabilities still learn.
            # Never take logsumexp over all -inf: that has undefined gradients.
            if not margins.shape[1]:return margins
            any_valid=torch.isfinite(margins).any(1,keepdim=True)
            safe=torch.where(any_valid,margins,torch.zeros_like(margins))
            normalized=safe-torch.logsumexp(safe,1,keepdim=True)+safe.max(1,keepdim=True).values
            return normalized.masked_fill(~any_valid,-torch.inf)
        logits=torch.cat((listings.new_zeros(b,1),race(entry),race(exit_),absent,absent,absent),1)
        return logits,self.size_head(context).squeeze(-1).sigmoid(),listings.new_zeros(b,h),listings.new_zeros(b,h)

    def forward(self,listings,account,held_index,held_features,**masks):
        if self.supervision_index is not None:
            # Teacher supervises one identity per hypothetical branch. Its
            # local head has no reduction across N: gather BEFORE projection
            # rather than recomputing every ticker for each label. Preserve
            # the full transport shape without using it as a teacher loss.
            i=self.supervision_index;n=len(listings);h=len(held_index)
            if not 0 <= i < n or h > 1 or (h and int(held_index[0]) != i):
                raise ValueError('Teacher local identity must match its single held branch')
            context=listings[i:i+1]+self.account(account.sign()*torch.log1p(account.abs()))[None]
            if h:
                features=held_features.clone()
                if features.shape[1]==9:features=F.pad(features,(0,2))
                features[:,:3]=features[:,:3].sign()*torch.log1p(features[:,:3].abs())
                features[:,3:6]*=10
                context=context+self.holding(features)
            out=self.heads(context,torch.full((1,),bool(h),device=listings.device,dtype=torch.bool))
            self.ticker_outputs=out
            logits=listings.new_full((1+n+4*h,),-torch.inf);logits[0]=0
            if h:
                logits[1+n]=(out.logits[0,3]-out.logits[0,2]).masked_fill(~masks['exit_allowed'][0],-torch.inf)
                logits[1+n+3*h]=0
            else:logits[1+i]=(out.logits[0,0]-out.logits[0,1]).masked_fill(~masks['enter_allowed'][i],-torch.inf)
            return logits,listings.new_zeros(n),listings.new_zeros(h),listings.new_zeros(h)
        result=self.forward_batch(listings[None],account[None],held_index[None],held_features[None],
            **{k:v[None] for k,v in masks.items()})
        out=self.ticker_outputs
        self.ticker_outputs=TickerOutputs(*(getattr(out,k).squeeze(0) for k in
            ('logits','value_bps','stop_bps','target_bps')))
        return tuple(x.squeeze(0) for x in result)
