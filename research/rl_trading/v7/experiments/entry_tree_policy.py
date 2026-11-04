"""Fixed tabular payoff-weighted classifier; supervised counterfactuals, not PPO."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
for _key in ('OMP_NUM_THREADS','MKL_NUM_THREADS','OPENBLAS_NUM_THREADS'):os.environ[_key]='4'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
import argparse
import json
import pickle
import platform
import subprocess
import time
import numpy as np
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits
from research.rl_trading.v7.experiments import entry_net
from research.rl_trading.v7.experiments import temporal_weighted_policy as metrics_helper
shared=entry_net.shared
PARAMETERS=dict(max_iter=200,max_leaf_nodes=15,min_samples_leaf=100,l2_regularization=10,
                learning_rate=.05,early_stopping=False,random_state=1917)


def sources():
    return dict(shared.implementation(),entry_net=shared.sha(Path(entry_net.__file__)),
        greedy_metrics=shared.sha(Path(metrics_helper.__file__)),entry_tree_policy=shared.sha(Path(__file__)))


def versions():
    return dict(python=platform.python_version(),numpy=np.__version__,sklearn=sklearn.__version__)


def fit_model(x,net):
    x,net=np.asarray(x),np.asarray(net)
    if x.ndim!=2 or x.shape[1]!=120 or net.shape!=(len(x),) or not np.isfinite(x).all() or not np.isfinite(net).all():
        raise ValueError('Finite causal120 features and separate known net outcomes required')
    if set((net[net!=0]>0).tolist())!={False,True}:
        raise ValueError('Positive-weight examples of both payoff signs required')
    model=HistGradientBoostingClassifier(**PARAMETERS)
    with threadpool_limits(limits=4):model.fit(x,net>0,sample_weight=np.abs(net)/10)
    return model


def scores(model,features):
    shape=features.shape[:-1]
    with threadpool_limits(limits=4):result=model.decision_function(features.reshape(-1,120))*10
    if not np.isfinite(result).all():raise ValueError('Nonfinite tree policy score')
    return result.reshape(shape)


class Scorer:
    row_step=10
    def __init__(self,model,selected,binding,days):
        self.model,self.selected,self.binding,self.days=model,selected,binding,days

    def for_session(self,session):
        if session.day not in self.days:raise ValueError('Only approved TRAINING replay dates')
        return self

    def score(self,observations):
        batch=shared.observation_batch(observations)
        value=scores(self.model,shared.feature_matrix(batch,'raw')) if self.selected else np.zeros(batch['candidate_valid'].shape)
        return np.where(batch['candidate_valid'],value,-np.inf)


def load_bundle(root,dataset,forecast=None,*,device='cpu'):
    # forecast and device are compatibility arguments; no forecast file is opened.
    root=Path(root).resolve()
    if not root.is_relative_to(Path('D:/TradingML/runtimes').resolve()):raise ValueError('Trusted local runtime bundle required')
    seal=json.loads((root/'complete.json').read_text())
    if set(seal['files'])!={'manifest.json','model.pkl','metrics.json'}:raise ValueError('Tree seal file set mismatch')
    for name,value in seal['files'].items():
        if shared.sha(root/name)!=value:raise ValueError('Tree artifact hash mismatch')
    manifest=json.loads((root/'manifest.json').read_text());c,h=manifest['contract'],manifest['contract_hash']
    if (h!=shared.digest(c) or seal['contract_hash']!=h or c['source']!=sources() or c['versions']!=versions() or
            c['base_source']!=shared.source_identity() or
            c['training_binding']!=dataset.binding or c['risk']!=shared.Config().manifest() or
            c['fit_days']!=dataset.days[:120] or c['development_days']!=dataset.days[120:] or
            len(dataset.days)!=150 or c['parameters']!=PARAMETERS or c['validation_access'] is not False):
        raise ValueError('Tree source/version/data/split contract mismatch')
    # Only locally generated, hash-verified pickle is trusted. No arbitrary downloads.
    with (root/'model.pkl').open('rb') as stream:model=pickle.load(stream)
    if not isinstance(model,HistGradientBoostingClassifier) or model.n_features_in_!=120:
        raise ValueError('Unexpected tree estimator schema')
    metrics=json.loads((root/'metrics.json').read_text())
    selected=metrics['selected_candidate']
    if selected is not (metrics['development']['greedy_net_per_decision']>0):raise ValueError('Tree selection receipt mismatch')
    return Scorer(model,selected,dict(root=str(root),contract=c,contract_hash=h,complete_sha256=shared.sha(root/'complete.json'),
        model_sha256=seal['files']['model.pkl'],selected_candidate=selected),dataset.days)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',default=shared.DEFAULT_DEST)
    p.add_argument('--snapshots',default='D:/TradingML/runtimes/rl-trading/v7/predictive-data-v1')
    p.add_argument('--labels',default='D:/TradingML/runtimes/rl-trading/v7/entry-opportunity-full150')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--resume',action='store_true')
    args=p.parse_args(argv);root=args.output.resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):raise ValueError('Available runtime output required')
    data=shared.PredictiveDataset(args.source,args.snapshots);labels=shared.EntryLabels(data,args.labels)
    contract=dict(source=sources(),base_source=shared.source_identity(),versions=versions(),training_binding=data.source.binding,snapshots_binding=data.binding,
        label_contract=labels.contract,label_receipts=labels.receipts,label_manifest_sha256=shared.sha(labels.root/'manifest.json'),
        fit_days=data.fit_days,development_days=data.development_days,risk=shared.Config().manifest(),parameters=PARAMETERS,
        row_step=10,threads=4,input='raw112 causal candidate features + account8; no ordered bars',
        target='net>0',weight='abs(net)/10; known zero outcomes retained with zero weight',
        score='decision_function *10; binary policy logits, NOT net-dollar estimates',forecast_reuse=False,
        selection='fixed final candidate only; strict greedy development net/decision > WAIT0, else publish WAIT',
        representation_caveat='comparison changes representation as well as learner; no sequential bar input',
        validation_access=False,evidence='overlapping standalone counterfactual outcomes, NOT account profit',
        roots={k:str(Path(getattr(args,k)).resolve()) for k in ('source','snapshots','labels','output')})
    h=shared.digest(contract)
    if args.resume:
        previous=json.loads((root/'manifest.json').read_text())
        if previous['contract']!=contract or previous['contract_hash']!=h:raise ValueError('Exact tree resume contract mismatch')
        load_bundle(root,data.source)
        shared.write_json(root/'status.json',dict(status='complete',completed=150,queued=0,failed=0,resumed_completed=True))
        return 0
    root.mkdir(parents=True,exist_ok=False)
    shared.write_json(root/'manifest.json',dict(contract=contract,contract_hash=h,git_commit=subprocess.check_output(
        ['git','rev-parse','HEAD'],cwd=Path(__file__).resolve().parents[4],text=True).strip()))
    started=time.perf_counter()
    try:
        shared.write_json(root/'status.json',dict(status='materializing',completed=0,queued=150,failed=0))
        obs,y,mask,days=entry_net.materialize(data,labels,lambda n:shared.write_json(root/'status.json',
            dict(status='materializing',completed=n,queued=150-n,failed=0)))
        x=shared.feature_matrix(obs,'raw');fit=np.isin(days,data.fit_days);dev=np.isin(days,data.development_days)
        if not fit.any() or not dev.any() or np.any(fit==dev):raise ValueError('Invalid fit/internal-development split')
        shared.write_json(root/'status.json',dict(status='fitting',completed=150,queued=0,failed=0,
            fit_eligible=int(mask[fit].sum()),development_eligible=int(mask[dev].sum()),iterations=200))
        model=fit_model(x[fit][mask[fit]],y[fit][mask[fit]])
        prediction=scores(model,x)
        metrics={name:metrics_helper.policy_metrics(prediction[part],y[part].astype(np.float64),mask[part],days[part])
                 for name,part in (('fit',fit),('development',dev))}
        metrics.update(selected_candidate=metrics['development']['greedy_net_per_decision']>0,
            wait_baseline_net_per_decision=0.,fit_eligible=int(mask[fit].sum()),development_eligible=int(mask[dev].sum()),
            fit_zero_weight_count=int(((y[fit]==0)&mask[fit]).sum()),
            development_zero_weight_count=int(((y[dev]==0)&mask[dev]).sum()),
            causal_ineligible_count=int((~mask).sum()),elapsed_seconds=time.perf_counter()-started)
        temporary=root/'model.partial'
        with temporary.open('wb') as stream:pickle.dump(model,stream,protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary,root/'model.pkl')
        shared.write_json(root/'metrics.json',metrics)
        shared.write_json(root/'complete.json',dict(contract_hash=h,files={name:shared.sha(root/name)
            for name in ('manifest.json','model.pkl','metrics.json')}))
        load_bundle(root,data.source)
        shared.write_json(root/'status.json',dict(status='complete',completed=150,queued=0,failed=0,
            selected_candidate=metrics['selected_candidate'],validation_access=False))
        print(json.dumps({name:({k:v for k,v in value.items() if k!='by_day'} if isinstance(value,dict) else value)
                          for name,value in metrics.items()}),flush=True)
    except BaseException as exc:
        shared.write_json(root/'status.json',dict(status='failed',error=repr(exc),failed=1))
        raise
    return 0


if __name__=='__main__':raise SystemExit(main())
