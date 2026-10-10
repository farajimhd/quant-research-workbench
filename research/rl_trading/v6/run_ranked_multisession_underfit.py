"""Six-session TRAIN learnability gate on the full ranked-market input path."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import time
import random
import traceback
import torch
from research.mlops.env import discover_env_files, load_env_files
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.ranked_teacher_data import load_prepared, coverage_report
from research.rl_trading.v6.ranked_multisession_metrics import pool_gate_metrics
from research.rl_trading.v6.run_ranked_teacher_generalization import admit_gate, build_policy, gate_targets, exact_metrics
from research.rl_trading.v6.run_ranked_teacher_underfit import passes
from research.rl_trading.v6.run_laptop_teacher import flatten
from research.rl_trading.v6.training import train_session


def selected_targets(plan, day, cache_sha, candidates):
    rows = [r for r in plan['rows'] if r['day'] == day]
    if not rows or any(r['cache_sha256'] != cache_sha for r in rows):
        raise ValueError('Selected TRAIN day/cache binding changed')
    keys = sorted(tuple(r['key']) for r in rows)
    return gate_targets(candidates, keys)


def verify_coverage(selection, sessions):
    counts = [0]*4; future = [[0]*4 for _ in range(5)]
    for s,t in sessions:
        r = coverage_report(t, len(s.listings))
        for i,n in enumerate(('ENTRY','WAIT','HOLD','EXIT')):
            counts[i] += r['current_counts'][n]
            for h in range(5): future[h][i] += r['future_counts'][h][n]
    if counts != [32]*4 or counts != selection['current_counts'] or future != selection['future_counts'] or any(n < 2 for horizon in future for n in horizon):
        raise ValueError('Actual pooled current/future label coverage changed')


def validate_continuation(root, epoch, plan):
    """Bind recovery to the same inputs, model, objective and saved evaluation."""
    old = json.loads((root/'manifest.json').read_text())
    if old.get('hash') != digest({k:v for k,v in old.items() if k != 'hash'}):
        raise ValueError('Continuation manifest hash changed')
    if any(old.get(k) is not False for k in ('sealed_labels_read', 'development_labels_read', 'generalization_evaluated', 'workstation_gpu_used')):
        raise ValueError('Continuation must remain TRAIN-only on laptop')
    runner = Path(__file__).name
    if old.get('clocks_per_chunk',32) != plan.get('clocks_per_chunk',32):
        raise ValueError('Continuation chronological optimizer chunk changed')
    if old['source_files_sha256'].get(runner) not in {
            'e90f8d6f679d715b1d2df0e4b7a23a58c29187e4513a60f8138be3f272d0b101',
            plan['source_files_sha256'][runner]}:
        raise ValueError('Unreviewed continuation runner source')
    for key in ('version', 'dataset_sha256', 'market_dataset_sha256', 'feature_contract',
                'forecast_contract', 'sessions', 'selection_sha256', 'normalization_sha256',
                'width', 'ranking', 'teacher_loss', 'regression_weights', 'auxiliary_weights',
                'learning_rate', 'weight_decay', 'laptop_resources', 'cpu_saved_tensors',
                'activation_checkpointing', 'criterion'):
        if old.get(key) != plan.get(key):
            raise ValueError(f'Continuation contract changed: {key}')
    for name, sha in old['source_files_sha256'].items():
        if name != Path(__file__).name and plan['source_files_sha256'].get(name) != sha:
            raise ValueError(f'Continuation calculation source changed: {name}')
    records = [json.loads(line) for line in (root/'metrics.jsonl').read_text().splitlines()]
    matches = [r for r in records if r['epoch'] == epoch]
    if len(matches) != 1 or not 1 <= epoch < plan['epochs']:
        raise ValueError('Continuation requires one saved evaluation before final epoch')
    checkpoint = root/f'epoch-{epoch:03d}.pt'
    return checkpoint, matches[0], dict(parent_manifest_sha256=file_hash(root/'manifest.json'),
        checkpoint_sha256=file_hash(checkpoint), parent_epoch=epoch,
        parent_run=str(root.resolve()), exact_uninterrupted_resume=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--underfit', type=Path, required=True, help='Verified single-session normalization/input contract')
    parser.add_argument('--normalization-source-dir', type=Path, help='Exact immutable producer of the frozen normalizer; never model initialization')
    parser.add_argument('--selection', type=Path, required=True)
    parser.add_argument('--initial-cache', type=Path, required=True)
    parser.add_argument('--initial-source', type=Path, required=True)
    parser.add_argument('--additional-cache', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--epochs', type=int, default=400)
    parser.add_argument('--width', type=int, choices=(128, 512), default=128)
    parser.add_argument('--activation-checkpointing', action='store_true')
    parser.add_argument('--history-microbatch', type=int, default=16)
    parser.add_argument('--gpu-duty-cycle', type=float, default=.75)
    parser.add_argument('--cpu-saved-tensors', action='store_true')
    parser.add_argument('--natural-train', action='store_true', help='TRAIN-only full target population; separate from the balanced admission contract')
    parser.add_argument('--clocks-per-chunk', type=int, default=32, help='Chronological optimizer block size; smaller blocks bound CPU-offloaded graphs')
    parser.add_argument('--continue-from', type=Path, help='Preserved run; new output and pre-training replay required')
    parser.add_argument('--continue-epoch', type=int)
    args = parser.parse_args(argv)
    if not 1 <= args.history_microbatch <= 32 or not 0 < args.gpu_duty_cycle <= .8:
        raise ValueError('Laptop requires bounded history batches and at most 80% duty cycle')
    if not 1 <= args.clocks_per_chunk <= 32:
        raise ValueError('Chronological computation chunks must be between 1 and 32 clocks')
    runtime = Path('D:/TradingML/runtimes').resolve()
    paths = [args.underfit, args.selection, args.initial_cache, args.initial_source, args.additional_cache, args.output]
    if (args.continue_from is None) != (args.continue_epoch is None):
        raise ValueError('Continuation run and epoch must be supplied together')
    if args.natural_train and args.continue_from is not None:
        raise ValueError('Natural TRAIN baseline requires fresh weights, not balanced-run continuation')
    if args.continue_from is not None:
        paths.append(args.continue_from)
    if not runtime.is_dir() or any(not p.resolve().is_relative_to(runtime) for p in paths) or args.output.exists() or not 1 <= args.epochs <= 400 or not torch.cuda.is_available():
        raise ValueError('Fresh bounded laptop CUDA experiment required')
    if args.normalization_source_dir is not None and not args.normalization_source_dir.resolve().is_relative_to(runtime):
        raise ValueError('Normalizer producer snapshot must be in laptop runtime')
    prior, _ = admit_gate(args.underfit, source_dir=args.normalization_source_dir)
    if not prior.get('normalization_sha256'):
        raise ValueError('Verified frozen TRAIN normalization required')
    selection = json.loads(args.selection.read_text())
    if selection.get('version') != 'rl-v6-six-session-underfit-sampling-preflight-v1' or selection.get('hash') != digest({k:v for k,v in selection.items() if k != 'hash'}) or selection.get('sealed_targets_read') is not False or selection.get('development_targets_read') is not False:
        raise ValueError('Authenticated TRAIN-only pooled selection required')
    if len(selection['rows']) != 128 or len({(r['day'], tuple(r['key'])) for r in selection['rows']}) != 128:
        raise ValueError('128 unique TRAIN targets required')
    roots = [(args.initial_cache, args.initial_source)] + [(p, p/'source.json') for p in sorted(args.additional_cache.glob('2026-*'))]
    if len(roots) != 6:
        raise ValueError('Exactly six certified TRAIN sessions required')
    sessions = []; bindings = []; balanced_witness = []
    for root, source in roots:
        proof = json.loads(source.read_text())
        if proof.get('hash') != digest({k:v for k,v in proof.items() if k != 'hash'}):
            raise ValueError('TRAIN source receipt changed')
        if proof['dataset_sha256'] != prior['dataset_sha256'] or proof['market_dataset_sha256'] != prior['market_dataset_sha256']:
            raise ValueError('TRAIN dataset authority changed')
        s, all_targets, _ = load_prepared(root, proof)
        day = s.day.isoformat(); cache_sha = file_hash(root/'prepared-train.pt')
        targets = selected_targets(selection, day, cache_sha, all_targets)
        balanced_witness.append((s,targets))
        if args.natural_train:
            targets=all_targets
        sessions.append((s, targets))
        bindings.append(dict(day=day, cache=str(root.resolve()), cache_sha256=cache_sha,
            source_sha256=file_hash(source), input_listings=list(s.listings), targets=len(targets),
            bank_certificate_sha256=s.source_certificate_sha256,
            context_split_receipt_sha256=s.context_split_receipt_sha256))
    if sorted(b['day'] for b in bindings) != sorted(selection['day_counts']) or len({b['day'] for b in bindings}) != 6:
        raise ValueError('TRAIN day coverage changed')
    verify_coverage(selection, balanced_witness)
    args.output.mkdir(); started = time.perf_counter()
    def write(name, value): (args.output/name).write_text(json.dumps(value, indent=2), encoding='utf-8')
    normalization = json.loads((args.underfit/'normalization.json').read_text())
    ranking = MarketAttentionConfig(**prior['ranking'])
    plan = dict(version='rl-v6-ranked-six-session-natural-underfit-v1' if args.natural_train else 'rl-v6-ranked-six-session-underfit-v1', source_commit=subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip(),
        arguments={k:str(v.resolve()) if isinstance(v,Path) else v for k,v in vars(args).items()},
        dataset_sha256=prior['dataset_sha256'], market_dataset_sha256=prior['market_dataset_sha256'],
        tickers=prior['arguments']['tickers'], feature_contract=prior['feature_contract'],
        forecast_contract=prior['forecast_contract'],
        sessions=bindings, selection_sha256=file_hash(args.selection), normalization_sha256=prior['normalization_sha256'],
        target_population='all_certified_cached_TRAIN_targets' if args.natural_train else 'balanced_128',
        normalization_origin='frozen_verified_single_TRAIN_contract_no_refitting', initialization='fresh_weights',
        epochs=args.epochs, width=args.width, activation_checkpointing=args.activation_checkpointing,
        clocks_per_chunk=args.clocks_per_chunk, system_ram_reserve_bytes=8*1024**3,
        cpu_saved_tensors=args.cpu_saved_tensors,
        laptop_resources=dict(history_microbatch=args.history_microbatch,duty_cycle=args.gpu_duty_cycle,reserve_bytes=4*1024**3),
        seed=17, learning_rate=3e-4, weight_decay=1e-4,
        ranking=prior['ranking'], teacher_loss='branch-balanced-v3', regression_weights=[0.,0.],
        auxiliary_weights=dict(ratio=1., forecast=1., quality=1., future_quality=1.),
        input_population_preserved=True, sealed_labels_read=False, development_labels_read=False,
        generalization_evaluated=False, workstation_gpu_used=False,
        criterion='same-checkpoint pooled full-head gate; current and future F1>=.95; all quality/sizing MAE<=.02; complete coverage',
        source_files_sha256={p.name:file_hash(p) for p in Path(__file__).parent.glob('*.py')})
    continuation = None
    if args.continue_from is not None:
        checkpoint, parent_record, continuation = validate_continuation(args.continue_from, args.continue_epoch, plan)
        plan['initialization'] = 'validated_parent_model_and_optimizer'
        plan['continuation'] = continuation
    plan['hash'] = digest(plan); write('manifest.json', plan); write('normalization.json', normalization)
    torch.manual_seed(17); torch.set_num_threads(4); device = torch.device('cuda')
    def model():
        policy=build_policy(ranking, device, width=args.width, normalization=normalization)
        policy.encoder.activation_checkpointing=args.activation_checkpointing
        policy.encoder.history_microbatch=args.history_microbatch
        from research.rl_trading.v6.laptop_resources import LaptopGpuPacer
        policy.resource_pacer=LaptopGpuPacer(device,duty_cycle=args.gpu_duty_cycle)
        policy.encoder.resource_pacer=policy.resource_pacer
        policy.cpu_saved_tensors=args.cpu_saved_tensors
        return policy
    policy = model(); optimizer = torch.optim.AdamW(policy.parameters(), lr=3e-4, weight_decay=1e-4)
    load_env_files(discover_env_files(Path.cwd()), verbose=False)
    import wandb
    logger = wandb.init(project='rl-trading-v6', name=args.output.name, dir=str(args.output), mode='online', config=plan)
    if logger is None or logger.settings.mode != 'online': raise ValueError('Online W&B required')
    write('wandb.json', dict(id=logger.id, url=logger.url))
    def evaluate(p):
        reports = []
        for s,t in sessions:
            evidence = {}
            report = asdict(train_session(p, None, s, t, (), device=device, evaluation=True,
                clocks_per_chunk=args.clocks_per_chunk,
                evaluate_train=True, teacher_loss='branch-balanced-v3', regression_weights=(0.,0.),
                regression_evidence=evidence))
            report.update(evidence); reports.append(report)
        return pool_gate_metrics(reports), reports
    passed = False
    try:
        start_epoch = 1
        if continuation is not None:
            write('progress.json', dict(phase='continuation_replay', epoch=args.continue_epoch))
            state = torch.load(checkpoint, map_location=device, weights_only=True)
            if state['epoch'] != args.continue_epoch or file_hash(checkpoint) != continuation['checkpoint_sha256']:
                raise ValueError('Continuation checkpoint identity changed')
            policy.load_state_dict(state['model']); optimizer.load_state_dict(state['optimizer'])
            replay_metrics, replay_reports = evaluate(policy)
            if not exact_metrics(replay_metrics, parent_record['metrics']) or not exact_metrics(replay_reports, parent_record['sessions']):
                raise ValueError('Parent checkpoint replay differs from saved evaluation')
            has_rng = 'torch_rng' in state and 'cuda_rng' in state and 'python_rng' in state
            if has_rng:
                torch.set_rng_state(state['torch_rng'].cpu())
                torch.cuda.set_rng_state_all([s.cpu() for s in state['cuda_rng']])
                random.setstate(state['python_rng'])
            write('continuation-verification.json', dict(**continuation, reload_exact=True,
                rng_restored=has_rng, rng_policy='restored' if has_rng else 'new_seed_17_continuation',
                generalization_evaluated=False))
            if not has_rng:
                torch.manual_seed(17); random.seed(17)
            logger.log({'continuation/parent_epoch': args.continue_epoch,
                        'continuation/reload_exact': True, 'continuation/rng_restored': has_rng}, step=args.continue_epoch)
            start_epoch = args.continue_epoch+1
        for epoch in range(start_epoch,args.epochs+1):
            for s,t in sessions:
                write('progress.json', dict(phase='training', epoch=epoch, day=s.day.isoformat()))
                train_session(policy, optimizer, s, t, (), device=device,
                    clocks_per_chunk=args.clocks_per_chunk,
                    teacher_loss='branch-balanced-v3', regression_weights=(0.,0.))
            if epoch != 1 and epoch % 5 and epoch != args.epochs: continue
            write('progress.json', dict(phase='evaluation', epoch=epoch))
            metrics, reports = evaluate(policy); passed = passes(metrics)
            record = dict(epoch=epoch, passed=passed, metrics=metrics, sessions=reports)
            write('result.json', record)
            with (args.output/'metrics.jsonl').open('a', encoding='utf-8') as stream: stream.write(json.dumps(record)+'\n')
            logger.log(flatten(record), step=epoch)
            temporary = args.output/f'epoch-{epoch:03d}.pt.tmp'
            torch.save(dict(model=policy.state_dict(), optimizer=optimizer.state_dict(), epoch=epoch,
                torch_rng=torch.get_rng_state(), cuda_rng=torch.cuda.get_rng_state_all(),
                python_rng=random.getstate()), temporary)
            temporary.replace(args.output/f'epoch-{epoch:03d}.pt')
            print(json.dumps(dict(epoch=epoch, passed=passed, f1=metrics['action_class_f1'], ratio=metrics['allocation_ratio_mae'])), flush=True)
            if passed: break
        torch.save(policy.state_dict(), args.output/'last.pt')
        restored = model(); restored.load_state_dict(torch.load(args.output/'last.pt', weights_only=True))
        repeated, repeated_reports = evaluate(restored)
        if not exact_metrics(repeated, metrics) or not exact_metrics(repeated_reports, reports):
            raise ValueError('Exact six-session checkpoint replay failed')
        write('complete.json', dict(status='completed', epoch=epoch, passed=passed, metrics=metrics,
            checkpoint_sha256=file_hash(args.output/'last.pt'), reload_exact=True,
            generalization_evaluated=False, production_teacher_certified=False,
            elapsed_seconds=time.perf_counter()-started, wandb_url=logger.url))
        logger.summary['completion_status']='completed'; logger.summary['underfit_passed']=passed
        for name in ('manifest.json','metrics.jsonl','complete.json','normalization.json'):
            logger.save(str(args.output/name), base_path=str(args.output), policy='now')
    except BaseException:
        write('failure.json', dict(status='failed', traceback=traceback.format_exc(),
            progress=json.loads((args.output/'progress.json').read_text()) if (args.output/'progress.json').exists() else None))
        raise
    finally: logger.finish()
    return 0


if __name__ == '__main__': raise SystemExit(main())
