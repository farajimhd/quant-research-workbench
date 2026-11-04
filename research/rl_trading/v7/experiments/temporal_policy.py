"""Full-information counterfactual entry policy; neither PPO nor account profit."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
import sys
sys.dont_write_bytecode=True
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parents[4]))
import argparse
import json
import subprocess
import time
import numpy as np
import torch
from research.rl_trading.v7.experiments import temporal_net as temporal
shared=temporal.shared
TemporalDataset=temporal.TemporalDataset
TemporalNet=temporal.TemporalNet
temporal_receipts=temporal.temporal_receipts
materialize=temporal.materialize
tensors=temporal.tensors
causal_extra=temporal.causal_extra
save_epoch=temporal.save_epoch
read_epoch=temporal.read_epoch
publish=temporal.publish


def sources():
    return dict(temporal.sources(),temporal_policy=shared.sha(Path(__file__)))


def distribution(scores,known):
    if scores.shape!=known.shape or known.dtype!=torch.bool or not torch.isfinite(scores).all():
        raise ValueError('Invalid policy scores/current eligibility')
    logits=torch.cat((torch.zeros_like(scores[:,:1]),scores/10),1)
    allowed=torch.cat((torch.ones_like(known[:,:1]),known),1)
    return torch.distributions.Categorical(logits=logits.masked_fill(~allowed,-torch.inf))


def objective(scores,payoffs,known):
    if payoffs.shape!=scores.shape or not torch.isfinite(payoffs).all():
        raise ValueError('Invalid separate counterfactual targets')
    policy=distribution(scores,known)
    targets=torch.cat((torch.zeros_like(payoffs[:,:1]),payoffs/10),1)
    return -(policy.probs*targets).sum(1).mean()-.001*policy.entropy().mean()


def policy_metrics(scores,payoffs,known,days):
    with torch.no_grad():
        policy=distribution(torch.as_tensor(scores,dtype=torch.float64),torch.as_tensor(known))
        targets=np.concatenate((np.zeros((len(payoffs),1)),payoffs),1)
        # WAIT occupies column0, so ties with WAIT and all nonpositive entries wait.
        greedy=np.argmax(np.concatenate((np.zeros((len(scores),1)),np.where(known,scores,-np.inf)),1),1)
        chosen=targets[np.arange(len(greedy)),greedy]
        expected=(policy.probs.numpy()*targets).sum(1)
    return dict(decisions=len(scores),selected_count=int((greedy>0).sum()),
        profitable_selected_count=int(((greedy>0)&(chosen>0)).sum()),
        chosen_standalone_net_sum=float(chosen.sum()),greedy_net_per_decision=float(chosen.mean()),
        stochastic_counterfactual_net_per_decision=float(expected.mean()),
        entropy=float(policy.entropy().mean()),wait_count=int((greedy==0).sum()),
        by_day=[dict(day=str(day),decisions=int((days==day).sum()),
                     selected_count=int(((days==day)&(greedy>0)).sum()),
                     chosen_standalone_net_sum=float(chosen[days==day].sum())) for day in np.unique(days)],
        evidence='overlapping single-entry counterfactual labels; NOT account profit or own-reward PPO')


def evaluate(model,obs,y,mask,rows,days,device,batch_size):
    model.eval();scores=[]
    with torch.no_grad():
        for begin in range(0,len(rows),batch_size):
            ix=rows[begin:begin+batch_size]
            scores.append(model(**tensors(obs,ix,device)).cpu().numpy())
    return policy_metrics(np.concatenate(scores),y[rows].astype(np.float64),mask[rows],days[rows])


def initial_model(bundle,dataset,device):
    scorer=temporal.load_bundle(bundle,dataset,device=device)
    model=TemporalNet(np.zeros(22),np.ones(22)).to(device)
    model.load_state_dict(scorer.model.state_dict())
    torch.nn.init.zeros_(model.head[-1].weight);torch.nn.init.zeros_(model.head[-1].bias)
    return model,scorer.binding


def load_bundle(root,dataset,forecast=None,*,device='cpu'):
    root=Path(root).resolve();manifest=json.loads((root/'manifest.json').read_text())
    c,h=manifest['contract'],manifest['contract_hash']
    if (shared.digest(c)!=h or c['source']!=sources() or c['base_source']!=shared.source_identity() or
            c['training_binding']!=dataset.binding or c['risk']!=shared.Config().manifest() or
            c['fit_days']!=dataset.days[:120] or c['development_days']!=dataset.days[120:] or
            len(dataset.days)!=150 or c['validation_access'] is not False or c['forecast_reuse'] is not False):
        raise ValueError('Temporal-policy source/data/split contract mismatch')
    # Verify initial checkpoint lineage without opening forecast weights or labels.
    initial=temporal.load_bundle(c['roots']['initial'],dataset,device='cpu')
    if initial.binding!=c['initial_binding']:raise ValueError('Temporal-policy initial lineage changed')
    data=TemporalDataset(dataset.root,c['roots']['snapshots'],c['roots']['temporal'])
    if data.binding!=c['temporal_binding']:raise ValueError('Temporal recipe changed')
    pointer=json.loads((root/'best.json').read_text());state=read_epoch(root,pointer['epoch'],h)
    if pointer!=dict(epoch=state['epoch'],sha256=shared.sha(root/f"epoch-{state['epoch']:03d}.pt"),contract_hash=h):
        raise ValueError('Policy best pointer mismatch')
    model=TemporalNet(np.zeros(22),np.ones(22)).to(device)
    model.load_state_dict(state['model']);model.requires_grad_(False).eval()
    if torch.any(model.std<=0):raise ValueError('Invalid policy scaler')
    return temporal.Scorer(model,dict(root=str(root),contract=c,contract_hash=h,checkpoint=pointer),data,device)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', default=shared.DEFAULT_DEST)
    p.add_argument('--snapshots', default='D:/TradingML/runtimes/rl-trading/v7/predictive-data-v1')
    p.add_argument('--labels', default='D:/TradingML/runtimes/rl-trading/v7/entry-opportunity-full150')
    p.add_argument('--temporal', default='D:/TradingML/runtimes/rl-trading/v7/temporal-data-v1')
    p.add_argument('--initial', type=Path, default=Path('D:/TradingML/runtimes/rl-trading/v7/temporal-net-v1'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    p.add_argument('--epochs', type=int, default=20)
    p.add_argument('--batch-size', type=int, default=16)
    p.add_argument('--resume', action='store_true')
    args = p.parse_args(argv)
    root = args.output.resolve()
    runtime = Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime) or not 1 <= args.batch_size <= 32 or not 1 <= args.epochs <= 20:
        raise ValueError('Runtime output and bounded epochs/batch required')
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA unavailable; select CPU explicitly')
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(1917)
    temporal = TemporalDataset(args.source, args.snapshots, args.temporal)
    data = temporal.predictive
    receipts = temporal_receipts(temporal)
    labels = shared.EntryLabels(data, args.labels)
    model, initial_binding = initial_model(args.initial, data.source, args.device)
    initial_contract=initial_binding['contract']
    if (initial_contract['temporal_receipts']!=receipts or initial_contract['label_receipts']!=labels.receipts or
            initial_contract['snapshots_binding']!=data.binding):
        raise ValueError('Initial temporal checkpoint uses different sealed inputs/labels')
    contract = dict(initial_binding=initial_binding, source=sources(), base_source=shared.source_identity(), training_binding=data.source.binding,
        snapshots_binding=data.binding, label_contract=labels.contract, label_receipts=labels.receipts,
        label_manifest_sha256=shared.sha(labels.root/'manifest.json'),
        temporal_binding=temporal.binding, temporal_receipts=receipts, forecast_reuse=False,
        forecast_argument='ignored compatibility argument at scoring only', risk=shared.Config().manifest(),
        fit_days=data.fit_days, development_days=data.development_days, row_step=10,
        recipe='full-information-temporal-entry-policy-v1', seed=1917,
        encoder_lr=1e-4, head_lr=1e-3, weight_decay=.01, loss='-mean(sum(softmax([WAIT0,score/10])*[WAIT0,net/10]))-.001*entropy', dropout=.1, window_position=60, summary_token=False,
        scaler='fit-only accepted cost14/account8; nearconstant threshold1e-12',
        selection='maximum internal30 greedy net per decision incl WAIT0; strict improvement; earlier ties',
        score_semantics='policy logit times10; NOT calibrated net-dollar prediction',
        action_mask='WAIT true; entry known CURRENT eligibility; observed nonfill zeros included',
        initializer='validated temporal best body; final head weight/bias reset zero; fresh optimizer',
        epochs=args.epochs, batch_size=args.batch_size, device=args.device,
        torch_version=torch.__version__, numpy_version=np.__version__, validation_access=False,
        roots={k: str(Path(getattr(args, k)).resolve()) for k in ('source','snapshots','labels','temporal','initial','output')},
        evidence='full-information overlapping counterfactual outcomes; NOT account profit or own-reward PPO')
    h = shared.digest(contract)
    if args.resume:
        previous = json.loads((root/'manifest.json').read_text())
        if previous['contract'] != contract or previous['contract_hash'] != h:
            raise ValueError('Exact resume contract mismatch')
    else:
        root.mkdir(parents=True, exist_ok=False)
        shared.write_json(root/'manifest.json', dict(contract=contract, contract_hash=h,
            git_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                cwd=Path(__file__).resolve().parents[4], text=True).strip()))
    started = time.monotonic()
    try:
        shared.write_json(root/'status.json', dict(status='materializing', completed=0, queued=150, failed=0))
        obs, y, mask, days = materialize(data, labels, temporal, receipts, lambda completed: shared.write_json(root/'status.json',
            dict(status='materializing', completed=completed, queued=150-completed, failed=0)))
        fit_rows = np.flatnonzero(np.isin(days, data.fit_days))
        dev_rows = np.flatnonzero(np.isin(days, data.development_days))
        if not mask[fit_rows].any() or not mask[dev_rows].any():
            raise ValueError('Empty internal partition')
        extra = causal_extra(obs['features'], obs['account'])[fit_rows][mask[fit_rows]].astype(np.float64)
        mean, std = extra.mean(0), extra.std(0)
        std[std <= 1e-12*np.maximum(1., abs(mean))] = 1
        if not np.array_equal(model.mean.detach().cpu().numpy(),mean.astype(np.float32)) or not np.array_equal(model.std.detach().cpu().numpy(),std.astype(np.float32)):
            raise ValueError('Initial temporal fit-only scaler mismatch')
        optimizer = torch.optim.AdamW([dict(params=[v for name,v in model.named_parameters() if not name.startswith('head.')], lr=1e-4),
                                      dict(params=model.head.parameters(), lr=1e-3)], weight_decay=.01)
        generator = torch.Generator().manual_seed(1917)
        baselines = dict(greedy_wait_net_per_decision=0., selection='WAIT on nonpositive scores and ties')
        metrics = [dict(epoch=0, development=evaluate(model, obs, y, mask, dev_rows, days, args.device, args.batch_size))]
        best, best_epoch, start = metrics[0]['development']['greedy_net_per_decision'], 0, 1
        if best != 0 or metrics[0]['development']['selected_count'] != 0:
            raise ValueError('Epoch0 must be the all-WAIT zero-payoff baseline')
        def state(epoch):
            return dict(epoch=epoch, contract_hash=h, model=model.state_dict(), optimizer=optimizer.state_dict(),
                        rng=torch.get_rng_state(), cuda_rng=torch.cuda.get_rng_state_all() if args.device=='cuda' else [],
                        shuffle=generator.get_state(), best=best, best_epoch=best_epoch, metrics=metrics, baselines=baselines)
        if args.resume:
            # Sealed epochs are authoritative even if publication was interrupted.
            epochs = sorted(int(path.name.split('-')[1].split('.')[0]) for path in root.glob('epoch-*.complete.json'))
            if not epochs or epochs != list(range(max(epochs)+1)):
                raise ValueError('Noncontiguous committed epochs')
            for path in root.glob('epoch-*.pt'):
                if not path.with_suffix('.complete.json').exists():
                    quarantine = root/'uncommitted'; quarantine.mkdir(exist_ok=True)
                    path.rename(quarantine/(path.name+'.'+str(time.time_ns())))
            restored = read_epoch(root, max(epochs), h)
            model.load_state_dict(restored['model']); optimizer.load_state_dict(restored['optimizer'])
            torch.set_rng_state(restored['rng']); generator.set_state(restored['shuffle'])
            if args.device=='cuda': torch.cuda.set_rng_state_all(restored['cuda_rng'])
            metrics, best, best_epoch, start = restored['metrics'], restored['best'], restored['best_epoch'], restored['epoch']+1
            publish(root, restored)
        else:
            save_epoch(root, state(0))
        for epoch in range(start, args.epochs+1):
            if (root/'STOP').exists():
                shared.write_json(root/'status.json', dict(status='stopped', completed=epoch-1, queued=args.epochs-epoch+1, failed=0))
                return 2
            model.train()
            order = fit_rows[torch.randperm(len(fit_rows), generator=generator).numpy()]
            loss_sum, count = 0., 0
            last_progress = time.monotonic()
            for begin in range(0, len(order), args.batch_size):
                ix = order[begin:begin+args.batch_size]
                valid = torch.as_tensor(mask[ix], device=args.device)
                prediction = model(**tensors(obs, ix, args.device))
                target = torch.as_tensor(y[ix], device=args.device)
                loss = objective(prediction,target,valid)
                if not torch.isfinite(loss): raise ValueError('Nonfinite training loss')
                optimizer.zero_grad(set_to_none=True); loss.backward()
                if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in model.parameters()):
                    raise ValueError('Nonfinite training gradient')
                optimizer.step()
                n = len(ix); loss_sum += float(loss.detach())*n; count += n
                if time.monotonic()-last_progress >= 15:
                    shared.write_json(root/'status.json', dict(status='training', active_epoch=epoch,
                        completed=epoch-1, queued=args.epochs-epoch, fit_snapshots=min(begin+args.batch_size, len(order)),
                        total_fit_snapshots=len(order), failed=0))
                    last_progress = time.monotonic()
            result = evaluate(model, obs, y, mask, dev_rows, days, args.device, args.batch_size)
            metrics.append(dict(epoch=epoch, fit_policy_objective=loss_sum/count, development=result))
            if result['greedy_net_per_decision'] > best: best, best_epoch = result['greedy_net_per_decision'], epoch
            save_epoch(root, state(epoch))
            status = dict(status='training', completed=epoch, queued=args.epochs-epoch, failed=0, best_epoch=best_epoch,
                          elapsed_seconds=time.monotonic()-started)
            shared.write_json(root/'status.json', status); print(json.dumps(status), flush=True)
        shared.write_json(root/'status.json', dict(status='complete', completed=args.epochs, queued=0, failed=0,
                                                 best_epoch=best_epoch, validation_access=False))
    except BaseException as exc:
        shared.write_json(root/'status.json', dict(status='failed', error=repr(exc)))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
