"""Natural-frequency binary action diagnostics and calibration-only thresholds."""
import numpy as np
from sklearn.metrics import average_precision_score,precision_recall_curve


def binary_report(target,probability,threshold=.5):
    target=np.asarray(target,bool);p=np.asarray(probability,float)
    if target.shape!=p.shape or not np.isfinite(p).all() or ((p<0)|(p>1)).any() or not np.isfinite(threshold) or not 0<=threshold<=1:raise ValueError('Invalid binary diagnostic')
    predicted=p>=threshold;tp=int((target&predicted).sum());fp=int((~target&predicted).sum());fn=int((target&~predicted).sum())
    precision=tp/(tp+fp) if tp+fp else 0.;recall=tp/(tp+fn) if tp+fn else 0.
    return dict(count=len(p),positive_count=int(target.sum()),predicted_positive=int(predicted.sum()),
        prevalence=float(target.mean()) if len(p) else None,
        brier_score=float(np.square(p-target).mean()) if len(p) else None,
        mean_positive_probability=float(p.mean()) if len(p) else None,
        average_precision=float(average_precision_score(target,p)) if target.any() else None,
        precision=precision,recall=recall,f1=2*precision*recall/(precision+recall) if precision+recall else 0.,
        threshold=float(threshold),positive_probability_quantiles=np.quantile(p[target],[0,.25,.5,.75,1]).tolist() if target.any() else [],
        negative_probability_quantiles=np.quantile(p[~target],[0,.25,.5,.75,1]).tolist() if (~target).any() else [],
        confusion=dict(tp=tp,fp=fp,fn=fn,tn=int((~target&~predicted).sum())))


def action_report(actions,probability,thresholds=(.5,.5)):
    actions=np.asarray(actions);p=np.asarray(probability)
    flat=actions<2;held=~flat
    return dict(ENTRY=binary_report(actions[flat]==0,p[flat],thresholds[0]),
                EXIT=binary_report(actions[held]==3,p[held],thresholds[1]))


def calibration_thresholds(actions,probability,*,minimum_precision=.25,minimum_positives=10):
    """Fit only on the explicitly reserved chronological calibration fold."""
    result=[]
    for mask,positive in ((actions<2,0),(actions>=2,3)):
        y=actions[mask]==positive;p=probability[mask]
        if y.sum()<minimum_positives or (~y).sum()<minimum_positives:
            result.append(.5);continue
        precision,recall,threshold=precision_recall_curve(y,p)
        f1=2*precision[:-1]*recall[:-1]/np.maximum(precision[:-1]+recall[:-1],1e-12)
        valid=precision[:-1]>=minimum_precision
        if not valid.any():result.append(.5);continue
        f1[~valid]=-1
        result.append(float(threshold[int(np.argmax(f1))]))
    return tuple(result)


def fit_probability_calibration(actions,probability,*,minimum_positives=10):
    """Monotone Platt fit on calibration only, preserving ranking and priors."""
    from scipy.optimize import minimize
    result=[]
    for mask,positive in ((actions<2,0),(actions>=2,3)):
        y=(actions[mask]==positive).astype(float);p=probability[mask]
        if y.sum()<minimum_positives or (1-y).sum()<minimum_positives:
            result.append(dict(scale=1.,bias=0.,status='insufficient_calibration_support'));continue
        clipped=np.clip(p,1e-6,1-1e-6);logit=np.log(clipped/(1-clipped))
        def objective(parameters):
            score=parameters[0]*logit+parameters[1]
            return float((np.logaddexp(0,score)-y*score).mean()+.001*parameters[0]**2)
        fit=minimize(objective,[1.,0.],bounds=((0.,10.),(-20.,20.)),method='L-BFGS-B')
        if not fit.success:raise ValueError('Probability calibration failed')
        result.append(dict(scale=float(fit.x[0]),bias=float(fit.x[1]),status='calibration_only_monotone_platt'))
    return result


def calibrated_probability(actions,probability,parameters):
    from scipy.special import expit
    result=np.asarray(probability,float).copy()
    for mask,fit in zip((actions<2,actions>=2),parameters):
        p=np.clip(result[mask],1e-6,1-1e-6)
        result[mask]=expit(fit['scale']*np.log(p/(1-p))+fit['bias'])
    return result
