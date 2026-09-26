"""Teacher-action and return supervision; no hindsight values enter observations."""
import torch
from torch.nn import functional as F


def active_orders(target):
    """A stop action is supervised once; later order slots are padding."""
    return target.eq(0).to(torch.int32).cumsum(dim=1).le(1)


def classification_metrics(confusion, exact_by_class):
    """Aggregate counts before computing rates so large batches do not skew F1."""
    matrix = confusion.detach().cpu().tolist()
    exact = exact_by_class.detach().cpu().tolist()
    names = ('stop', 'buy', 'sell')
    total = sum(sum(row) for row in matrix)
    result = {'action_accuracy':sum(exact)/max(1,total),
        'trade_recall':(exact[1]+exact[2])/max(1,sum(matrix[1])+sum(matrix[2])),
        'active_orders':total}
    recalls = []
    f1s = []
    for index,name in enumerate(names):
        support = sum(matrix[index])
        predicted = sum(row[index] for row in matrix)
        true_positive = matrix[index][index]
        precision = true_positive/max(1,predicted)
        recall = true_positive/max(1,support)
        f1 = 2*precision*recall/max(1e-12,precision+recall)
        result.update({f'{name}_support':support,f'{name}_predicted':predicted,
            f'{name}_precision':precision,f'{name}_recall':recall,f'{name}_f1':f1,
            f'{name}_exact_recall':exact[index]/max(1,support)})
        if support:
            recalls.append(recall)
            f1s.append(f1)
    result['balanced_accuracy'] = sum(recalls)/max(1,len(recalls))
    result['macro_f1'] = sum(f1s)/max(1,len(f1s))
    return result


def teacher_loss(logits, value, batch, *, trade_weight: float = 1., value_weight: float = .1):
    if trade_weight < 1 or value_weight < 0:
        raise ValueError('Loss weights are invalid')
    target = batch['actions']
    mask = batch['action_mask']
    if logits.shape != mask.shape or target.shape != logits.shape[:2]:
        raise ValueError('Teacher action and feasible-mask shapes differ')
    if not torch.gather(mask,2,target.unsqueeze(-1)).all():
        raise ValueError('Teacher target is outside the causal action mask')
    protected = logits.float().masked_fill(~mask,-1e9)
    per_order = F.cross_entropy(protected.flatten(0,1),target.flatten(),reduction='none').reshape_as(target)
    active = active_orders(target)
    weights = torch.where(target != 0,trade_weight,1.)*active
    action = (per_order*weights).sum()/weights.sum()
    returns = F.smooth_l1_loss(value.float(),batch['return_to_go'].float())
    total = action+value_weight*returns
    prediction = protected.argmax(dim=-1)
    top_n = batch['ticker_id'].shape[1]
    def kinds(tokens):
        return torch.where(tokens == 0,0,torch.where(tokens <= top_n,1,2))
    truth_class = kinds(target)
    prediction_class = kinds(prediction)
    confusion = torch.bincount((truth_class[active]*3+prediction_class[active]).long(),
        minlength=9).reshape(3,3)
    exact_by_class = torch.bincount(truth_class[active & (prediction == target)].long(),
        minlength=3)
    return total,dict(action_loss=action.detach(),value_loss=returns.detach(),
        class_confusion=confusion.detach(),exact_by_class=exact_by_class.detach(),
        action_accuracy=(prediction.eq(target) & active).sum().float().detach()/active.sum(),
        trade_recall=(prediction.eq(target) & target.ne(0)).sum().float().detach()
            /target.ne(0).sum().clamp_min(1))
