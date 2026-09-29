"""Train the causal V5 policy from audited dynamic teacher sessions.

The sealed test inventory is checked but never opened by this launcher.
Checkpoint selection uses development closed-loop model replay, not teacher P&L.
"""
from __future__ import annotations

import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import sys
sys.dont_write_bytecode = True
from pathlib import Path
REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

import argparse
import json
import math
import shutil
import subprocess

import numpy as np
import torch

from research.mlops.env import discover_env_files, load_env_files
from research.mlops.wandb_utils import init_wandb
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v1.model_v5 import DynamicMarketPolicy
from research.rl_trading.v1.v5_policy_replay import rollout_session
from research.rl_trading.v1.v5_replay_artifacts import save_replay
from research.rl_trading.v1.v5_session_data import load_session
from research.rl_trading.v1.v5_training import train_chronological_session
from research.rl_trading.v1.v5_training_plan import load_training_plan
from research.rl_trading.v2.config import Config
from src.market_engine.level_book_store import read, write
from src.runtime_paths import runtime_root

VERSION = 'rl-trading-v5-causal-dynamic-bc-train-v1'


def _qualified(reports: list[dict]) -> bool:
    """Require an active, terminally reconciled, positive-net dev policy."""
    return (bool(reports) and all(item['valid_terminal'] and
            item['buy_fills'] > 0 and item['completed_position_rows'] > 0 and
            math.isfinite(item['net_profit']) and
            math.isfinite(item['max_drawdown']) for item in reports) and
            sum(item['net_profit'] for item in reports) > 0)


def _ticker_ids(tickers: tuple[str, ...], vocabulary: dict[str, int],
                unknown_id: int) -> np.ndarray:
    return np.asarray([vocabulary.get(ticker, unknown_id)
                       for ticker in tickers], dtype=np.int64)


def _save_checkpoint(path: Path, payload: dict):
    temporary = path.with_suffix('.pt.tmp')
    torch.save(payload, temporary)
    os.replace(temporary, path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--split-manifest', type=Path, required=True)
    parser.add_argument('--teacher-audit', type=Path, required=True)
    parser.add_argument('--feature-roots', type=Path, required=True,
                        help='Runtime JSON mapping date to certified feature shard root')
    parser.add_argument('--execution-roots', type=Path, required=True,
                        help='Runtime JSON mapping date to certified replay grid root')
    parser.add_argument('--run-name', required=True)
    parser.add_argument('--epochs', type=int, default=20)
    parser.add_argument('--replay-every', type=int, default=5)
    parser.add_argument('--seconds-per-chunk', type=int, default=64)
    parser.add_argument('--learning-rate', type=float, default=3e-4)
    parser.add_argument('--weight-decay', type=float, default=.01)
    parser.add_argument('--width', type=int, default=128)
    parser.add_argument('--seed', type=int, default=17)
    parser.add_argument('--device', type=int, default=0)
    parser.add_argument('--wandb-project', default='rl-trading-v5')
    parser.add_argument('--wandb-entity', default='')
    parser.add_argument('--wandb-mode', choices=('online', 'offline', 'disabled'),
                        default='online')
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args(argv)
    if (not 1 <= args.epochs <= 200 or not 1 <= args.replay_every <= args.epochs or
            not 1 <= args.seconds_per_chunk <= 120 or args.learning_rate <= 0 or
            args.width < 16 or args.run_name in ('.', '..') or
            Path(args.run_name).name != args.run_name):
        parser.error('Invalid V5 campaign bounds or run name')
    runtime = runtime_root().resolve()
    if not runtime.is_dir():
        raise ValueError('Required runtime root unavailable')
    features_path, execution_path = (path.resolve() for path in
                                     (args.feature_roots, args.execution_roots))
    if any(not path.is_relative_to(runtime) for path in
           (features_path, execution_path)):
        raise ValueError('V5 source maps must reside under runtime')
    plan = load_training_plan(args.split_manifest, args.teacher_audit,
                              read(features_path), read(execution_path), runtime)
    sessions = {source.date:load_session(source.supervision_root,
        source.feature_root, runtime) for source in plan.train + plan.development}
    if any(sessions[source.date].split != source.split
           for source in plan.train + plan.development):
        raise ValueError('V5 loaded session differs from audited split')
    vocabulary = {ticker:index + 1 for index, ticker in enumerate(sorted({ticker
        for source in plan.train for ticker in sessions[source.date].binding.tickers}))}
    if not vocabulary:
        raise ValueError('No certified train identities')
    torch.manual_seed(args.seed)
    device = torch.device(f'cuda:{args.device}')
    if not torch.cuda.is_available():
        raise ValueError('Full-market V5 training requires CUDA')
    torch.cuda.set_device(device)
    model = DynamicMarketPolicy(features=sessions[plan.train[0].date].binding.feature_count,
        ticker_vocabulary=len(vocabulary), width=args.width).to(device)
    if any(session.binding.feature_count != model.feature.in_features
           for session in sessions.values()):
        raise ValueError('V5 causal feature dimensions differ across sessions')
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.learning_rate,
                                  weight_decay=args.weight_decay)
    commit = subprocess.check_output(['git', '-C', str(REPO), 'rev-parse', 'HEAD'],
                                     text=True).strip()
    config = dict(version=VERSION, run_name=args.run_name, source_commit=commit,
        split_hash=plan.split_hash, teacher_audit_hash=plan.audit_hash,
        feature_roots_hash=file_hash(features_path),
        execution_roots_hash=file_hash(execution_path),
        train_dates=[source.date for source in plan.train],
        development_dates=[source.date for source in plan.development],
        sealed_test_dates=[source.date for source in plan.test_sealed],
        ticker_vocabulary=vocabulary, model=dict(width=args.width,
            history_seconds=model.history_seconds), training=dict(
            epochs=args.epochs, replay_every=args.replay_every,
            seconds_per_chunk=args.seconds_per_chunk,
            learning_rate=args.learning_rate, weight_decay=args.weight_decay,
            seed=args.seed), replay_config=Config().manifest())
    config['config_hash'] = digest(config)
    root = runtime / 'rl-trading' / 'v5' / 'train' / args.run_name
    root.mkdir(parents=True, exist_ok=True)
    checks = root / 'checkpoints'
    checks.mkdir(exist_ok=True)
    config_path = root / 'config.json'
    if config_path.exists():
        if read(config_path) != config:
            raise ValueError('Existing V5 run name belongs to a different contract')
    else:
        write(config_path, config)
    latest = checks / 'checkpoint_latest.pt'
    start_epoch = 0
    if args.resume:
        if not latest.is_file():
            raise ValueError('Resume requested but no V5 checkpoint exists')
        saved = torch.load(latest, map_location=device, weights_only=False)
        if saved['config_hash'] != config['config_hash']:
            raise ValueError('V5 resume checkpoint contract changed')
        model.load_state_dict(saved['model'])
        optimizer.load_state_dict(saved['optimizer'])
        start_epoch = int(saved['epoch'])
    elif latest.exists():
        raise ValueError('Existing V5 run requires --resume')
    load_env_files(discover_env_files(REPO), verbose=False)
    wandb_dir = root / 'wandb'
    wandb_dir.mkdir(exist_ok=True)
    wandb = init_wandb(entity=args.wandb_entity, project=args.wandb_project,
        run_name=args.run_name, config=config, run_dir=wandb_dir,
        mode=args.wandb_mode, timeout_seconds=60,
        run_id=read(root / 'wandb.json')['run_id'] if args.resume and
            (root / 'wandb.json').exists() else None,
        resume_mode='must' if args.resume and (root / 'wandb.json').exists()
            else 'never') if args.wandb_mode != 'disabled' else None
    if wandb and not (root / 'wandb.json').exists():
        write(root / 'wandb.json', dict(run_id=wandb.id, url=wandb.url))
    best_path = root / 'best_development.json'
    best = read(best_path) if best_path.exists() else None
    history = root / 'metrics.jsonl'
    try:
        for epoch in range(start_epoch + 1, args.epochs + 1):
            if (root / 'STOP').exists():
                break
            rows = []
            for source in plan.train:
                session = sessions[source.date]
                result = train_chronological_session(model, optimizer,
                    session.binding, session.orders, session.trajectory,
                    session.positions,
                    _ticker_ids(session.binding.tickers, vocabulary,
                                model.unknown_ticker_id),
                    initial_cash=session.initial_cash, device=device,
                    seconds_per_chunk=args.seconds_per_chunk)
                row = dict(epoch=epoch, split='train', date=source.date,
                           **vars(result))
                with history.open('a', encoding='utf-8') as output:
                    output.write(json.dumps(row, sort_keys=True) + '\n')
                rows.append(row)
                if not math.isfinite(row['loss']):
                    raise FloatingPointError('Nonfinite V5 session loss')
            checkpoint = dict(config_hash=config['config_hash'], epoch=epoch,
                              model=model.state_dict(), optimizer=optimizer.state_dict())
            _save_checkpoint(latest, checkpoint)
            metrics = dict(epoch=epoch,
                **{f'train/{key}':sum(row[key] for row in rows)/len(rows)
                   for key in ('loss', 'action_loss', 'size_loss',
                               'action_accuracy', 'buy_size_mae')},
                **{f'train/{key}':sum(row[key] for row in rows)
                   for key in ('teacher_orders', 'active_seconds',
                               'sampled_empty_seconds', 'optimization_steps')})
            if epoch == 1 or epoch % args.replay_every == 0 or epoch == args.epochs:
                replay_checkpoint = checks / f'checkpoint_epoch_{epoch:03d}.pt'
                shutil.copyfile(latest, replay_checkpoint)
                model.eval()
                reports = []
                for source in plan.development:
                    session = sessions[source.date]
                    account = rollout_session(model, session.binding,
                        source.execution_root,
                        _ticker_ids(session.binding.tickers, vocabulary,
                                    model.unknown_ticker_id), Config(),
                        device=device,
                        seconds_per_chunk=args.seconds_per_chunk)
                    artifact = save_replay(account, session.binding,
                        source.execution_root, replay_checkpoint,
                        split='development', source_commit=commit)
                    report = dict(date=source.date, artifact=str(artifact),
                                  **account.summary())
                    reports.append(report)
                    del account
                metrics.update({'development/net_profit':sum(row['net_profit']
                    for row in reports), 'development/fees':sum(row['fees']
                    for row in reports), 'development/buy_fills':sum(
                    row['buy_fills'] for row in reports),
                    'development/max_drawdown_worst':max(row['max_drawdown']
                    for row in reports), 'development/turnover':sum(
                    row['turnover'] for row in reports)})
                for row in reports:
                    for key in ('net_profit', 'fees', 'buy_fills', 'sell_fills',
                                'max_drawdown', 'turnover', 'open_positions'):
                        metrics[f'development/{row["date"]}/{key}'] = row[key]
                eligible = _qualified(reports)
                if eligible and (best is None or
                                 metrics['development/net_profit'] > best['net_profit']):
                    chosen = checks / 'checkpoint_best_development.pt'
                    shutil.copyfile(replay_checkpoint, chosen)
                    best = dict(epoch=epoch,
                        net_profit=metrics['development/net_profit'],
                        checkpoint=str(chosen), checkpoint_hash=file_hash(chosen),
                        reports=reports)
                    write(best_path, best, immutable=False)
                model.train()
            with history.open('a', encoding='utf-8') as output:
                output.write(json.dumps(metrics, sort_keys=True) + '\n')
            if wandb:
                wandb.log(metrics, step=epoch)
            print(metrics, flush=True)
    finally:
        if wandb:
            wandb.finish()
    write(root / 'training_complete.json', dict(version=VERSION,
        status='stopped' if (root / 'STOP').exists() else 'completed',
        config_hash=config['config_hash'], latest_checkpoint_hash=file_hash(latest)
        if latest.exists() else None, best=best), immutable=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
