"""Training-only residual entry payoff prediction; not persistent-account profit."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
for _key in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS'):
    os.environ[_key] = '4'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))
import argparse
import json
import subprocess
import time
import numpy as np
import torch
from torch import nn
from research.rl_trading.v7.experiments import entry_score as shared


def sources():
    return dict(shared.implementation(), entry_net=shared.sha(Path(__file__)))


def baseline(features, account):
    return (-shared.Config().initial_cash*account[:, :1]*shared.Config().allocation*
            features[..., 99]*features[..., 100:103].sum(-1)/100)


def causal_extra(features, account):
    if isinstance(features, torch.Tensor):
        return torch.cat((features[..., -14:], account[:, None].expand(-1, 5, -1)), -1)
    return np.concatenate((features[..., -14:], np.broadcast_to(account[:, None], (len(account), 5, 8))), -1)


class EntryNet(nn.Module):
    def __init__(self, forecast_state, mean, std):
        super().__init__()
        self.encoder = shared.PredictivePolicy()
        self.encoder.load_state_dict(forecast_state)
        for name in ('buy', 'control', 'value', 'forecast'):
            getattr(self.encoder, name).requires_grad_(False)
        self.register_buffer('mean', torch.as_tensor(mean, dtype=torch.float32))
        self.register_buffer('std', torch.as_tensor(std, dtype=torch.float32))
        self.head = nn.Sequential(nn.Linear(86, 64), nn.SiLU(), nn.Linear(64, 1))
        nn.init.zeros_(self.head[-1].weight)
        nn.init.zeros_(self.head[-1].bias)

    def forward(self, features, account, holding, candidate_valid):
        tokens = self.encoder.encode(features, account, holding, candidate_valid)[:, :5]
        extra = (causal_extra(features, account)-self.mean)/self.std
        prediction = baseline(features, account)+10*self.head(torch.cat((tokens, extra), -1)).squeeze(-1)
        if not torch.isfinite(prediction).all():
            raise ValueError('Nonfinite net-dollar prediction')
        return prediction


def materialize(data, labels, progress=None):
    batches, targets, masks, days = [], [], [], []
    for day in data.days:
        obs, keep, y, _ = labels.load(day)
        batch = shared.observation_batch(obs)
        target = np.zeros((len(batch['features']), 5), np.float32)
        mask = np.zeros_like(target, dtype=bool)
        target.reshape(-1)[keep] = y
        mask.reshape(-1)[keep] = True
        batches.append(batch); targets.append(target); masks.append(mask)
        days.extend([day]*len(target))
        if progress is not None and len(batches) % 10 == 0:
            progress(len(batches))
    obs = {key: np.concatenate([b[key] for b in batches]) for key in batches[0]}
    return obs, np.concatenate(targets), np.concatenate(masks), np.asarray(days)


def tensors(obs, indices, device):
    return {k: torch.as_tensor(v[indices], device=device) for k, v in obs.items()}


def evaluate(model, obs, y, mask, rows, days, device, batch_size):
    model.eval()
    predictions = []
    with torch.no_grad():
        for start in range(0, len(rows), batch_size):
            ix = rows[start:start+batch_size]
            predictions.append(model(**tensors(obs, ix, device)).cpu().numpy())
    p = np.concatenate(predictions)
    valid = mask[rows]
    return shared.diagnostics(p[valid].astype(np.float64), y[rows][valid].astype(np.float64),
                              np.broadcast_to(days[rows, None], valid.shape)[valid])


def save_epoch(root, state):
    path = root/f"epoch-{state['epoch']:03d}.pt"
    if path.exists() or path.with_suffix('.complete.json').exists():
        raise ValueError('Authoritative epoch already exists')
    temporary = path.with_suffix('.partial')
    torch.save(state, temporary)
    os.replace(temporary, path)
    shared.write_json(path.with_suffix('.complete.json'), dict(sha256=shared.sha(path), contract_hash=state['contract_hash']))
    publish(root, state)


def read_epoch(root, epoch, contract_hash):
    path = root/f'epoch-{epoch:03d}.pt'
    seal = json.loads(path.with_suffix('.complete.json').read_text())
    if seal != dict(sha256=shared.sha(path), contract_hash=contract_hash):
        raise ValueError('Epoch checkpoint integrity mismatch')
    state = torch.load(path, map_location='cpu', weights_only=False)
    if state['contract_hash'] != contract_hash or state['epoch'] != epoch:
        raise ValueError('Epoch checkpoint contract mismatch')
    if any(not torch.isfinite(v).all() for v in state['model'].values()):
        raise ValueError('Nonfinite saved model')
    return state


def publish(root, state):
    for name, epoch in (('latest', state['epoch']), ('best', state['best_epoch'])):
        path = root/f'epoch-{epoch:03d}.pt'
        read_epoch(root, epoch, state['contract_hash'])
        shared.write_json(root/(name+'.json'), dict(epoch=epoch, sha256=shared.sha(path), contract_hash=state['contract_hash']))
    shared.write_json(root/'metrics.json', dict(epochs=state['metrics'], baselines=state['baselines'],
        best_epoch=state['best_epoch'], learned_improvement=state['best_epoch'] > 0,
        evidence='standalone overlapping entry outcomes, NOT persistent-account profit'))


class Scorer:
    def __init__(self, model, binding, device):
        self.model, self.binding, self.device, self.row_step = model, binding, device, 10

    def score(self, observations):
        obs = shared.observation_batch(observations)
        results = []
        with torch.no_grad():
            for begin in range(0, len(obs['features']), 128):
                ix = np.arange(begin, min(begin+128, len(obs['features'])))
                results.append(self.model(**tensors(obs, ix, self.device)).cpu().numpy())
        return np.where(obs['candidate_valid'], np.concatenate(results), -np.inf)


def load_bundle(root, dataset, forecast, *, device='cpu'):
    root = Path(root).resolve()
    manifest = json.loads((root/'manifest.json').read_text())
    c, h = manifest['contract'], manifest['contract_hash']
    if (shared.digest(c) != h or c['source'] != sources() or c['base_source'] != shared.source_identity() or
            c['training_binding'] != dataset.binding or c['risk'] != shared.Config().manifest() or
            c['fit_days'] != dataset.days[:120] or c['development_days'] != dataset.days[120:] or
            len(dataset.days) != 150 or c['validation_access'] is not False):
        raise ValueError('Entry-net source/data/split contract mismatch')
    saved, forecast_sha = shared.load_forecast(Path(forecast), dataset)
    if forecast_sha != c['forecast_sha256'] or saved['contract_hash'] != c['forecast_contract']:
        raise ValueError('Entry-net forecast lineage mismatch')
    pointer = json.loads((root/'best.json').read_text())
    state = read_epoch(root, pointer['epoch'], h)
    if pointer != dict(epoch=state['epoch'], sha256=shared.sha(root/f"epoch-{state['epoch']:03d}.pt"), contract_hash=h):
        raise ValueError('Best checkpoint pointer mismatch')
    model = EntryNet(saved['policy'], np.zeros(22), np.ones(22)).to(device)
    model.load_state_dict(state['model'])
    if torch.any(model.std <= 0):
        raise ValueError('Invalid saved scaler')
    model.requires_grad_(False).eval()
    return Scorer(model, dict(root=str(root), contract=c, contract_hash=h, checkpoint=pointer), device)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', default=shared.DEFAULT_DEST)
    p.add_argument('--snapshots', default='D:/TradingML/runtimes/rl-trading/v7/predictive-data-v1')
    p.add_argument('--labels', default='D:/TradingML/runtimes/rl-trading/v7/entry-opportunity-full150')
    p.add_argument('--forecast', type=Path, default=Path('D:/TradingML/runtimes/rl-trading/v7/forecast-seed1917/checkpoint_best.pt'))
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    p.add_argument('--epochs', type=int, default=20)
    p.add_argument('--batch-size', type=int, default=128)
    p.add_argument('--resume', action='store_true')
    args = p.parse_args(argv)
    root = args.output.resolve()
    runtime = Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime) or not 1 <= args.batch_size <= 128 or not 1 <= args.epochs <= 20:
        raise ValueError('Runtime output and bounded epochs/batch required')
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA unavailable; select CPU explicitly')
    torch.set_num_threads(4)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(1917)
    data = shared.PredictiveDataset(args.source, args.snapshots)
    labels = shared.EntryLabels(data, args.labels)
    saved, forecast_sha = shared.load_forecast(args.forecast, data.source)
    contract = dict(source=sources(), base_source=shared.source_identity(), training_binding=data.source.binding,
        snapshots_binding=data.binding, label_contract=labels.contract, label_receipts=labels.receipts,
        label_manifest_sha256=shared.sha(labels.root/'manifest.json'),
        forecast_sha256=forecast_sha, forecast_contract=saved['contract_hash'], risk=shared.Config().manifest(),
        fit_days=data.fit_days, development_days=data.development_days, row_step=10,
        recipe='current-quote-baseline-plus-10-dollar-neural-residual-v1', seed=1917,
        encoder_lr=1e-5, head_lr=1e-3, weight_decay=.01, loss='MSE(net dollars/10)',
        scaler='fit-only accepted cost14/account8; nearconstant threshold1e-12',
        selection='minimum internal30 development MSE; epoch0 baseline included',
        epochs=args.epochs, batch_size=args.batch_size, device=args.device,
        torch_version=torch.__version__, numpy_version=np.__version__, validation_access=False,
        roots={k: str(Path(getattr(args, k)).resolve()) for k in ('source','snapshots','labels','forecast','output')},
        evidence='standalone overlapping entry outcomes, NOT persistent-account profit')
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
        obs, y, mask, days = materialize(data, labels, lambda completed: shared.write_json(root/'status.json',
            dict(status='materializing', completed=completed, queued=150-completed, failed=0)))
        fit_rows = np.flatnonzero(np.isin(days, data.fit_days))
        dev_rows = np.flatnonzero(np.isin(days, data.development_days))
        if not mask[fit_rows].any() or not mask[dev_rows].any():
            raise ValueError('Empty internal partition')
        extra = causal_extra(obs['features'], obs['account'])[fit_rows][mask[fit_rows]].astype(np.float64)
        mean, std = extra.mean(0), extra.std(0)
        std[std <= 1e-12*np.maximum(1., abs(mean))] = 1
        model = EntryNet(saved['policy'], mean, std).to(args.device)
        optimizer = torch.optim.AdamW([dict(params=[v for v in model.encoder.parameters() if v.requires_grad], lr=1e-5),
                                      dict(params=model.head.parameters(), lr=1e-3)], weight_decay=.01)
        generator = torch.Generator().manual_seed(1917)
        fit_mean = float(y[fit_rows][mask[fit_rows]].astype(np.float64).mean())
        dev_y = y[dev_rows][mask[dev_rows]].astype(np.float64)
        base = baseline(obs['features'][dev_rows], obs['account'][dev_rows])[mask[dev_rows]].astype(np.float64)
        baselines = dict(fit_mean=fit_mean, constant_mse=float(np.mean((dev_y-fit_mean)**2)),
                         current_quote_mse=float(np.mean((dev_y-base)**2)))
        metrics = [dict(epoch=0, development=evaluate(model, obs, y, mask, dev_rows, days, args.device, args.batch_size))]
        best, best_epoch, start = metrics[0]['development']['mse'], 0, 1
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
                if not valid.any(): continue
                prediction = model(**tensors(obs, ix, args.device))
                target = torch.as_tensor(y[ix], device=args.device)
                loss = ((prediction[valid]-target[valid])/10).square().mean()
                if not torch.isfinite(loss): raise ValueError('Nonfinite training loss')
                optimizer.zero_grad(set_to_none=True); loss.backward()
                if any(p.grad is not None and not torch.isfinite(p.grad).all() for p in model.parameters()):
                    raise ValueError('Nonfinite training gradient')
                optimizer.step()
                n = int(valid.sum()); loss_sum += float(loss.detach())*n; count += n
                if time.monotonic()-last_progress >= 15:
                    shared.write_json(root/'status.json', dict(status='training', active_epoch=epoch,
                        completed=epoch-1, queued=args.epochs-epoch, fit_snapshots=min(begin+args.batch_size, len(order)),
                        total_fit_snapshots=len(order), failed=0))
                    last_progress = time.monotonic()
            result = evaluate(model, obs, y, mask, dev_rows, days, args.device, args.batch_size)
            metrics.append(dict(epoch=epoch, fit_scaled_mse=loss_sum/count, development=result))
            if result['mse'] < best: best, best_epoch = result['mse'], epoch
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
