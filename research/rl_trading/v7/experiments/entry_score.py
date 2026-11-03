"""Two fixed ridge scores for isolated, training-only entry net-dollar outcomes.

No future outcome enters an observation. Positive-score summaries sum independent,
possibly overlapping hypothetical entries; they are not portfolio/account profit.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
for _name in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
    os.environ[_name] = '4'
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
from threadpoolctl import threadpool_limits

from research.market_shards.v1.contract import digest, sha, write_json
from research.rl_trading.v7.config import Config
from research.rl_trading.v7.stage_data import DEFAULT_DEST
from research.rl_trading.v7.train import source_identity
from research.rl_trading.v7.experiments.predictive_data import PredictiveDataset
from research.rl_trading.v7.experiments.predictive_model import PredictivePolicy
from research.rl_trading.v7.experiments.predictive_ppo import load_forecast

REGULARIZATION = .001
KINDS = ('raw', 'attention')


def implementation():
    here = Path(__file__).parent
    return {name: sha(here/name) for name in ('entry_score.py', 'entry_outcomes.py', 'predictive_data.py',
                                            'predictive_model.py', 'predictive_ppo.py', 'cost_aware.py')}


def label_contract(predictive):
    """Reconstruct the exact approved five-minute diagnostic authority."""
    return dict(source=predictive.binding, code=sha(Path(__file__).with_name('entry_outcomes.py')),
        base_source=source_identity(), config=Config().manifest(), dates=predictive.days,
        row_step=10, target='single entry then WAIT with fixed exits; flat initial cash',
        evidence='hindsight opportunity diagnostic; not policy performance or PPO trajectories',
        excluded_metrics='skipped per-second rewards, peak equity and drawdown', validation_access=False)


def checked_records(report, snapshots, tickers, *, row_step=10):
    """Validate every requested coordinate; never select rows by future outcome."""
    expected = [(row, slot) for row in range(0, len(snapshots['seconds']), row_step) for slot in range(5)]
    records = report['records']
    if [(r['row'], r['slot']) for r in records] != expected:
        raise ValueError('Missing, duplicate, reordered or unexpected entry targets')
    counts = dict(ineligible_now=0, unfilled=0, closed=0, unliquidated=0)
    accepted, targets = [], []
    for index, record in enumerate(records):
        row, slot = record['row'], record['slot']
        if record['second'] != int(snapshots['seconds'][row]) or record['status'] not in counts:
            raise ValueError('Entry target clock/status mismatch')
        status = record['status']
        counts[status] += 1
        if status == 'unliquidated':
            raise ValueError('Unliquidated entry target: fail closed, never drop it')
        if status == 'ineligible_now':
            if record['net_dollars'] is not None or record['fills']:
                raise ValueError('Invalid current-ineligibility evidence')
            continue
        if not snapshots['candidate_valid'][row, slot]:
            raise ValueError('Nonadmitted candidate has an executable entry target')
        target = record['net_dollars']
        if target is None or not np.isfinite(target):
            raise ValueError('Missing/nonfinite executable entry target')
        fills = record['fills']
        if status == 'unfilled':
            if target != 0 or fills:
                raise ValueError('Nonfill must be an observed zero with no fills')
        else:
            if (record['terminal_quantity'] != 0 or len(fills) < 2 or fills[0]['side'] != 1 or
                    fills[0]['second'] != record['second']+1 or any(f['side'] != -1 for f in fills[1:])):
                raise ValueError('Invalid closed entry execution evidence')
            listing = int(snapshots['selection'][row, slot])
            if not 0 <= listing < len(tickers):
                raise ValueError('Invalid admitted listing index')
            ticker = tickers[listing]
            if any(f['ticker'] != ticker for f in fills):
                raise ValueError('Entry target listing identity mismatch')
            if any(not all(np.isfinite(f[k]) for k in ('quantity', 'price', 'fees')) or
                   f['quantity'] <= 0 or f['price'] <= 0 or f['fees'] < 0 for f in fills):
                raise ValueError('Invalid fill quantity/price/fees')
            quantity = sum(f['side']*f['quantity'] for f in fills)
            payoff = sum(-f['side']*f['quantity']*f['price']-f['fees'] for f in fills)
            if quantity != 0 or not np.isclose(payoff, target, rtol=0, atol=1e-7):
                raise ValueError('Closed target does not reconcile with signed fill cashflows')
        accepted.append(index)
        targets.append(float(target))
    if counts != report['counts']:
        raise ValueError('Reported entry-status counts differ from records')
    return np.asarray(accepted, dtype=np.int64), np.asarray(targets, dtype=np.float64), counts


class EntryLabels:
    def __init__(self, predictive, root):
        self.predictive, self.root = predictive, Path(root).resolve()
        self.days = list(predictive.days)
        if len(self.days) != 150:
            raise ValueError('Require all150 approved TRAINING dates')
        self.contract = json.loads((self.root/'manifest.json').read_text())
        expected = label_contract(predictive)
        if (self.contract.get('hash') != digest({k: v for k, v in self.contract.items() if k != 'hash'}) or
                any(self.contract.get(k) != value for k, value in expected.items()) or
                not isinstance(self.contract.get('parity_count'), int) or self.contract['parity_count'] < 1):
            raise ValueError('Entry-label source/config/sampling contract mismatch')
        status = json.loads((self.root/'status.json').read_text())
        if status.get('status') != 'complete' or status.get('completed') != self.days or status.get('validation_access') is not False:
            raise ValueError('All150 sealed label days must complete before fitting')
        self.receipts = {}
        for day in self.days:
            path, seal = self.root/(day+'.json'), self.root/(day+'.complete.json')
            expected_seal = dict(sha256=sha(path), contract_hash=self.contract['hash'])
            if json.loads(seal.read_text()) != expected_seal:
                raise ValueError('Entry-label day hash/seal mismatch: '+day)
            self.receipts[day] = dict(data_sha256=expected_seal['sha256'], seal_sha256=sha(seal))

    def load(self, day):
        if day not in self.days:
            raise ValueError('Only approved TRAINING label dates may be loaded')
        path, seal = self.root/(day+'.json'), self.root/(day+'.complete.json')
        if sha(path) != self.receipts[day]['data_sha256'] or sha(seal) != self.receipts[day]['seal_sha256']:
            raise ValueError('Entry-label hash changed after binding')
        report = json.loads(path.read_text())
        if report['day'] != day or report['contract_hash'] != self.contract['hash']:
            raise ValueError('Entry-label day contract mismatch')
        snapshots = self.predictive.load(day)
        tickers = self.predictive.source.items[day]['selected']
        keep, targets, counts = checked_records(report, snapshots, tickers, row_step=self.contract['row_step'])
        if report['parity_checked'] < min(counts['closed'], self.contract['parity_count']):
            raise ValueError('Missing required simulator parity evidence')
        rows = np.arange(0, len(snapshots['seconds']), self.contract['row_step'])
        observations = {key: np.asarray(snapshots[key][rows]) for key in ('features', 'candidate_valid', 'account')}
        return observations, keep, targets, counts


def observation_batch(observations):
    if isinstance(observations, (list, tuple)):
        if not observations:
            raise ValueError('Nonempty observations required')
        batch = {key: np.stack([row[key] for row in observations]) for key in ('features', 'account', 'candidate_valid')}
        batch['holding'] = np.stack([row.get('holding', np.zeros(8, np.float32)) for row in observations])
    else:
        batch = {key: np.asarray(observations[key]) for key in ('features', 'account', 'candidate_valid')}
        if batch['features'].ndim == 2:
            batch = {key: value[None] for key, value in batch.items()}
            batch['holding'] = np.asarray(observations.get('holding', np.zeros(8, np.float32)))[None]
        else:
            batch['holding'] = np.asarray(observations.get('holding', np.zeros((len(batch['features']), 8), np.float32)))
    n = len(batch['features'])
    if (batch['features'].shape != (n, 5, 112) or batch['account'].shape != (n, 8) or
            batch['holding'].shape != (n, 8) or batch['candidate_valid'].shape != (n, 5) or
            any(not np.isfinite(value).all() for value in batch.values())):
        raise ValueError('Causal scoring observation contract mismatch')
    return batch


def feature_matrix(observations, kind, *, model=None, device='cpu', batch_size=256):
    batch = observation_batch(observations)
    account = np.broadcast_to(batch['account'][:, None], (len(batch['features']), 5, 8))
    if kind == 'raw':
        return np.concatenate((batch['features'], account), axis=-1)
    if kind != 'attention' or model is None or not 1 <= batch_size <= 256:
        raise ValueError('Attention scoring requires a frozen model and bounded batch')
    model.eval()
    chunks = []
    with torch.no_grad():
        for begin in range(0, len(batch['features']), batch_size):
            values = {key: torch.as_tensor(value[begin:begin+batch_size], device=device) for key, value in batch.items()}
            chunks.append(model.encode(**values)[:, :5].cpu().numpy())
    tokens = np.concatenate(chunks)
    return np.concatenate((tokens, batch['features'][..., -14:], account), axis=-1)


def fit_ridge(features, targets):
    x, y = np.asarray(features, np.float64), np.asarray(targets, np.float64)
    if x.ndim != 2 or y.shape != (len(x),) or not len(x) or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('Finite nonempty fit inputs and separate targets required')
    mean, std = x.mean(axis=0), x.std(axis=0)
    std[std <= 1e-12*np.maximum(1., np.abs(mean))] = 1.
    z = (x-mean)/std
    center, response_mean = z.mean(axis=0), y.mean()
    gram = z.T@z-len(x)*np.outer(center, center)
    gram.flat[::x.shape[1]+1] += len(x)*REGULARIZATION
    coefficient = np.linalg.solve(gram, z.T@y-len(x)*center*response_mean)
    return dict(mean=mean, std=std, coefficient=coefficient,
                intercept=np.asarray(response_mean-center@coefficient))


def predict(features, parameters):
    scores = ((np.asarray(features, np.float64)-parameters['mean'])/parameters['std'])@parameters['coefficient']+parameters['intercept']
    if not np.isfinite(scores).all():
        raise ValueError('Nonfinite entry score')
    return scores


def diagnostics(scores, targets, days):
    positive = scores > 0
    return dict(count=len(targets), mse=float(np.mean((scores-targets)**2)), mae=float(np.abs(scores-targets).mean()),
        zero_mse=float(np.mean(targets**2)), zero_mae=float(np.abs(targets).mean()),
        correlation=float(np.corrcoef(scores, targets)[0, 1]) if scores.std()>1e-12 and targets.std()>1e-12 else 0.,
        positive_score_count=int(positive.sum()), positive_score_days=len(set(days[positive])),
        positive_score_standalone_net_sum=float(targets[positive].sum()),
        positive_score_mean_standalone_net=float(targets[positive].mean()) if positive.any() else None,
        positive_score_profitable_outcomes=int((targets[positive]>0).sum()),
        by_day=[dict(day=str(day), positive_score_count=int((positive & (days == day)).sum()),
                     positive_score_standalone_net_sum=float(targets[positive & (days == day)].sum()))
                for day in np.unique(days)],
        evidence='independent possibly overlapping entry outcomes; NOT persistent-account profit')


def write_bundle(root, kind, parameters, common, metrics):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    contract = dict(common, representation=kind, dimension=120 if kind == 'raw' else 86)
    write_json(root/'manifest.json', dict(contract=contract, contract_hash=digest(contract)))
    np.savez(root/'weights.npz', **parameters)
    write_json(root/'metrics.json', metrics)
    write_json(root/'complete.json', dict(files={name: sha(root/name) for name in ('manifest.json', 'weights.npz', 'metrics.json')}))


class Scorer:
    def __init__(self, parameters, contract, binding, model=None, device='cpu', batch_size=256):
        self.parameters, self.kind, self.binding = parameters, contract['representation'], binding
        self.row_step = contract['row_step']
        self.model, self.device, self.batch_size = model, device, batch_size

    def score(self, observations):
        batch = observation_batch(observations)
        features = feature_matrix(batch, self.kind, model=self.model, device=self.device, batch_size=self.batch_size)
        values = predict(features, self.parameters)
        return np.where(batch['candidate_valid'], values, -np.inf)


def load_bundle(root, dataset, forecast, *, device='cpu', batch_size=256):
    root = Path(root).resolve()
    seal = json.loads((root/'complete.json').read_text())
    if set(seal['files']) != {'manifest.json', 'weights.npz', 'metrics.json'}:
        raise ValueError('Entry-score bundle files mismatch')
    for name, expected in seal['files'].items():
        if sha(root/name) != expected:
            raise ValueError('Entry-score bundle hash mismatch')
    manifest = json.loads((root/'manifest.json').read_text())
    contract = manifest['contract']
    if (manifest['contract_hash'] != digest(contract) or contract['training_binding'] != dataset.binding or
            contract['source'] != implementation() or contract['base_source'] != source_identity() or
            contract['risk'] != Config().manifest() or contract['row_step'] != 10 or
            contract['fit_days']+contract['development_days'] != dataset.days or
            len(contract['fit_days']) != 120 or contract['representation'] not in KINDS or
            contract['dimension'] != (120 if contract['representation'] == 'raw' else 86)):
        raise ValueError('Entry-score source/data/risk/split contract mismatch')
    saved, forecast_hash = load_forecast(Path(forecast), dataset)
    if forecast_hash != contract['forecast_sha256'] or saved['contract_hash'] != contract['forecast_contract']:
        raise ValueError('Entry-score forecast lineage mismatch')
    with np.load(root/'weights.npz', allow_pickle=False) as archive:
        parameters = {key: archive[key].copy() for key in archive.files}
    width = contract['dimension']
    if (set(parameters) != {'mean', 'std', 'coefficient', 'intercept'} or
            any(parameters[key].shape != (width,) for key in ('mean', 'std', 'coefficient')) or
            parameters['intercept'].shape != () or np.any(parameters['std'] <= 0) or
            any(not np.isfinite(value).all() for value in parameters.values())):
        raise ValueError('Invalid entry-score coefficient/scaler shapes or values')
    model = None
    if contract['representation'] == 'attention':
        if device == 'cuda' and not torch.cuda.is_available():
            raise ValueError('CUDA unavailable; CPU scoring must be explicit')
        model = PredictivePolicy().to(device)
        model.load_state_dict(saved['policy'])
        model.requires_grad_(False).eval()
    binding = dict(root=str(root), contract=contract, contract_hash=manifest['contract_hash'],
                   complete_sha256=sha(root/'complete.json'), weights_sha256=seal['files']['weights.npz'])
    return Scorer(parameters, contract, binding, model, device, batch_size)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=DEFAULT_DEST)
    parser.add_argument('--snapshots', default='D:/TradingML/runtimes/rl-trading/v7/predictive-data-v1')
    parser.add_argument('--labels', default='D:/TradingML/runtimes/rl-trading/v7/entry-opportunity-full150')
    parser.add_argument('--forecast', type=Path, default=Path('D:/TradingML/runtimes/rl-trading/v7/forecast-seed1917/checkpoint_best.pt'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--device', choices=('cpu', 'cuda'), default='cuda')
    parser.add_argument('--batch-size', type=int, default=256)
    args = parser.parse_args(argv)
    if not 1 <= args.batch_size <= 256:
        raise ValueError('Encoding batch must be1..256')
    root = args.output.resolve()
    runtime = Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):
        raise ValueError('Runtime output required')
    data = PredictiveDataset(args.source, args.snapshots)
    labels = EntryLabels(data, args.labels)
    saved, forecast_hash = load_forecast(args.forecast, data.source)
    if args.device == 'cuda' and not torch.cuda.is_available():
        raise ValueError('CUDA unavailable; CPU requires explicit --device cpu')
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    model = PredictivePolicy().to(args.device)
    model.load_state_dict(saved['policy'])
    model.requires_grad_(False).eval()
    root.mkdir(parents=True, exist_ok=False)
    common = dict(recipe='fixed-ridge-single-entry-net-dollar-v1', training_binding=data.source.binding,
        snapshots_binding=data.binding, label_contract=labels.contract,
        label_manifest_sha256=sha(labels.root/'manifest.json'), label_receipts=labels.receipts,
        label_root=str(labels.root), forecast_sha256=forecast_hash, forecast_contract=saved['contract_hash'],
        source=implementation(), base_source=source_identity(), risk=Config().manifest(), row_step=10,
        fit_days=data.fit_days, development_days=data.development_days, regularization=REGULARIZATION,
        penalty='n_fit * .001; unpenalized intercept',
        scaler='fit-only mean/std; std<=1e-12*max(1,abs(mean)) replaced by1',
        git_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=Path(__file__).resolve().parents[4], text=True).strip(),
        roots=dict(source=str(Path(args.source).resolve()), snapshots=str(Path(args.snapshots).resolve()),
                   labels=str(labels.root), output=str(root), forecast=str(args.forecast.resolve())),
        device=args.device, encoding_batch=args.batch_size, numpy_version=np.__version__, torch_version=torch.__version__,
        input_account='flat initial cash for fitting; replay inputs may use actual causal account state',
        labels='all closed net dollars and observed nonfill zeros; causal ineligibility excluded; unliquidated fails',
        selection='neither representation selected; threshold0 fixed, no grid', validation_access=False)
    write_json(root/'manifest.json', dict(contract=common, contract_hash=digest(common)))
    matrices = {kind: [] for kind in KINDS}
    targets, dates, counts = [], [], []
    started = time.monotonic()
    try:
        for index, day in enumerate(data.days):
            observations, keep, y, count = labels.load(day)
            for kind in KINDS:
                values = feature_matrix(observations, kind, model=model, device=args.device, batch_size=args.batch_size)
                matrices[kind].append(values.reshape(-1, values.shape[-1])[keep])
            targets.append(y)
            dates.extend([day]*len(y))
            counts.append(dict(day=day, **count))
            if (index+1) % 10 == 0:
                status = dict(status='encoding', completed_days=index+1, total_days=150,
                              elapsed_seconds=time.monotonic()-started)
                write_json(root/'status.json', status)
                print(json.dumps(status), flush=True)
        y, days = np.concatenate(targets), np.asarray(dates)
        fit = np.isin(days, data.fit_days)
        development = np.isin(days, data.development_days)
        if not fit.any() or not development.any() or np.any(fit == development):
            raise ValueError('Invalid nonempty internal fit/development partition')
        reports = {}
        with threadpool_limits(limits=4):
            for kind in KINDS:
                x = np.concatenate(matrices[kind]).astype(np.float64)
                parameters = fit_ridge(x[fit], y[fit])
                metrics = dict(fit_count=int(fit.sum()), development_count=int(development.sum()),
                    alpha=int(fit.sum())*REGULARIZATION,
                    development=diagnostics(predict(x[development], parameters), y[development], days[development]))
                write_bundle(root/kind, kind, parameters, common, metrics)
                reports[kind] = metrics
                print(json.dumps(dict(kind=kind, **metrics)), flush=True)
        write_json(root/'counts.json', counts)
        write_json(root/'metrics.json', reports)
        write_json(root/'status.json', dict(status='complete', completed_days=150,
            elapsed_seconds=time.monotonic()-started, validation_access=False,
            evidence='two fixed standalone-entry predictors; no account-profit qualification'))
    except BaseException as exc:
        write_json(root/'status.json', dict(status='failed', error=repr(exc)))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
