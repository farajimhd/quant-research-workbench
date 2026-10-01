"""Chronological, clock-chunked classification-only ResNet control updates."""
import numpy as np
import torch
from research.rl_trading.v6.ticker_heads import ACTION_NAMES


def run_epoch(model, windows, present, decisions, *, device, optimizer=None,
              batch_size=8, clocks_per_chunk=32, origin_us=None, balance=None,
              loss_denominator=None):
    """One update per chronological BPTT-sized clock chunk, not per minibatch.

    ResNet has no recurrent graph. Minibatches accumulate the same weighted
    chunk objective before one Adam update. Legacy chunk mean or the supplied
    fixed-session balanced denominator matches the V6 teacher convention.
    Return per-example probabilities for downstream error/calibration audits.
    """
    if (not decisions or len(windows)!=len(decisions) or len(present)!=len(decisions)
        or batch_size<1 or clocks_per_chunk<1):
        raise ValueError('Invalid ResNet epoch inputs')
    if balance is not None and (loss_denominator is None or loss_denominator<=0):
        raise ValueError('Balanced loss requires fixed session denominator')
    clocks=np.array([x.close_us for x in decisions],np.int64)
    if np.any(clocks[1:]<clocks[:-1]):raise ValueError('ResNet labels not chronological')
    origin=clocks[0] if origin_us is None else origin_us
    groups=(clocks-origin)//(1_000_000*clocks_per_chunk)
    if (groups<0).any():raise ValueError('Label before diagnostic origin')
    bounds=np.r_[0,np.flatnonzero(groups[1:]!=groups[:-1])+1,len(decisions)]
    predictions=np.zeros((len(decisions),4),np.float32)
    truth=np.zeros(len(decisions),np.int64)
    confusion=np.zeros((4,4),np.int64);loss_sum=0.;updates=0
    model.train(optimizer is not None)
    for left,right in zip(bounds[:-1],bounds[1:]):
        if optimizer is not None:optimizer.zero_grad(set_to_none=True)
        denominator=loss_denominator if balance is not None else sum(x.sample_weight for x in decisions[left:right])
        for start in range(left,right,batch_size):
            end=min(start+batch_size,right);items=decisions[start:end]
            account=[];held=[];features=[];cost=[];prob=[];weights=[];hard=[]
            for item in items:
                holding=bool(len(item.held_index));held.append(holding)
                identity=int(item.held_index[0]) if holding else item.soft_tokens[1]-1
                if item.execution_features is None:raise ValueError('ResNet execution evidence missing')
                match=np.flatnonzero(item.execution_indices==identity)
                if len(match)!=1:raise ValueError('ResNet execution identity missing/duplicated')
                cost.append(item.execution_features[match[0]])
                account.append(item.account)
                features.append(item.held_features[0] if holding else np.zeros(11,np.float32))
                p=item.soft_probabilities[0] if holding else item.soft_probabilities[1]
                prob.append([0,0,1-p,p] if holding else [p,1-p,0,0])
                target=(3 if p>=.5 else 2) if holding else (0 if p>=.5 else 1)
                hard.append(target)
                factor=balance[(1,0,5,2)[target]] if balance is not None else 1.
                weights.append(item.sample_weight*factor)
            with torch.set_grad_enabled(optimizer is not None):
                tensor=lambda x,dtype=torch.float32:torch.as_tensor(np.asarray(x),device=device,dtype=dtype)
                out=model(tensor(windows[start:end]),tensor(present[start:end],torch.bool),
                    tensor(account),tensor(held,torch.bool),tensor(features),tensor(cost))
                allowed=torch.isfinite(out.logits)
                per_row=-(tensor(prob)*out.logits.log_softmax(-1).masked_fill(~allowed,0)).sum(-1)
                objective=(per_row*tensor(weights)).sum()/denominator
                if not torch.isfinite(objective):raise ValueError('Nonfinite ResNet objective')
                if optimizer is not None:objective.backward()
            values=out.logits.detach().softmax(-1).cpu().numpy()
            predictions[start:end]=values;truth[start:end]=hard
            np.add.at(confusion,(hard,values.argmax(-1)),1)
            loss_sum+=float(per_row.detach().sum())
        if optimizer is not None:
            torch.nn.utils.clip_grad_norm_(model.parameters(),1.)
            optimizer.step();updates+=1
    counts=confusion.sum(1);predicted=confusion.sum(0)
    precision=np.divide(confusion.diagonal(),predicted,out=np.zeros(4),where=predicted>0)
    recall=np.divide(confusion.diagonal(),counts,out=np.zeros(4),where=counts>0)
    f1=np.divide(2*precision*recall,precision+recall,out=np.zeros(4),where=(precision+recall)>0)
    return dict(decisions=len(decisions),optimizer_steps=updates,mean_loss=loss_sum/len(decisions),
        counts=dict(zip(ACTION_NAMES,counts.tolist())),precision=dict(zip(ACTION_NAMES,precision.tolist())),
        recall=dict(zip(ACTION_NAMES,recall.tolist())),f1=dict(zip(ACTION_NAMES,f1.tolist()))),predictions,truth
