"""Sequential matched laptop benchmark with underfit admission before validation.

Direct command: python -B -m research.rl_trading.v6.run_architecture_benchmark
 --output D:/TradingML/runtimes/v6-matched-architectures-20261011
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
import argparse
from dataclasses import asdict
import gc
import json
from pathlib import Path
import subprocess
import time
import traceback
import uuid
import numpy as np
import torch
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.architecture_benchmark import VERSION, Candidate, WindowTeacher, matched_sizes, loss, report, passes
from research.rl_trading.v6.architecture_benchmark_data import FIELDS, training, validation
from research.rl_trading.v6.laptop_resources import LaptopGpuPacer
from research.rl_trading.v6.run_laptop_teacher import flatten
from research.mlops.env import discover_env_files, load_env_files


def write(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    torch.save(value, temporary); temporary.replace(path)


def batch(data, indices, device):
    names=FIELDS+tuple(name for name in ('multi','multi_present','liquidity','market') if name in data)
    return {name: data[name][indices].to(device) for name in names}


def representation(model,item):
    extras={name:item[name] for name in ('multi','multi_present','liquidity','market') if name in item}
    return model.representation(item['raw'],item['present'],item['held'],**extras)


@torch.no_grad()
def evaluate(model, data, device, pacer):
    model.eval(); values = {name: [] for name in ('current', 'quality', 'ratio', 'future', 'future_quality')}
    for begin in range(0, len(data['action']), 8):
        pacer.begin_activity()
        item = batch(data, slice(begin, begin + 8), device)
        context = representation(model,item)
        output = model.heads(context, item['held'][:, 0] > 0)
        future = model.forecast(context, model.forecast_gru, steps=5)
        for name, value in dict(current=output.logits.softmax(-1), quality=output.quality,
                ratio=model.size(context).sigmoid().flatten(), future=future.exp(),
                future_quality=model.forecast.quality_predictions).items():
            values[name].append(value.cpu().numpy())
        pacer()
    arrays = {name: np.concatenate(value) for name, value in values.items()}
    return report(data, **arrays), arrays


def admit_validation(metrics, reload_exact):
    if not reload_exact or not passes(metrics):
        raise ValueError('Candidate failed complete underfit/replay gate; validation denied')


def resume_optimizer(model, completed_updates, joint_updates):
    # A state saved at the last joint update still owns the joint optimizer.
    # The next loop iteration performs the phase transition exactly once.
    heads = completed_updates > joint_updates
    parameters = model.head_parameters() if heads else model.parameters()
    return torch.optim.AdamW(parameters, lr=.003 if heads else .001, weight_decay=.0001)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, default=Path('D:/TradingML/runtimes/v6-own-channel-overfit-20261011'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-runtime', type=Path, default=Path(r'\\DESKTOP-SAAI85T\Workstation-D\TradingML\runtimes'))
    parser.add_argument('--joint-updates', type=int, default=256)
    parser.add_argument('--head-updates', type=int, default=384)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    parser.add_argument('--transformer-search', action='store_true', help='Frozen 32 transformer variants')
    parser.add_argument('--snapshot-runtime', type=Path, help='Reuse authenticated completed benchmark snapshots')
    parser.add_argument('--candidate-indices', type=int, nargs='+', help='Explicit 1-based transformer subset for bounded smoke tests')
    parser.add_argument('--wandb-project', default='rl-trading-v6', help='Dedicated W&B project; bound into the experiment manifest')
    parser.add_argument('--enriched-runtime',type=Path,help='Certified frozen enriched feature panel')
    args = parser.parse_args(argv)
    if not args.wandb_project or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in args.wandb_project):
        raise ValueError('Explicit alphanumeric W&B project name required')
    runtime = Path('D:/TradingML/runtimes').resolve(); output = args.output.resolve()
    if not runtime.is_dir() or not output.is_relative_to(runtime) or not args.parent.resolve().is_relative_to(runtime):
        raise ValueError('Explicit laptop runtime root required')
    if not 1 <= args.joint_updates <= 1200 or not 1 <= args.head_updates <= 1200:
        raise ValueError('Bounded matched update budget required')
    if args.candidate_indices is not None and (not args.transformer_search or len(set(args.candidate_indices)) != len(args.candidate_indices)
            or any(not 1 <= index <= 32 for index in args.candidate_indices)):
        raise ValueError('Unique bounded transformer indices required')
    if output.exists() and not args.resume:
        raise ValueError('Fresh output required, or explicit authenticated --resume')
    import psutil
    torch.set_num_threads(2); torch.set_num_interop_threads(2)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.allow_tf32 = False
    torch.backends.cuda.matmul.allow_tf32 = False
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    if psutil.virtual_memory().available < 8 * 1024**3:
        raise RuntimeError('Laptop requires 8 GiB available host memory')
    output.mkdir(exist_ok=args.resume)
    write(output / 'progress.json', dict(phase='authenticating_train', active=1, queued=32 if args.transformer_search else 3, completed=0, failed=0))
    snapshot_plan = None
    if args.snapshot_runtime is not None:
        snapshot = args.snapshot_runtime.resolve()
        if not snapshot.is_relative_to(runtime): raise ValueError('Snapshot runtime escaped laptop root')
        snapshot_plan = json.loads((snapshot / 'manifest.json').read_text())
        proof_path=snapshot/('final-comparison-audit.json' if (snapshot/'final-comparison-audit.json').exists() else 'comparison-audit.json')
        proof = json.loads(proof_path.read_text())
        authenticated=(proof.get('source_and_snapshot_hashes_verified') is True or (
            snapshot_plan['version']=='rl-v6-transformer-search-32-v1' and proof.get('candidates')==32
            and proof.get('manifest_and_checkpoints_verified') is True and proof.get('fresh_process_train_replays')==32
            and proof.get('validation_aggregations_exact')==32))
        if proof['status'] != 'passed' or proof['sealed_labels_read'] or not authenticated:
            raise ValueError('Verified completed benchmark snapshot required')
        if snapshot_plan['parent_manifest_sha256'] != file_hash(args.parent / 'manifest.json') or snapshot_plan['train_sha256'] != file_hash(snapshot / 'train.pt'):
            raise ValueError('Snapshot TRAIN/parent binding changed')
        if snapshot_plan['normalization_sha256'] != file_hash(args.parent / 'normalization.json') or snapshot_plan['channel_normalization_sha256'] != file_hash(args.parent / 'channel-normalization.json'):
            raise ValueError('Snapshot normalization binding changed')
        train = torch.load(snapshot / 'train.pt', weights_only=False)
        parent = None
        bindings = [dict(day=d) for d in snapshot_plan['train_days']]
    else:
        parent, train, bindings = training(args.parent)
    base = json.loads((args.parent / 'normalization.json').read_text())
    extra = json.loads((args.parent / 'channel-normalization.json').read_text())
    enrichment=None;enriched_normalization=None
    if args.enriched_runtime:
        panel=args.enriched_runtime.resolve()
        if not panel.is_relative_to(runtime) or not args.transformer_search or not args.snapshot_runtime:
            raise ValueError('Enrichment requires explicit transformer snapshot/runtime')
        enrichment=json.loads((panel/'complete.json').read_text())
        if enrichment['status']!='prepared' or enrichment['source_manifest_sha256']!=file_hash(args.snapshot_runtime/'manifest.json'):
            raise ValueError('Enriched panel source binding changed')
        if file_hash(panel/'train.pt')!=enrichment['train_sha256'] or file_hash(panel/'normalization.json')!=enrichment['normalization_sha256']:
            raise ValueError('Enriched TRAIN/normalization hash changed')
        enriched_train=torch.load(panel/'train.pt',weights_only=False)
        if enriched_train['keys']!=train['keys'] or any(not torch.allclose(enriched_train[k],train[k],equal_nan=True,atol=0,rtol=0) for k in FIELDS):
            raise ValueError('Enrichment changed frozen TRAIN targets or original inputs')
        train=enriched_train;enriched_normalization=json.loads((panel/'normalization.json').read_text())
    # Preserve global RNG while capacity is chosen without any outcomes.
    with torch.random.fork_rng():
        if args.transformer_search:
            from research.rl_trading.v6 import transformer_search
            sizes = transformer_search.plan(base, extra)
            if args.candidate_indices: sizes = [sizes[i-1] for i in args.candidate_indices]
        else:
            sizes = matched_sizes(base, extra)
    def build_model(spec):
        if args.enriched_runtime:
            from research.rl_trading.v6 import enriched_teacher
            return enriched_teacher.build(spec,base,extra,enriched_normalization)
        return (transformer_search.build(spec, base, extra) if args.transformer_search
            else WindowTeacher(Candidate(spec['architecture'], spec['width']), base, extra))
    if args.enriched_runtime:
        for spec in sizes:spec['parameters']=sum(p.numel() for p in build_model(spec).parameters())
    train_path = output / 'train.pt'
    if train_path.exists():
        old_train = torch.load(train_path, weights_only=False)
        if old_train['keys'] != train['keys'] or any(not torch.equal(old_train[k], train[k]) if k != 'ratio'
                else not torch.allclose(old_train[k], train[k], equal_nan=True, atol=0, rtol=0) for k in FIELDS):
            raise ValueError('Resumed causal TRAIN snapshot changed')
    else:
        save(train_path, train)
    plan = dict(version=transformer_search.VERSION if args.transformer_search else VERSION, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        wandb_project=args.wandb_project,
        enriched_runtime=str(args.enriched_runtime.resolve()) if args.enriched_runtime else None,
        enrichment_sha256=file_hash(args.enriched_runtime/'complete.json') if args.enriched_runtime else None,
        snapshot_runtime=str(args.snapshot_runtime.resolve()) if args.snapshot_runtime else None,
        snapshot_manifest_sha256=file_hash(args.snapshot_runtime/'manifest.json') if args.snapshot_runtime else None,
        source_runtime=str(args.source_runtime.resolve()), parent_runtime=str(args.parent.resolve()),
        parent_manifest_sha256=file_hash(args.parent / 'manifest.json'), train_sha256=file_hash(train_path),
        normalization_sha256=file_hash(args.parent / 'normalization.json'), channel_normalization_sha256=file_hash(args.parent / 'channel-normalization.json'),
        source_files_sha256={p.name: file_hash(p) for p in Path(__file__).parent.glob('*.py')},
        candidates=sizes, train_days=[b['day'] for b in bindings], train_rows=len(train['action']),
        current_counts=torch.bincount(train['action'], minlength=4).tolist(),
        validation_days=['2026-08-24', '2026-08-25'], validation_seconds=600, validation_rows_per_day=2048,
        validation_sampling='label_blind_SHA256_identity_uniform_sample',
        validation_status='previously_exposed_public_development_exploratory_not_independent_holdout',
        feature_scope='120_strict_prior_observed_rows_own_channels_plus_reference_held_state',
        policy_scope='local_causal_window_benchmark_not_full_ranked_market_policy',
        supervision='current_hierarchical_action_allocation_quality_and_five_step_forced_plus_free_labels',
        joint_updates=args.joint_updates, head_updates=args.head_updates,
        sample_exposure_per_candidate=len(train['action']) * (args.joint_updates + args.head_updates),
        optimizer=dict(name='AdamW', joint_lr=.001, head_lr=.003, weight_decay=.0001, clip_norm=1.),
        seed=17, microbatch=8, cpu_threads=2, priority='BelowNormal', gpu_duty=.5,
        deterministic_algorithms=True, tf32=False, replay_parameter_flags='frozen_representation_trainable_heads',
        vram_reserve_bytes=4*1024**3, host_reserve_bytes=8*1024**3,
        sealed_labels_read=False, workstation_gpu_used=False, default_training_changed=False)
    plan['hash'] = digest(plan)
    manifest_path = output / 'manifest.json'
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text())
        if existing != plan:
            raise ValueError('Resume experiment/source/data/budget binding changed')
    else:
        write(manifest_path, plan)
    print(json.dumps(dict(phase='prepared', candidates=sizes, train_rows=len(train['action']), exposure=plan['sample_exposure_per_candidate'])), flush=True)
    if args.prepare_only:
        write(output / 'progress.json', dict(phase='prepared_not_training', queued=len(sizes), active=0, completed=0, failed=0))
        return 0
    if not torch.cuda.is_available():
        raise ValueError('Laptop CUDA required')
    device = torch.device('cuda'); pacer = LaptopGpuPacer(device, duty_cycle=.5)
    pacer.check_reserve(); load_env_files(discover_env_files(Path.cwd()), verbose=False)
    import wandb
    results = []; validation_data = None
    mass = torch.bincount(train['future'][train['future'] >= 0], minlength=4).float()
    weights = (mass.sum() / mass).sqrt(); weights /= (weights * mass).sum() / mass.sum(); weights = weights.to(device)
    try:
        for spec in sizes:
            name = spec['architecture']; root = output / name; root.mkdir(exist_ok=True)
            completed = root / 'complete.json'
            if completed.exists():
                result = json.loads(completed.read_text())
                if result['manifest_sha256'] != file_hash(manifest_path) or file_hash(root / 'selected.pt') != result['checkpoint_sha256']:
                    raise ValueError('Completed candidate binding changed')
                results.append(result); continue
            torch.manual_seed(17)
            model = build_model(spec).to(device)
            optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.0001)
            start = 0; frozen = None
            state_receipt = root / 'state.json'
            if state_receipt.exists():
                receipt = json.loads(state_receipt.read_text())
                state_path = root / receipt['file']
                if state_path.parent != root or not state_path.name.startswith('state-') or state_path.suffix != '.pt':
                    raise ValueError('Resume checkpoint path escaped candidate runtime')
                if receipt['manifest_sha256'] != file_hash(manifest_path) or receipt['sha256'] != file_hash(state_path):
                    raise ValueError('Resume checkpoint binding changed')
                state = torch.load(state_path, weights_only=False, map_location='cpu'); start = state['update']
                model.load_state_dict(state['model'], strict=True)
                optimizer = resume_optimizer(model, start, args.joint_updates)
                optimizer.load_state_dict(state['optimizer'])
            wandb_path = root / 'wandb.json'
            run_id = json.loads(wandb_path.read_text())['id'] if wandb_path.exists() else None
            logger = wandb.init(project=args.wandb_project, group=output.name, name=output.name + '-' + name,
                mode='online', dir=str(root), config={**plan, 'candidate': spec}, id=run_id, resume='allow' if run_id else None)
            if logger is None or logger.settings.mode != 'online':
                raise ValueError('Online W&B required')
            if logger.project != args.wandb_project:
                raise ValueError('W&B project identity mismatch')
            write(wandb_path, dict(id=logger.id, url=logger.url, project=logger.project))
            started = time.perf_counter(); metrics = None
            try:
                for update in range(start, args.joint_updates + args.head_updates):
                    if update == args.joint_updates:
                        optimizer = torch.optim.AdamW(model.head_parameters(), lr=.003, weight_decay=.0001)
                    if update >= args.joint_updates and frozen is None:
                        model.eval(); contexts = []
                        with torch.no_grad():
                            for begin in range(0, len(train['action']), 8):
                                pacer.begin_activity(); item = batch(train, slice(begin, begin + 8), device)
                                contexts.append(representation(model,item).detach()); pacer()
                        frozen = torch.cat(contexts)
                    model.train(); optimizer.zero_grad(set_to_none=True)
                    order = np.random.default_rng(17 + update).permutation(len(train['action']))
                    total_loss = 0.
                    for begin in range(0, len(order), 8):
                        pacer.begin_activity(); indices = torch.as_tensor(order[begin:begin+8])
                        item = batch(train, indices, device)
                        context = frozen[indices.to(device)] if frozen is not None else representation(model,item)
                        value = loss(model, context, item, weights)
                        if not torch.isfinite(value):
                            raise RuntimeError('Nonfinite candidate objective')
                        (value * len(indices) / len(order)).backward()
                        total_loss += float(value.detach()) * len(indices) / len(order); pacer()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.); optimizer.step()
                    if (update + 1) % 16 == 0 or update + 1 == args.joint_updates + args.head_updates:
                        metrics, train_arrays = evaluate(model, train, device, pacer)
                        record = dict(update=update+1, sample_exposure=(update+1)*len(order), loss=total_loss,
                            phase='joint' if update < args.joint_updates else 'frozen_heads', train=metrics, underfit_passed=passes(metrics))
                        logger.log(flatten(record), step=update+1)
                        with (root / 'metrics.jsonl').open('a', encoding='utf-8') as stream:
                            stream.write(json.dumps(record) + '\n')
                        state_path = root / f'state-{update+1:05d}-{uuid.uuid4().hex}.pt'
                        save(state_path, dict(update=update+1, model=model.state_dict(), optimizer=optimizer.state_dict()))
                        write(state_receipt, dict(file=state_path.name, sha256=file_hash(state_path), manifest_sha256=file_hash(manifest_path)))
                        write(output / 'progress.json', dict(phase='training', architecture=name, update=update+1,
                            total_updates=args.joint_updates+args.head_updates, active=1, completed=len(results), queued=len(sizes)-1-len(results), failed=0,
                            elapsed_seconds=time.perf_counter()-started, entry_f1=metrics['current']['ENTRY']['f1'], gate_passed=passes(metrics)))
                        print(json.dumps(dict(architecture=name, update=update+1, loss=total_loss,
                            f1={k:v['f1'] for k,v in metrics['current'].items()}, passed=passes(metrics))), flush=True)
                save(root / 'selected.pt', model.state_dict())
                reloaded = build_model(spec).to(device)
                reloaded.load_state_dict(torch.load(root / 'selected.pt', weights_only=True, map_location=device))
                # requires_grad flags are not tensors in state_dict. CUDA may
                # choose a different inference kernel when these flags differ.
                # Restore the final phase exactly, not just checkpoint weights.
                reloaded.head_parameters()
                replay, replay_arrays = evaluate(reloaded, train, device, pacer)
                exact = replay == metrics and all(np.array_equal(replay_arrays[k], train_arrays[k]) for k in train_arrays)
                if not exact:
                    raise ValueError('Candidate checkpoint metrics failed exact reload replay')
                admitted = passes(replay)
                write(root / 'overfit-gate.json', dict(passed=admitted, reload_exact=exact, metrics=replay))
                val_metrics = None
                if admitted:
                    admit_validation(replay, exact)
                    if validation_data is None:
                        val_path = output / 'validation.pt'; val_receipt = output / 'validation.json'
                        if val_path.exists():
                            proof = json.loads(val_receipt.read_text())
                            if file_hash(val_path) != proof['sha256'] or proof['manifest_sha256'] != file_hash(manifest_path):
                                raise ValueError('Validation snapshot binding changed')
                            validation_data = torch.load(val_path, weights_only=False)
                        else:
                            write(output / 'progress.json', dict(phase='authenticating_public_validation', architecture=name, active=1))
                            if args.snapshot_runtime:
                                old_receipt = json.loads((args.snapshot_runtime/'validation.json').read_text())
                                if old_receipt['manifest_sha256'] != file_hash(args.snapshot_runtime/'manifest.json') or old_receipt['sha256'] != file_hash(args.snapshot_runtime/'validation.pt'):
                                    raise ValueError('Snapshot validation binding changed')
                                validation_data = torch.load(args.snapshot_runtime/'validation.pt', weights_only=False)
                                if args.enriched_runtime:
                                    if file_hash(args.enriched_runtime/'validation.pt')!=enrichment['validation_sha256']:
                                        raise ValueError('Enriched validation hash changed')
                                    enriched_validation=torch.load(args.enriched_runtime/'validation.pt',weights_only=False)
                                    if enriched_validation['keys']!=validation_data['keys'] or any(not torch.allclose(enriched_validation[k],validation_data[k],equal_nan=True,atol=0,rtol=0) for k in FIELDS):
                                        raise ValueError('Enrichment changed validation inputs/labels')
                                    validation_data=enriched_validation
                                scope = old_receipt['scope']
                            else:
                                validation_data, scope = validation(args.source_runtime, parent, plan['validation_days'])
                            save(val_path, validation_data)
                            write(val_receipt, dict(sha256=file_hash(val_path), manifest_sha256=file_hash(manifest_path), scope=scope,
                                first_admitted_checkpoint_sha256=file_hash(root/'selected.pt')))
                    val_metrics, arrays = evaluate(reloaded, validation_data, device, pacer)
                    write(root / 'validation-metrics.json', val_metrics)
                    np.savez_compressed(root / 'validation-predictions.npz', **arrays)
                    logger.log(flatten(val_metrics, 'validation'), step=args.joint_updates+args.head_updates+1)
                result = dict(architecture=name, width=spec['width'], parameters=spec['parameters'],
                    sample_exposure=plan['sample_exposure_per_candidate'], overfit_passed=admitted, reload_exact=exact,
                    train=replay, validation=val_metrics, validation_evaluated=val_metrics is not None,
                    checkpoint_sha256=file_hash(root/'selected.pt'), manifest_sha256=file_hash(manifest_path), wandb_url=logger.url)
                write(completed, result); results.append(result)
                logger.summary['overfit_passed'] = admitted
                logger.summary['validation_evaluated'] = val_metrics is not None
            finally:
                logger.finish()
            del model, optimizer, reloaded, frozen; gc.collect(); torch.cuda.empty_cache()
            write(output / 'results.json', dict(candidates=results, sealed_labels_read=False, production_teacher_certified=False))
            if args.transformer_search: write(output / 'selection.json', transformer_search.selection(results))
        write(output / 'complete.json', dict(status='completed', candidates=results,
            equal_sample_exposure=len({r['sample_exposure'] for r in results}) == 1,
            validation_is_exploratory=True, sealed_labels_read=False, workstation_gpu_used=False))
        write(output / 'progress.json', dict(phase='completed', active=0, queued=0, completed=len(results),
            failed=sum(not r['overfit_passed'] for r in results)))
    except BaseException:
        write(output / 'failure.json', dict(traceback=traceback.format_exc(), time=time.time()))
        write(output / 'progress.json', dict(phase='failed', active=0, completed=len(results), failed=1,
            queued=max(0, len(sizes)-len(results)-1)))
        raise
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
