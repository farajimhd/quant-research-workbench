"""Fixed HGB raw-plus-ordered-statistics comparison; no account-profit claim."""
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
from research.rl_trading.v7.experiments import entry_tree_policy as raw_tree
from research.rl_trading.v7.experiments import temporal_net
from research.rl_trading.v7.experiments.temporal_data import TemporalDataset, LENGTHS, SCALES
from research.rl_trading.v7.experiments import temporal_weighted_policy as metrics_helper
shared=entry_net.shared
PARAMETERS=raw_tree.PARAMETERS
versions=raw_tree.versions
STAT_NAMES=['close_slope','close_location','consecutive_price_rms','direction_persistence',
            'activity_recent_minus_old','count_recent_minus_old']


def ordered_statistics(seq):
    """Fixed native scheduled positions; invalid fields never become observations."""
    seq=np.asarray(seq)
    if seq.shape[-3:]!=(7,60,14) or not np.isfinite(seq).all() or not np.isin(seq[...,7:],[0,1]).all():
        raise ValueError('Invalid ordered sequence schema')
    result=np.zeros((*seq.shape[:-3],7,12),np.float64)
    def mean(value,valid):
        n=valid.sum(-1)
        return np.where(valid,value,0).sum(-1)/np.maximum(n,1),n
    for group,length in enumerate(LENGTHS):
        native=seq[... ,group,-length:,:].astype(np.float64)
        value=native[...,:7].copy();value[...,:4]/=SCALES[group]
        valid=native[...,7:].astype(bool);close=value[...,3];cm=valid[...,3]
        u=np.arange(length,dtype=np.float64)/(length-1)
        um,n=mean(u,cm);center,_=mean(close,cm)
        den=np.where(cm,(u-um[...,None])**2,0).sum(-1)
        out=result[...,group,:6];mask=result[...,group,6:]
        mask[...,0]=(n>=2)&(den>0)
        out[...,0]=np.where(cm,(u-um[...,None])*(close-center[...,None]),0).sum(-1)/np.where(den>0,den,1)
        paired=valid[...,1]&valid[...,2]&(value[...,1]>=value[...,2])
        high=np.where(paired,value[...,1],-np.inf).max(-1)
        low=np.where(paired,value[...,2],np.inf).min(-1)
        last=np.maximum(np.where(cm,np.arange(length),-1).max(-1),0)
        final=np.take_along_axis(close,last[...,None],axis=-1)[...,0]
        ok=paired.any(-1)&(n>0)&(high>low)
        mask[...,1]=ok
        out[...,1]=(final-np.where(ok,low,0))/np.where(ok,high-low,1)
        change=np.diff(close,axis=-1);adjacent=cm[...,1:]&cm[...,:-1]
        variance,pairs=mean(change**2,adjacent)
        out[...,2]=np.sqrt(variance);mask[...,2]=pairs>0
        persistence,triples=mean(np.sign(change[...,1:])*np.sign(change[...,:-1]),adjacent[...,1:]&adjacent[...,:-1])
        out[...,3]=persistence;mask[...,3]=triples>0
        half=length//2
        for field,index in ((4,4),(5,5)):
            older,no=mean(value[...,:half,field],valid[...,:half,field])
            recent,nr=mean(value[...,half:,field],valid[...,half:,field])
            out[...,index]=recent-older;mask[...,index]=(no>0)&(nr>0)
        out[:]=np.where(mask,out,0)
    if not np.isfinite(result).all():raise ValueError('Nonfinite ordered statistics')
    return result.reshape(*seq.shape[:-3],84)


def features(observations,seq):
    raw=shared.feature_matrix(observations,'raw')
    stats=ordered_statistics(seq)
    if stats.shape[:-1]!=raw.shape[:-1]:raise ValueError('Ordered/raw observation shape mismatch')
    return np.concatenate((raw,stats),axis=-1)


def training_features(obs,days,temporal,receipts):
    result=np.empty((len(days),5,204),np.float64)
    for day in temporal.days:
        if shared.sha(temporal.root/day/'complete.json')!=receipts[day]['receipt_sha256']:
            raise ValueError('Temporal training receipt changed')
        arrays=temporal.load(day);ix=np.flatnonzero(days==day)
        if not np.array_equal(arrays['rows'],np.arange(0,len(temporal.predictive.load(day)['seconds']),10)):
            raise ValueError('Ordered training sample grid mismatch')
        result[ix]=features({k:v[ix] for k,v in obs.items()},arrays['seq'])
    return result


class Scorer:
    row_step=10
    def __init__(self,model,selected,binding,temporal):
        self.model,self.selected,self.binding,self.temporal=model,selected,binding,temporal

    def for_session(self,session):
        day=session.day
        if day not in self.temporal.days:raise ValueError('Only approved TRAINING replay dates')
        receipt=self.binding['contract']['temporal_receipts'][day]
        if shared.sha(self.temporal.root/day/'complete.json')!=receipt['receipt_sha256']:
            raise ValueError('Replay temporal receipt differs from training')
        arrays=self.temporal.load(day);snapshot=temporal_net.causal_snapshots(self.temporal.predictive,day)
        rows=arrays['rows'];seconds=snapshot['seconds'][rows]
        if (session.tickers!=self.temporal.predictive.source.items[day]['selected'] or
                not np.array_equal(session.selection[seconds],snapshot['selection'][rows]) or
                not np.array_equal(session.features[seconds],snapshot['features'][rows,:,:98])):
            raise ValueError('Session temporal admission/market mismatch')
        return BoundScorer(self,ordered_statistics(arrays['seq']),snapshot,rows,seconds)


class BoundScorer:
    row_step=10
    def __init__(self,parent,stats,snapshot,rows,seconds):
        self.parent,self.stats,self.snapshot,self.rows=parent,stats,snapshot,rows
        self.clock_index={int(t):i for i,t in enumerate(seconds)};self.binding=parent.binding

    def score(self,observations):
        obs=shared.observation_batch(observations)
        clocks=np.rint(obs['account'][:,3].astype(np.float64)*57600).astype(np.int64)
        if any(int(t) not in self.clock_index for t in clocks):raise ValueError('Clock outside bound sample grid')
        ix=np.asarray([self.clock_index[int(t)] for t in clocks]);rows=self.rows[ix]
        if (not np.array_equal(obs['features'][...,:98],self.snapshot['features'][rows,:,:98]) or
                not np.array_equal(obs['candidate_valid'],self.snapshot['candidate_valid'][rows]) or
                not np.array_equal(obs['account'][:,3],self.snapshot['account'][rows,3])):
            raise ValueError('Causal score clock/market/admission mismatch')
        x=np.concatenate((shared.feature_matrix(obs,'raw'),self.stats[ix]),axis=-1)
        value=scores(self.parent.model,x) if self.parent.selected else np.zeros(x.shape[:-1])
        return np.where(obs['candidate_valid'],value,-np.inf)


def sources():
    return dict(raw_tree.sources(),temporal_net=shared.sha(Path(temporal_net.__file__)),
                temporal_data=shared.sha(Path(__file__).with_name('temporal_data.py')),
                entry_temporal_tree=shared.sha(Path(__file__)))


def fit_model(x,net):
    x,net=np.asarray(x),np.asarray(net)
    if x.ndim!=2 or x.shape[1]!=204 or net.shape!=(len(x),) or not np.isfinite(x).all() or not np.isfinite(net).all():
        raise ValueError('Finite causal204 features and separate known net outcomes required')
    if set((net[net!=0]>0).tolist())!={False,True}:
        raise ValueError('Positive-weight examples of both payoff signs required')
    model=HistGradientBoostingClassifier(**PARAMETERS)
    with threadpool_limits(limits=4):model.fit(x,net>0,sample_weight=np.abs(net)/10)
    return model


def scores(model,features):
    shape=features.shape[:-1]
    with threadpool_limits(limits=4):result=model.decision_function(features.reshape(-1,204))*10
    if not np.isfinite(result).all():raise ValueError('Nonfinite tree policy score')
    return result.reshape(shape)


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
    if not isinstance(model,HistGradientBoostingClassifier) or model.n_features_in_!=204:
        raise ValueError('Unexpected tree estimator schema')
    metrics=json.loads((root/'metrics.json').read_text())
    selected=metrics['selected_candidate']
    if selected is not (metrics['development']['greedy_net_per_decision']>0):raise ValueError('Tree selection receipt mismatch')
    temporal=TemporalDataset(dataset.root,c['roots']['snapshots'],c['roots']['temporal'])
    if temporal.binding!=c['temporal_binding']:raise ValueError('Temporal recipe mismatch')
    return Scorer(model,selected,dict(root=str(root),contract=c,contract_hash=h,complete_sha256=shared.sha(root/'complete.json'),
        model_sha256=seal['files']['model.pkl'],selected_candidate=selected),temporal)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',default=shared.DEFAULT_DEST)
    p.add_argument('--snapshots',default='D:/TradingML/runtimes/rl-trading/v7/predictive-data-v1')
    p.add_argument('--labels',default='D:/TradingML/runtimes/rl-trading/v7/entry-opportunity-full150')
    p.add_argument('--temporal',default='D:/TradingML/runtimes/rl-trading/v7/temporal-data-v1')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--resume',action='store_true')
    args=p.parse_args(argv);root=args.output.resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):raise ValueError('Available runtime output required')
    temporal=TemporalDataset(args.source,args.snapshots,args.temporal);data=temporal.predictive
    receipts=temporal_net.temporal_receipts(temporal)
    labels=shared.EntryLabels(data,args.labels)
    contract=dict(temporal_binding=temporal.binding,temporal_receipts=receipts,feature_recipe=STAT_NAMES,source=sources(),base_source=shared.source_identity(),versions=versions(),training_binding=data.source.binding,snapshots_binding=data.binding,
        label_contract=labels.contract,label_receipts=labels.receipts,label_manifest_sha256=shared.sha(labels.root/'manifest.json'),
        fit_days=data.fit_days,development_days=data.development_days,risk=shared.Config().manifest(),parameters=PARAMETERS,
        row_step=10,threads=4,input='raw120 plus seven groups of six ordered statistics and six validity masks=204',
        target='net>0',weight='abs(net)/10; known zero outcomes retained with zero weight',
        score='decision_function *10; binary policy logits, NOT net-dollar estimates',forecast_reuse=False,
        selection='fixed final candidate only; strict greedy development net/decision > WAIT0, else publish WAIT',
        representation_caveat='same fixed classifier as raw tree; adds deterministic ordered statistics, not attention',
        validation_access=False,evidence='overlapping standalone counterfactual outcomes, NOT account profit',
        roots={k:str(Path(getattr(args,k)).resolve()) for k in ('source','snapshots','labels','temporal','output')})
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
        x=training_features(obs,days,temporal,receipts);fit=np.isin(days,data.fit_days);dev=np.isin(days,data.development_days)
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
