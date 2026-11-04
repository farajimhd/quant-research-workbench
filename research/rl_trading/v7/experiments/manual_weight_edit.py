"""One attribution-guided outgoing-weight edit; no optimizer or gradient updates."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import sys
sys.dont_write_bytecode=True
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
import argparse
import json
import subprocess
import numpy as np
import torch
from research.rl_trading.v7.experiments import temporal_weighted_policy as parent
shared=parent.shared
KEY='head.2.weight'
RULE=dict(neighborhood='top10 percent of eligible fit scores',halves='first60 and next60 fit days',
    diagnostic='mean contribution on losing minus profitable examples; positive mean contribution in both halves',
    selection='largest minimum harmful contrast across halves, first index on tie',
    minimum_examples_per_sign_per_half=30,weight_multiplier=.5,candidates=1,
    caveat='Observed association is not semantic understanding or proof of a causal trading feature')


def choose(scores,contributions,payoffs,known,days,fit_days):
    if set(days)!=set(fit_days) or len(fit_days)!=120:raise ValueError('Exactly120 fit days required')
    if contributions.shape!=(*scores.shape,64) or payoffs.shape!=scores.shape or known.shape!=scores.shape:
        raise ValueError('Attribution shape mismatch')
    threshold=float(np.quantile(scores[known],.9))
    contrasts=[];means=[];counts=[]
    for half in (fit_days[:60],fit_days[60:]):
        near=known & np.isin(days,half)[:,None] & (scores>=threshold)
        good=near & (payoffs>0);bad=near & (payoffs<0)
        if min(good.sum(),bad.sum())<30:raise ValueError('Insufficient per-sign attribution evidence')
        contrasts.append(contributions[bad].mean(0)-contributions[good].mean(0))
        means.append(contributions[near].mean(0));counts.append(dict(profitable=int(good.sum()),losing=int(bad.sum())))
    contrast=np.stack(contrasts);mean=np.stack(means)
    eligible=(contrast>0).all(0)&(mean>0).all(0)
    ranking=np.where(eligible,contrast.min(0),-np.inf)
    if not eligible.any():raise ValueError('No justified consistent edit; do not perturb arbitrarily')
    index=int(np.argmax(ranking))
    return dict(index=index,threshold=threshold,counts=counts,contrast_by_half=contrast.tolist(),
                mean_contribution_by_half=mean.tolist(),eligible_indices=np.flatnonzero(eligible).tolist())


def edited_state(state,index):
    if not 0<=index<64:raise ValueError('Invalid neuron index')
    changed={k:v.detach().cpu().clone() for k,v in state.items()}
    changed[KEY][0,index]*=.5
    return changed


def verify_edit(original,edited,index):
    expected=edited_state(original,index)
    if set(expected)!=set(edited) or any(not torch.equal(expected[k],edited[k].cpu()) for k in expected):
        raise ValueError('Checkpoint is not the declared single-weight edit')


def collect(scorer,labels,days,device,progress=None):
    scores=[];contributions=[];targets=[];masks=[];dates=[]
    model=scorer.model;model.eval();captured=[]
    hook=model.head[-1].register_forward_pre_hook(lambda module,args:captured.append(args[0].detach()))
    try:
        for number,day in enumerate(days,1):
            obs,keep,y,_=labels.load(day);batch=shared.observation_batch(obs)
            arrays=scorer.temporal.load(day)
            if not np.array_equal(arrays['rows'],np.arange(0,len(scorer.temporal.predictive.load(day)['seconds']),10)):
                raise ValueError('Attribution temporal grid mismatch')
            batch['seq']=arrays['seq'];target=np.zeros((len(batch['features']),5),np.float32);mask=np.zeros_like(target,bool)
            target.reshape(-1)[keep]=y;mask.reshape(-1)[keep]=True
            for begin in range(0,len(target),16):
                ix=np.arange(begin,min(begin+16,len(target)));captured.clear()
                with torch.no_grad():
                    tensors=parent.tensors(batch,ix,device);output=model(**tensors)
                    if len(captured)!=1:raise ValueError('Unexpected output-head invocation')
                    parts=10*captured[0]*model.head[-1].weight[0]
                    expected=parent.temporal.baseline(tensors['features'],tensors['account'])+parts.sum(-1)+10*model.head[-1].bias
                    torch.testing.assert_close(output,expected,atol=1e-5,rtol=1e-5)
                scores.append(output.cpu().numpy());contributions.append(parts.cpu().numpy())
            targets.append(target);masks.append(mask);dates.extend([day]*len(target))
            if progress:progress(number)
    finally:hook.remove()
    return tuple(np.concatenate(x) for x in (scores,contributions,targets,masks))+(np.asarray(dates),)


def load_bundle(root,dataset,forecast=None,*,device='cpu'):
    root=Path(root).resolve();seal=json.loads((root/'complete.json').read_text())
    for name in ('manifest.json','edited.pt','fit-diagnostic.json'):
        if shared.sha(root/name)!=seal['files'][name]:raise ValueError('Manual edit artifact integrity mismatch')
    manifest=json.loads((root/'manifest.json').read_text())
    if manifest['source']!=shared.sha(Path(__file__)) or manifest['rule']!=RULE or manifest['validation_access'] is not False:
        raise ValueError('Manual edit source/rule mismatch')
    scorer=parent.load_bundle(manifest['parent_root'],dataset,device=device)
    if scorer.binding!=manifest['parent_binding']:raise ValueError('Parent checkpoint lineage changed')
    state=torch.load(root/'edited.pt',map_location='cpu',weights_only=True)
    verify_edit(scorer.model.state_dict(),state,manifest['edit']['index'])
    scorer.model.load_state_dict(state)
    # Preserve the temporal contract fields used by for_session.
    scorer.binding=dict(scorer.binding,manual_edit=dict(manifest=manifest,complete_sha256=shared.sha(root/'complete.json')))
    return scorer


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent',type=Path,default=Path('D:/TradingML/runtimes/rl-trading/v7/temporal-weighted-policy-v1'))
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--device',choices=('cpu','cuda'),default='cuda')
    p.add_argument('--evaluate-development',action='store_true')
    args=p.parse_args(argv);root=args.output.resolve()
    if not Path('D:/TradingML/runtimes').is_dir() or not root.is_relative_to(Path('D:/TradingML/runtimes').resolve()):
        raise ValueError('Runtime output required')
    torch.set_num_threads(4)
    from research.rl_trading.v7.data import TrainingDataset
    data=TrainingDataset(shared.DEFAULT_DEST)
    if args.evaluate_development:
        edited=load_bundle(root,data,device=args.device)
        manifest=json.loads((root/'manifest.json').read_text())
        original=parent.load_bundle(manifest['parent_root'],data,device=args.device)
        labels=shared.EntryLabels(edited.temporal.predictive,manifest['labels_root'])
        score,parts,y,mask,days=collect(original,labels,data.days[120:],args.device)
        changed=collect(edited,labels,data.days[120:],args.device)[0]
        np.testing.assert_allclose(changed,score-.5*parts[...,manifest['edit']['index']],atol=2e-5,rtol=2e-5)
        report=dict(original=parent.policy_metrics(score,y,mask,days),edited=parent.policy_metrics(changed,y,mask,days),
                    selection='No changes permitted from this evaluation',validation_access=False)
        shared.write_json(root/'development-diagnostic.json',report)
        print(json.dumps({k:{n:v for n,v in r.items() if n!='by_day'} for k,r in report.items() if isinstance(r,dict)}),flush=True)
        return 0
    root.mkdir(parents=True,exist_ok=False)
    scorer=parent.load_bundle(args.parent,data,device=args.device)
    labels_root=scorer.binding['contract']['roots']['labels'];labels=shared.EntryLabels(scorer.temporal.predictive,labels_root)
    def progress(n):shared.write_json(root/'status.json',dict(status='diagnosing_fit',completed=n,queued=120-n,failed=0))
    score,parts,y,mask,days=collect(scorer,labels,data.days[:120],args.device,progress)
    edit=choose(score,parts,y,mask,days,data.days[:120]);index=edit['index']
    old=scorer.model.state_dict();changed=edited_state(old,index);verify_edit(old,changed,index)
    old_weight=float(old[KEY][0,index])
    scorer.model.load_state_dict(changed)
    candidate=collect(scorer,labels,data.days[:120],args.device)[0]
    np.testing.assert_allclose(candidate,score-.5*parts[...,index],atol=2e-5,rtol=2e-5)
    diagnostic=dict(attribution=edit,original=parent.policy_metrics(score,y,mask,days),
        edited=parent.policy_metrics(candidate,y,mask,days),label_receipts=labels.receipts,
        old_weight=old_weight,new_weight=float(changed[KEY][0,index]),
        gradients_used=False,optimizer_used=False)
    manifest=dict(source=shared.sha(Path(__file__)),parent_root=str(args.parent.resolve()),parent_binding=scorer.binding,
        rule=RULE,edit=dict(parameter=KEY,index=index,multiplier=.5),labels_root=labels_root,
        diagnostic_days=data.days[:120],validation_access=False,
        git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=Path(__file__).resolve().parents[4],text=True).strip())
    torch.save(changed,root/'edited.pt');shared.write_json(root/'manifest.json',manifest)
    shared.write_json(root/'fit-diagnostic.json',diagnostic)
    shared.write_json(root/'complete.json',dict(files={name:shared.sha(root/name) for name in ('manifest.json','edited.pt','fit-diagnostic.json')}))
    load_bundle(root,data,device='cpu')
    shared.write_json(root/'status.json',dict(status='complete',completed=120,queued=0,failed=0,validation_access=False))
    print(json.dumps(dict(index=index,old_weight=diagnostic['old_weight'],new_weight=diagnostic['new_weight'],
        original_net=diagnostic['original']['chosen_standalone_net_sum'],edited_net=diagnostic['edited']['chosen_standalone_net_sum'])),flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
