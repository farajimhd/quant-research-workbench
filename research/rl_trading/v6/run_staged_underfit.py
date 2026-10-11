"""Authenticated laptop-only staged head underfit experiment, never final training."""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
import gc
import hashlib
import json
from pathlib import Path
import subprocess
import time
import traceback

import torch
from research.rl_trading.v1.common import digest, file_hash
from research.rl_trading.v6.run_channel_underfit import selected_targets, verify_coverage
from research.rl_trading.v6.run_ranked_teacher_generalization import build_policy, evaluate_probabilities, exact_metrics
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.ranked_teacher_data import load_prepared
from research.rl_trading.v6.channel_features import install
from research.rl_trading.v6.laptop_resources import LaptopGpuPacer
from research.rl_trading.v6.hierarchical_heads import forecast_class_weights
from research.rl_trading.v6.staged_heads import capture, configure, targets_from_packet, objective, VERSION
from research.rl_trading.v6.ranked_multisession_metrics import pool_gate_metrics
from research.rl_trading.v6.run_ranked_teacher_underfit import passes
from research.rl_trading.v6.run_laptop_teacher import flatten
from research.mlops.env import discover_env_files, load_env_files


def read_authenticated(path):
    value = json.loads(path.read_text())
    if value.get('hash') != digest({k: v for k, v in value.items() if k != 'hash'}):
        raise ValueError('Receipt hash changed: ' + str(path))
    return value


def restore_fit(path, sha256, binding, policy, optimizer, updates):
    if file_hash(path).lower() != sha256.lower():
        raise ValueError('Resume checkpoint hash changed')
    state = torch.load(path, map_location='cpu', weights_only=False)
    if state.get('binding') != binding or not 0 <= state.get('step', -1) <= updates:
        raise ValueError('Resume packet/objective binding changed')
    policy.load_state_dict(state['model'])
    optimizer.load_state_dict(state['optimizer'])
    torch.set_rng_state(state['torch_rng'])
    return state['step']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--checkpoint', type=Path, required=True)
    parser.add_argument('--checkpoint-sha256', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--updates', type=int, default=400)
    parser.add_argument('--resume-fit-state', type=Path)
    parser.add_argument('--resume-sha256')
    args = parser.parse_args(argv)
    root = Path('D:/TradingML/runtimes').resolve()
    if (not root.is_dir() or args.output.exists() or not torch.cuda.is_available() or
            not 1 <= args.updates <= 1200 or any(not p.resolve().is_relative_to(root)
            for p in (args.parent, args.checkpoint, args.output))):
        raise ValueError('Fresh bounded laptop runtime and CUDA required')
    if file_hash(args.checkpoint).lower() != args.checkpoint_sha256.lower():
        raise ValueError('Checkpoint binding changed')
    if bool(args.resume_fit_state) != bool(args.resume_sha256) or (args.resume_fit_state is not None and
            not args.resume_fit_state.resolve().is_relative_to(root)):
        raise ValueError('Resume requires a runtime checkpoint and explicit hash')
    parent = read_authenticated(args.parent / 'manifest.json')
    if (parent.get('version') != 'rl-v6-own-channel-balanced-underfit-v1' or
            parent.get('sealed_labels_read') is not False or parent.get('development_labels_read') is not False):
        raise ValueError('Authenticated own-channel TRAIN parent required')
    selection_path = Path(parent['arguments']['selection'])
    if file_hash(selection_path) != parent['selection_sha256']:
        raise ValueError('Selection binding changed')
    selection = read_authenticated(selection_path)
    if selection.get('sealed_targets_read') is not False or selection.get('development_targets_read') is not False:
        raise ValueError('TRAIN-only selection required')
    for name, key in (('normalization.json', 'normalization_sha256'),
                      ('channel-normalization.json', 'channel_normalization_sha256')):
        if file_hash(args.parent / name) != parent[key]:
            raise ValueError('Normalizer binding changed')
    if len(parent['sessions']) != 6 or len({b['day'] for b in parent['sessions']}) != 6:
        raise ValueError('Six unique TRAIN sessions required')
    torch.set_num_threads(2)
    import psutil
    psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    def check_host_reserve():
        if psutil.virtual_memory().available < 8 * 1024**3:
            raise RuntimeError('Laptop host RAM reserve below 8 GiB')
    check_host_reserve()
    args.output.mkdir()
    def write(name, value):
        temporary = args.output / (name + '.tmp')
        temporary.write_text(json.dumps(value, indent=2))
        temporary.replace(args.output / name)
    def save(name, value):
        temporary = args.output / (name + '.tmp')
        torch.save(value, temporary)
        temporary.replace(args.output / name)
    def session(binding):
        check_host_reserve()
        cache = Path(binding['cache'])
        source = Path(parent['arguments']['initial_source']) if binding['day'] == parent['sessions'][0]['day'] else cache / 'source.json'
        if file_hash(cache / 'prepared-train.pt') != binding['cache_sha256'] or file_hash(source) != binding['source_sha256']:
            raise ValueError('TRAIN cache/source changed')
        proof = read_authenticated(source)
        s, t, _ = load_prepared(cache, proof)
        if s.role != 'train' or s.day.isoformat() != binding['day']:
            raise ValueError('Session role/day changed')
        return s, selected_targets(selection, binding['day'], binding['cache_sha256'], t)
    def model(device, checkpoint):
        p = build_policy(MarketAttentionConfig(**parent['ranking']), device, width=parent['width'])
        install(p, json.loads((args.parent / 'normalization.json').read_text()),
                json.loads((args.parent / 'channel-normalization.json').read_text()))
        state = torch.load(checkpoint, map_location='cpu', weights_only=False)
        p.load_state_dict(state['model'] if 'model' in state else state)
        if device.type == 'cuda':
            p.encoder.history_microbatch = 8
            p.encoder.activation_checkpointing = True
            p.resource_pacer = LaptopGpuPacer(device, duty_cycle=.5)
            p.encoder.resource_pacer = p.resource_pacer
        return p
    plan = dict(version=VERSION, parent_manifest_sha256=file_hash(args.parent / 'manifest.json'),
        checkpoint_sha256=file_hash(args.checkpoint), arguments={k: str(v) for k, v in vars(args).items()},
        source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
        source_files_sha256={p.name: file_hash(p) for p in Path(__file__).parent.glob('*.py')},
        fitting='equal_selected_row_mass', acceptance='existing_weighted_full_head_gate_and_exact_reload',
        learning_rate=.003, weight_decay=.0001, cpu_threads=2, cpu_duty=.5, gpu_duty=.5,
        sealed_inspection=False, generalization_evaluated=False, workstation_training=False)
    plan['hash'] = digest(plan)
    write('manifest.json', plan)
    load_env_files(discover_env_files(Path.cwd()), verbose=False)
    import wandb
    logger = wandb.init(project='rl-trading-v6', name=args.output.name, dir=str(args.output), mode='online', config=plan)
    if logger is None or logger.settings.mode != 'online':
        raise ValueError('Online W&B required')
    write('wandb.json', dict(id=logger.id, url=logger.url))
    try:
        gpu = torch.device('cuda')
        p = model(gpu, args.checkpoint)
        packets, targets, coverage = [], [], []
        for index, binding in enumerate(parent['sessions']):
            write('progress.json', dict(phase='capture', day=binding['day'], active=1,
                completed=index, queued=5-index, failed=0))
            s, t = session(binding)
            packet, metrics, probabilities = capture(p, s, t, gpu)
            save('context-' + binding['day'] + '.pt', packet)
            print(json.dumps(dict(phase='capture', day=binding['day'], completed=index+1)), flush=True)
            packets.append(targets_from_packet(s, t, packet)); targets.extend(t)
            coverage.append((s, t))
        verify_coverage(selection, coverage)
        del p, coverage, s
        gc.collect(); torch.cuda.empty_cache()
        p = model(torch.device('cpu'), args.checkpoint)
        parameters = configure(p)
        optimizer = torch.optim.AdamW(parameters, lr=.003, weight_decay=.0001)
        x = torch.cat([q[0] for q in packets]); actions = torch.cat([q[1] for q in packets])
        quality = torch.cat([q[2] for q in packets]); ratio = torch.cat([q[3] for q in packets])
        groups, offset = [], 0
        for packet in packets:
            groups.extend((indices + offset, hard, soft) for indices, hard, soft in packet[4])
            offset += len(packet[0])
        weights = torch.tensor(forecast_class_weights(targets), dtype=torch.float32)
        binding = digest(dict(parent=plan['parent_manifest_sha256'], checkpoint=plan['checkpoint_sha256'],
            selection=parent['selection_sha256'], context=hashlib.sha256(x.numpy().tobytes()).hexdigest(),
            objective_sha256=file_hash(Path(__file__).with_name('staged_heads.py')),
            forecast_sha256=file_hash(Path(__file__).with_name('hierarchical_heads.py')),
            optimizer=dict(lr=.003, weight_decay=.0001)))
        start_step = 0
        if args.resume_fit_state is not None:
            start_step = restore_fit(args.resume_fit_state, args.resume_sha256, binding, p, optimizer, args.updates)
        for step in range(start_step + 1, args.updates + 1):
            check_host_reserve()
            started = time.perf_counter(); optimizer.zero_grad()
            loss = objective(p, x, actions, quality, ratio, groups, weights)
            if not torch.isfinite(loss): raise ValueError('Nonfinite fitting loss')
            loss.backward(); torch.nn.utils.clip_grad_norm_(parameters, 1.); optimizer.step()
            time.sleep(max(0, time.perf_counter() - started))
            if step % 50 == 0 or step == args.updates:
                save('fit-state.pt', dict(model=p.state_dict(), optimizer=optimizer.state_dict(), step=step,
                                          torch_rng=torch.get_rng_state(), binding=binding))
                write('fit-state-receipt.json', dict(step=step, binding=binding,
                    sha256=file_hash(args.output / 'fit-state.pt')))
                write('progress.json', dict(phase='fit', step=step, completed=step,
                    queued=args.updates-step, active=1, failed=0, loss=float(loss.detach())))
                print(json.dumps(dict(phase='fit', step=step, loss=float(loss.detach()))), flush=True)
                logger.log({'fit/loss': float(loss.detach())}, step=step)
        save('candidate.pt', p.state_dict()); del p, optimizer
        gc.collect()
        results = []
        for repeat in range(2):
            p = model(gpu, args.output / 'candidate.pt'); reports = []
            for index, binding in enumerate(parent['sessions']):
                write('progress.json', dict(phase='replay', repeat=repeat, day=binding['day'],
                    completed=repeat*6+index, queued=11-repeat*6-index, active=1, failed=0))
                s, t = session(binding); evidence = {}
                metrics, probabilities, _ = evaluate_probabilities(p, s, t, gpu, regression_evidence=evidence)
                metrics.update(evidence); reports.append(dict(day=binding['day'], metrics=metrics, probabilities=probabilities))
                print(json.dumps(dict(phase='replay', repeat=repeat, day=binding['day'], completed=index+1)), flush=True)
                del s, t; gc.collect()
            result = dict(metrics=pool_gate_metrics([r['metrics'] for r in reports]), sessions=reports)
            results.append(result); write('replay-' + str(repeat) + '.json', result)
            logger.log({'replay_' + str(repeat) + '/' + k: v for k, v in flatten(result['metrics']).items()
                        if isinstance(v, (int, float))}, step=args.updates + repeat + 1)
            del p; gc.collect(); torch.cuda.empty_cache()
        if not exact_metrics(results[0], results[1]): raise ValueError('Fresh reload replay mismatch')
        write('complete.json', dict(passed=passes(results[0]['metrics']), reload_exact=True,
            metrics=results[0]['metrics'], checkpoint_sha256=file_hash(args.output / 'candidate.pt'),
            generalization_evaluated=False, production_certified=False))
    except BaseException:
        write('failure.json', dict(traceback=traceback.format_exc()))
        raise
    finally:
        logger.finish()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
