"""TRAIN-only learnability gate on the real ranked-market teacher path.

Bound target tickers/time, never the certified input listing population.
No development or sealed targets are opened and no generalization is run.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
import argparse
from dataclasses import asdict, replace
from datetime import datetime
import gc
import json
from pathlib import Path
import subprocess
import time
from zoneinfo import ZoneInfo

import numpy as np
import torch
from research.mlops.env import discover_env_files, load_env_files
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.action_contract import ActionAxes
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.opportunity_dataset import load_teacher
from research.rl_trading.v6.probe_teacher_sequence import subset
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.run_full_head_underfit import underfit_rows
from research.rl_trading.v6.run_laptop_teacher import flatten
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.teacher_forecast import configure, CONTRACT
from research.rl_trading.v6.temporal_encoders import replace_encoder
from research.rl_trading.v6.training import train_session


def select_targets(labels, listings, per_class):
    """Unique balanced current targets with every future class represented."""
    mapping = {1: 0, 0: 1, 5: 2, 2: 3}
    actions = np.asarray([mapping[ActionAxes(listings, len(d.held_index)).action_class(d.token)] for d in labels])
    future = np.full((len(labels), 5), -1, np.int64)
    for i, decision in enumerate(labels):
        if decision.forecast_actions is not None:
            future[i, :len(decision.forecast_actions)] = decision.forecast_actions
    rows = underfit_rows(actions, future, per_class=per_class)
    selected = sorted((labels[i] for i in rows), key=lambda d: (d.close_us, d.episode_uid, len(d.held_index)))
    ordered = []; last = None; order = 0
    for decision in selected:
        if decision.close_us != last:
            last = decision.close_us; order = 0
        ordered.append(replace(decision, order_index=order)); order += 1
    return tuple(ordered), rows.tolist()


def passes(metrics):
    """Same complete-head tolerances as the local gate; absent targets fail."""
    names = ('wait', 'enter_long', 'hold', 'exit_long')
    current = all(metrics['action_class_counts'].get(n, 0) > 0 and
                  metrics['action_class_f1'].get(n, 0) >= .95 for n in names)
    forecasts = metrics.get('forecast_label_metrics', ())
    future = len(forecasts) == 5 and all(
        h['labels'].get(n, {}).get('count', 0) > 0 and h['labels'].get(n, {}).get('f1', 0) >= .95
        for h in forecasts for n in ('ENTRY', 'WAIT', 'HOLD', 'EXIT'))
    ratio = metrics.get('allocation_ratio_mae')
    quality = metrics.get('action_quality_mae') or {}
    future_quality = metrics.get('forecast_quality_mae', ())
    return bool(current and future and metrics.get('allocation_targets', 0) > 0 and
                ratio is not None and np.isfinite(ratio) and ratio <= .02 and
                all(quality.get(n) is not None and np.isfinite(quality[n]) and quality[n] <= .02 for n in ('ENTRY', 'EXIT')) and
                len(future_quality) == 5 and all(h.get(n) is not None and np.isfinite(h[n]) and h[n] <= .02
                                                for h in future_quality for n in ('ENTRY', 'EXIT')))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-runtime', type=Path, default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes'))
    parser.add_argument('--day', default='2026-07-31')
    parser.add_argument('--tickers', nargs='+', default=['AAPL', 'NVDA', 'MU', 'CYCU', 'SNDK'])
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--rows-per-class', type=int, choices=(16, 32, 64), default=32)
    parser.add_argument('--epochs', type=int, default=200)
    args = parser.parse_args(argv)
    runtime = Path('D:/TradingML/runtimes').resolve(); output = args.output.resolve()
    if not runtime.is_dir() or not output.is_relative_to(runtime) or output.exists():
        raise ValueError('Fresh laptop runtime required')
    if not 1 <= args.seconds <= 600 or not 1 <= args.epochs <= 400 or not 1 <= len(set(args.tickers)) == len(args.tickers) <= 19:
        raise ValueError('Bounded TRAIN probe requires <=600 seconds, <=400 epochs, unique <=19 target tickers')
    if not torch.cuda.is_available(): raise ValueError('Laptop CUDA required')
    from research.rl_trading.v6 import saved_label_audit as source, published_market_audit as market
    os.environ['RL_V6_LABEL_AUDIT_RUNTIME'] = str(args.source_runtime.resolve())
    active, entry, _, teacher, bankroot, _ = source.session(args.day)
    if entry['role'] != 'train': raise ValueError('Only public TRAIN targets admitted')
    output.mkdir(); started = time.perf_counter()
    def write(name, value):
        (output/name).write_text(json.dumps(value, indent=2), encoding='utf-8')
    write('progress.json', dict(phase='verifying_banks', day=args.day))
    names = source.saved_symbols(str(bankroot), entry['bank_certificate_sha256'])
    ids = sorted(i for i, name in names.items() if name in args.tickers)
    if len(ids) != len(args.tickers): raise ValueError('Target tickers must resolve uniquely')
    full = open_session(bankroot, runtime_root=source.runtime(), previous_root=source.mapped(entry['previous_root']))
    ma, _, me, mroot, _ = market.session(args.day)
    write('progress.json', dict(phase='verifying_target_shards', day=args.day, input_listings=len(full.listings)))
    labels, _ = load_teacher(teacher, full, runtime_root=source.runtime(), audit_development=True,
                            audit_listing_ids=ids, market_root=mroot)
    begin = int(datetime.fromisoformat(args.day+'T04:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1_000_000)
    # Input population is unchanged; only prefix length and target count are bounded.
    session, candidates = subset(full, labels, full.listings, begin, begin+args.seconds*1_000_000-1)
    if session.listings != full.listings: raise ValueError('Certified input population changed')
    targets, rows = select_targets(candidates, len(session.listings), args.rows_per_class)
    del full, labels; gc.collect()
    ranking = MarketAttentionConfig(**source.published()[1]['ranking'])
    manifest = dict(version='rl-v6-ranked-complete-head-underfit-v1',
        source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        arguments={k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
        device=torch.cuda.get_device_name(), seed=17, ranking=asdict(ranking), forecast_contract=CONTRACT,
        input_listings=list(session.listings), target_listing_ids=ids, target_rows=rows,
        target_keys=[(d.close_us, d.episode_uid, bool(len(d.held_index))) for d in targets],
        dataset_sha256=active['sha256'], market_dataset_sha256=ma['sha256'],
        bank_certificate_sha256=entry['bank_certificate_sha256'], market_certificate_sha256=me['sha256'],
        context_split_receipt_sha256=session.context_split_receipt_sha256,
        input_population_preserved=True, generalization_evaluated=False, sealed_labels_read=False,
        workstation_gpu_used=False, teacher_loss='branch-balanced-v3',
        criterion='each current/future class F1 >= .95; sizing and current/future ENTRY/EXIT quality MAE <= .02; complete class coverage required',
        auxiliary_weights=dict(ratio=1., forecast=1., quality=1., future_quality=1.), regression_weights=[0., 0.],
        source_files_sha256={p.name: file_hash(p) for p in Path(__file__).parent.glob('*.py')})
    manifest['hash'] = digest(manifest); write('manifest.json', manifest)
    torch.manual_seed(17); torch.set_num_threads(4)
    def model():
        policy = RankedBracketActorCritic(config=ranking, wait_hold=True).cuda()
        replace_encoder(policy, 'tcn', structured=True)
        configure(policy, hierarchical=True, shared_heads=False)
        policy.independent_episode_supervision = True
        return policy
    policy = model(); optimizer = torch.optim.AdamW(policy.parameters(), lr=3e-4, weight_decay=1e-4)
    load_env_files(discover_env_files(Path.cwd()), verbose=False)
    import wandb
    logger = wandb.init(project='rl-trading-v6', name=output.name, mode='online', dir=str(output), config=manifest)
    if logger is None or logger.settings.mode != 'online': raise ValueError('Online W&B required')
    write('wandb.json', dict(url=logger.url, id=logger.id)); passed = False
    try:
        def evaluate(p):
            return asdict(train_session(p, None, session, targets, (), device=torch.device('cuda'),
                evaluation=True, evaluate_train=True, teacher_loss='branch-balanced-v3', regression_weights=(0., 0.)))
        for epoch in range(1, args.epochs+1):
            before = time.perf_counter()
            fit = asdict(train_session(policy, optimizer, session, targets, (), device=torch.device('cuda'),
                teacher_loss='branch-balanced-v3', regression_weights=(0., 0.),
                progress_callback=lambda p: write('progress.json', dict(phase='training', epoch=epoch, **p))))
            if epoch != 1 and epoch % 5: continue
            metrics = evaluate(policy); passed = passes(metrics)
            record = dict(epoch=epoch, fit=fit, metrics=metrics, passed=passed, seconds=time.perf_counter()-before)
            with (output/'metrics.jsonl').open('a', encoding='utf-8') as stream: stream.write(json.dumps(record)+'\n')
            logger.log(flatten(record), step=epoch); write('result.json', record)
            torch.save(dict(model=policy.state_dict(), optimizer=optimizer.state_dict(), epoch=epoch,
                            manifest_sha256=file_hash(output/'manifest.json')), output/f'epoch-{epoch:03d}.pt')
            write('progress.json', dict(phase='evaluated', epoch=epoch, passed=passed, seconds=record['seconds']))
            print(json.dumps(dict(epoch=epoch, passed=passed, f1=metrics['action_class_f1'], seconds=record['seconds'])), flush=True)
            if passed: break
        # Evaluate the actual final epoch even when it is not a regular checkpoint.
        metrics = evaluate(policy); passed = passes(metrics)
        torch.save(policy.state_dict(), output/'last.pt')
        restored = model(); restored.load_state_dict(torch.load(output/'last.pt', weights_only=True))
        if evaluate(restored) != metrics: raise ValueError('Exact TRAIN checkpoint replay failed')
        write('complete.json', dict(status='completed', epoch=epoch, passed=passed, metrics=metrics,
            checkpoint_sha256=file_hash(output/'last.pt'), reload_exact=True, generalization_evaluated=False,
            production_teacher_certified=False, elapsed_seconds=time.perf_counter()-started, wandb_url=logger.url))
        logger.summary['completion_status'] = 'completed'; logger.summary['underfit_passed'] = passed
        for name in ('manifest.json', 'metrics.jsonl', 'complete.json'): logger.save(str(output/name), base_path=str(output), policy='now')
    finally: logger.finish()
    return 0


if __name__ == '__main__': raise SystemExit(main())
