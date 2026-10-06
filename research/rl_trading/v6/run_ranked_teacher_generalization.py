"""Bounded chronological ranked-teacher pilot admitted by its own underfit gate.

Continue verified TRAIN weights on the natural TRAIN population. Freeze the
fixed final checkpoint before opening later public development target shards.
The certified market input population stays intact on both dates.
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
from research.rl_trading.v6.bias_metrics import binary_report
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.opportunity_dataset import load_teacher
from research.rl_trading.v6.probe_teacher_sequence import subset
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.run_laptop_teacher import flatten
from research.rl_trading.v6.run_ranked_teacher_underfit import passes, coverage_report
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.teacher_forecast import configure
from research.rl_trading.v6.temporal_encoders import replace_encoder
from research.rl_trading.v6.training import train_session


def admit_gate(root, *, source_dir=None):
    plan = json.loads((root/'manifest.json').read_text())
    complete = json.loads((root/'complete.json').read_text())
    if (plan.get('version') != 'rl-v6-ranked-complete-head-underfit-v1' or
            plan.get('hash') != digest({k: v for k, v in plan.items() if k != 'hash'}) or
            complete.get('status') != 'completed' or not complete.get('passed') or
            not complete.get('reload_exact') or not passes(complete['metrics']) or
            complete.get('generalization_evaluated') is not False or
            plan.get('input_population_preserved') is not True or
            plan.get('sealed_labels_read') is not False or plan.get('workstation_gpu_used') is not False):
        raise ValueError('Exact complete-head TRAIN underfit gate required')
    if file_hash(root/'last.pt') != complete['checkpoint_sha256']:
        raise ValueError('Underfit checkpoint changed')
    source_dir = Path(__file__).parent if source_dir is None else source_dir
    if not plan.get('source_files_sha256') or any(file_hash(source_dir/n) != h for n, h in plan['source_files_sha256'].items()):
        raise ValueError('Model/objective source changed since underfit')
    return plan, complete


def gate_targets(candidates, keys):
    by_key = {(d.close_us, d.episode_uid, bool(len(d.held_index))): d for d in candidates}
    wanted = [tuple(k) for k in keys]
    if len(by_key) != len(candidates) or len(set(wanted)) != len(wanted) or wanted != sorted(wanted) or any(k not in by_key for k in wanted):
        raise ValueError('Underfit target identity/clock/state binding changed')
    selected = []; last = None; order = 0
    for key in wanted:
        d = by_key[key]
        if d.close_us != last: last = d.close_us; order = 0
        selected.append(replace(d, order_index=order)); order += 1
    return tuple(selected)


def build_policy(ranking, device, *, width=128):
    policy = RankedBracketActorCritic(config=ranking, width=width, wait_hold=True).to(device)
    replace_encoder(policy, 'tcn', structured=True)
    configure(policy, hierarchical=True, shared_heads=False)
    policy.independent_episode_supervision = True
    return policy


def evaluate_probabilities(policy, session, targets, device):
    """Read current logits from the real evaluation; no extra forward/state step."""
    original = policy.decide; rows = []
    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        logits = policy.decoder.ticker_outputs.logits
        if policy.decoder.supervision_index is None or logits.shape != (1, 4):
            raise ValueError('Independent teacher target axis required for probability audit')
        rows.append(logits.detach().softmax(-1)[0].cpu().numpy())
        return result
    policy.decide = capture
    try:
        metrics = asdict(train_session(policy, None, session, targets, (), device=device,
            evaluation=True, evaluate_train=session.role == 'train', teacher_loss='branch-balanced-v3', regression_weights=(0., 0.)))
    finally: policy.decide = original
    if len(rows) != len(targets): raise ValueError('Probability/target row count changed')
    p = np.asarray(rows); held = np.asarray([bool(len(d.held_index)) for d in targets])
    actual = np.asarray([(3 if d.token == 1+len(session.listings) else 2) if len(d.held_index)
                         else (0 if d.token else 1) for d in targets], np.int64)
    reports = {name: binary_report(actual[mask] == label, p[mask, label])
               for name, label, mask in (('ENTRY', 0, ~held), ('WAIT', 1, ~held), ('HOLD', 2, held), ('EXIT', 3, held))}
    return metrics, reports, dict(probability=p, action=actual,
        close_us=np.asarray([d.close_us for d in targets], np.int64), held=held,
        episode_uid=np.asarray([d.episode_uid for d in targets], dtype=str),
        listing_index=np.asarray([int(d.held_index[0]) if len(d.held_index) else d.soft_tokens[1]-1 for d in targets], np.int64))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--underfit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-runtime', type=Path, default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes'))
    parser.add_argument('--development-day', default='2026-08-24')
    parser.add_argument('--epochs', type=int, default=10)
    args = parser.parse_args(argv)
    runtime = Path('D:/TradingML/runtimes').resolve(); output = args.output.resolve(); gate = args.underfit.resolve()
    if (not runtime.is_dir() or not output.is_relative_to(runtime) or not gate.is_relative_to(runtime) or
            output.exists() or not torch.cuda.is_available() or not 1 <= args.epochs <= 10):
        raise ValueError('Fresh bounded laptop CUDA pilot required')
    prior, complete = admit_gate(gate)
    if prior['teacher_loss'] != 'branch-balanced-v3' or prior['regression_weights'] != [0., 0.] or prior['auxiliary_weights'] != dict(ratio=1., forecast=1., quality=1., future_quality=1.):
        raise ValueError('Exact admitted objective required')
    train_day = prior['arguments']['day']; seconds = prior['arguments']['seconds']; tickers = prior['arguments']['tickers']
    if not 1 <= seconds <= 600 or args.development_day <= train_day: raise ValueError('Chronological bounded dates required')
    from research.rl_trading.v6 import saved_label_audit as source, published_market_audit as market
    os.environ['RL_V6_LABEL_AUDIT_RUNTIME'] = str(args.source_runtime.resolve())
    output.mkdir(); started = time.perf_counter()
    def write(name, value): (output/name).write_text(json.dumps(value, indent=2), encoding='utf-8')
    def prepare(day, role):
        write('progress.json', dict(phase='verifying_'+role, day=day))
        active, entry, _, teacher, bankroot, _ = source.session(day)
        if entry['role'] != role: raise ValueError('Only explicit public TRAIN/development roles admitted')
        ma, _, me, mroot, _ = market.session(day)
        if active['sha256'] != prior['dataset_sha256'] or ma['sha256'] != prior['market_dataset_sha256']:
            raise ValueError('Public dataset identity changed since underfit')
        names = source.saved_symbols(str(bankroot), entry['bank_certificate_sha256'])
        ids = sorted(i for i, name in names.items() if name in tickers)
        missing = sorted(set(tickers)-{names[i] for i in ids})
        if len(ids) != len(tickers) or missing: raise ValueError('Target ticker coverage changed: '+str(missing))
        full = open_session(bankroot, runtime_root=source.runtime(), previous_root=source.mapped(entry['previous_root']))
        labels, _ = load_teacher(teacher, full, runtime_root=source.runtime(), audit_development=True,
                                audit_listing_ids=ids, market_root=mroot)
        begin = int(datetime.fromisoformat(day+'T04:00:00').replace(tzinfo=ZoneInfo('America/New_York')).timestamp()*1_000_000)
        packed, targets = subset(full, labels, full.listings, begin, begin+seconds*1_000_000-1)
        if packed.listings != full.listings or not targets: raise ValueError('Full market input population required')
        scope = dict(day=day, role=role, input_listings=list(packed.listings), target_ids=ids,
            begin_us=begin, end_us=begin+seconds*1_000_000, bank_certificate_sha256=entry['bank_certificate_sha256'],
            market_certificate_sha256=me['sha256'], context_split_receipt_sha256=packed.context_split_receipt_sha256,
            coverage=coverage_report(targets, len(packed.listings)))
        write(role+'-scope.json', scope); del full, labels; gc.collect()
        return packed, targets, scope
    training, targets, scope = prepare(train_day, 'train')
    if (scope['input_listings'] != prior['input_listings'] or scope['bank_certificate_sha256'] != prior['bank_certificate_sha256'] or
            scope['context_split_receipt_sha256'] != prior['context_split_receipt_sha256'] or
            scope['market_certificate_sha256'] != prior['market_certificate_sha256']):
        raise ValueError('TRAIN input binding changed')
    ranking = MarketAttentionConfig(**prior['ranking'])
    if asdict(ranking) != asdict(MarketAttentionConfig(**source.published()[1]['ranking'])): raise ValueError('Market ranking contract changed')
    torch.manual_seed(17); torch.set_num_threads(4); device = torch.device('cuda')
    policy = build_policy(ranking, device)
    policy.load_state_dict(torch.load(gate/'last.pt', map_location=device, weights_only=True), strict=True)
    replay = asdict(train_session(policy, None, training, gate_targets(targets, prior['target_keys']), (), device=device,
        evaluation=True, evaluate_train=True, teacher_loss='branch-balanced-v3', regression_weights=(0., 0.)))
    if replay != complete['metrics']: raise ValueError('Admitted underfit checkpoint replay changed')
    write('underfit-replay.json', dict(exact=True, checkpoint_sha256=complete['checkpoint_sha256'], metrics=replay))
    plan = dict(version='rl-v6-ranked-natural-generalization-v1', source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        epochs=args.epochs, initialization='passed_TRAIN_underfit_weights_optimizer_reset', learning_rate=3e-4, weight_decay=1e-4,
        underfit_manifest_sha256=file_hash(gate/'manifest.json'), underfit_checkpoint_sha256=complete['checkpoint_sha256'],
        train_day=train_day, development_day=args.development_day, seconds=seconds, tickers=tickers,
        checkpoint_selection='fixed_final_epoch_before_development_target_load', input_population_preserved=True,
        calibration='none_no_development_tuning', sealed_labels_read=False, workstation_gpu_used=False,
        scope='bounded_19_target_ticker_600second_pilot_not_final_full_market_training',
        source_files_sha256={p.name: file_hash(p) for p in Path(__file__).parent.glob('*.py')})
    plan['hash'] = digest(plan); write('manifest.json', plan)
    load_env_files(discover_env_files(Path.cwd()), verbose=False)
    import wandb
    logger = wandb.init(project='rl-trading-v6', name=output.name, mode='online', dir=str(output), config=plan)
    if logger is None or logger.settings.mode != 'online': raise ValueError('Online W&B required')
    write('wandb.json', dict(url=logger.url, id=logger.id))
    optimizer = torch.optim.AdamW(policy.parameters(), lr=3e-4, weight_decay=1e-4)
    try:
        for epoch in range(1, args.epochs+1):
            fit = asdict(train_session(policy, optimizer, training, targets, (), device=device,
                teacher_loss='branch-balanced-v3', regression_weights=(0., 0.),
                progress_callback=lambda p: write('progress.json', dict(phase='training', epoch=epoch, **p))))
            metrics, reports, probabilities = evaluate_probabilities(policy, training, targets, device)
            record = dict(epoch=epoch, training=fit, train_fit=metrics, probabilities=reports)
            with (output/'metrics.jsonl').open('a', encoding='utf-8') as stream: stream.write(json.dumps(record)+'\n')
            logger.log(flatten(record), step=epoch); write('train-result.json', record)
            torch.save(dict(model=policy.state_dict(), optimizer=optimizer.state_dict(), epoch=epoch,
                            manifest_sha256=file_hash(output/'manifest.json')), output/f'epoch-{epoch:02d}.pt')
            print(json.dumps(dict(epoch=epoch, train_f1=metrics['action_class_f1'], ratio_mae=metrics['allocation_ratio_mae'])), flush=True)
        torch.save(policy.state_dict(), output/'selected.pt')
        selection = dict(epoch=args.epochs, checkpoint_sha256=file_hash(output/'selected.pt'), frozen_before_development_targets=True)
        write('selection.json', selection); np.savez_compressed(output/'train-probabilities.npz', **probabilities)
        restored = build_policy(ranking, device)
        restored.load_state_dict(torch.load(output/'selected.pt', map_location=device, weights_only=True))
        repeated, _, _ = evaluate_probabilities(restored, training, targets, device)
        if repeated != metrics: raise ValueError('Natural TRAIN checkpoint replay changed')
        del policy, optimizer, training, targets; gc.collect(); torch.cuda.empty_cache()
        development, dev_targets, _ = prepare(args.development_day, 'development')
        if file_hash(output/'selected.pt') != selection['checkpoint_sha256']: raise ValueError('Frozen checkpoint changed')
        evaluated, reports, probabilities = evaluate_probabilities(restored, development, dev_targets, device)
        write('development.json', dict(metrics=evaluated, probabilities=reports))
        np.savez_compressed(output/'development-probabilities.npz', **probabilities)
        logger.log({**flatten(evaluated, 'development'), **flatten(reports, 'development_probability')}, step=args.epochs+1)
        write('complete.json', dict(status='completed', train_metrics=metrics, development_metrics=evaluated,
            development_probabilities=reports, checkpoint_sha256=selection['checkpoint_sha256'], train_reload_exact=True,
            underfit_gate_verified=True, generalization_evaluated=True, sealed_labels_read=False, workstation_gpu_used=False,
            production_teacher_certified=False, elapsed_seconds=time.perf_counter()-started, wandb_url=logger.url))
        logger.summary['completion_status'] = 'completed'
        for name in ('manifest.json', 'selection.json', 'complete.json', 'metrics.jsonl', 'development.json', 'train-scope.json', 'development-scope.json'):
            logger.save(str(output/name), base_path=str(output), policy='now')
    finally: logger.finish()
    return 0


if __name__ == '__main__': raise SystemExit(main())
