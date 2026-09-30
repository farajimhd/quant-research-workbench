"""Audit-gated, bounded real teacher/PPO optimizer and replay profile.

Diagnostic updates are discarded. This never creates a production checkpoint
or consumes the sealed test, and never bypasses the full dataset audit.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ.setdefault('CUBLAS_WORKSPACE_CONFIG', ':4096:8')
import argparse
import cProfile
from dataclasses import asdict
from datetime import date
from itertools import islice
import json
from pathlib import Path
import pstats
import time
import torch
from research.mlops.clickhouse import discover_clickhouse_env_files
from research.mlops.env import load_env_files
from research.rl_trading.v1 import arte_source
from research.rl_trading.v1.common import bounds, exclusive, file_hash
from research.rl_trading.v6.build_luld import open_sidecar
from research.rl_trading.v6.environment import BracketEnvironment
from research.rl_trading.v6.environment_source import ArteExecutionSource
from research.rl_trading.v6.identity_map import certify_identity_map, open_identity_map
from research.rl_trading.v6.market_attention import MarketAttentionConfig
from research.rl_trading.v6.prepare_training import _write_json
from research.rl_trading.v6.ranked_policy import RankedBracketActorCritic
from research.rl_trading.v6.rollout import collect_session, audit_reconstruction, update_session
from research.rl_trading.v6.session_data import open_session
from research.rl_trading.v6.teacher_data import load_teacher
from research.rl_trading.v6.train import _commit, _model_causality_audit
from research.rl_trading.v6.training import train_session
from research.rl_trading.v6.training_gate import require_dataset


class PrefixSession:
    """Reuse packed arrays; retain only a bounded chronological event prefix."""
    def __init__(self, session, count):
        self.session = session
        self.events = tuple(islice(session.candle_events(), count))
        if not self.events:
            raise ValueError('Empty profile prefix')

    def __getattr__(self, name):
        return getattr(self.session, name)

    def candle_events(self):
        return iter(self.events)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('dataset', 'output', 'luld-root', 'early-manifest', 'late-manifest', 'ledger'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--teacher-clocks', type=int, default=128)
    parser.add_argument('--ppo-clocks', type=int, default=32)
    parser.add_argument('--clocks-per-chunk', type=int, default=8)
    parser.add_argument('--max-orders-per-second', type=int, default=4)
    parser.add_argument('--day', type=date.fromisoformat,
                        help='Audited training day only; defaults to first training day')
    args = parser.parse_args(argv)
    from src.runtime_paths import runtime_root
    runtime = runtime_root().resolve()
    if min(args.teacher_clocks, args.ppo_clocks, args.clocks_per_chunk, args.max_orders_per_second) < 2:
        raise ValueError('Bounded profile requires positive diagnostic limits')
    if any(not p.resolve().is_relative_to(runtime) for p in
           (args.dataset, args.output, args.luld_root, args.early_manifest, args.late_manifest, args.ledger)):
        raise ValueError('Profile inputs and output must stay under runtime')
    dataset = require_dataset(args.dataset, runtime_root=runtime)
    eligible = [e for e in dataset['days'] if e['role'] == 'train' and
                (args.day is None or e['day'] == str(args.day))]
    if not eligible:
        raise ValueError('Profile day must belong to the audited training split')
    entry = eligible[0]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if (output/'complete.json').exists():
        raise ValueError('Profile already completed; inspect before repeating')
    load_env_files(discover_clickhouse_env_files(), verbose=False)
    device = torch.device('cuda')
    torch.manual_seed(17)
    policy = RankedBracketActorCritic(config=MarketAttentionConfig(**dataset['ranking'])).to(device)
    optimizer = torch.optim.Adam(policy.parameters(), lr=3e-4)
    result = {'source_commit': _commit(), 'dataset_sha256': file_hash(args.dataset),
              'rank': dataset['ranking']['top_r'], 'diagnostic_updates_discarded': True,
              'sealed_test_accessed': False, 'timings': {}}
    profiler = cProfile.Profile()
    reader = None
    with exclusive(output/'profile.lock'):
        try:
            profiler.enable()
            started = time.perf_counter()
            session = open_session(Path(entry['bank_root']), runtime_root=runtime,
                                   previous_root=Path(entry['previous_root']))
            decisions, outcomes = load_teacher(Path(entry['teacher_root']), session, runtime_root=runtime)
            prefix = PrefixSession(session, args.teacher_clocks)
            end = prefix.events[-1].close_us
            labels = tuple(d for d in decisions if d.close_us <= end)
            keys = {(d.close_us, d.order_index) for d in labels}
            fills = tuple(o for o in outcomes if o.bucket_end_us <= end and
                          (o.source_close_us, o.source_order_index) in keys)
            result['timings']['load_and_prefix_seconds'] = time.perf_counter()-started
            result['causality'] = _model_causality_audit(policy, device)
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
            started = time.perf_counter()
            period_times = {'premarket': 0., 'regular': 0., 'after_hours': 0.}
            last_pulse = [started]
            session_start = bounds(session.day)[0]

            def teacher_pulse(values):
                # Chunk boundary timing includes real forward/backward/update
                # work. Period boundaries use the certified session clock.
                torch.cuda.synchronize()
                now = time.perf_counter()
                offset = (values['close_us'] - session_start) / 1_000_000
                period = ('premarket' if offset < 19_800 else
                          'regular' if offset < 43_200 else 'after_hours')
                period_times[period] += now - last_pulse[0]
                last_pulse[0] = now

            metrics = train_session(policy, optimizer, prefix, labels, fills,
                                    device=device, clocks_per_chunk=args.clocks_per_chunk,
                                    progress_callback=teacher_pulse)
            torch.cuda.synchronize()
            result['teacher'] = asdict(metrics)
            result['timings']['teacher_seconds'] = time.perf_counter()-started
            result['teacher_period_chunk_seconds'] = period_times
            if not metrics.optimizer_steps:
                raise ValueError('Profile did not exercise a teacher optimizer step')
            reader = arte_source.reader(threads=2)
            source = arte_source.load_build(args.early_manifest if session.day <= date(2026,8,17)
                                           else args.late_manifest, args.ledger, [session.day])
            identity = output/'identity'
            if not (identity/'complete.json').is_file():
                population, proof = arte_source.population(reader, source, session.day)
                certify_identity_map(session, population, proof['snapshot_hash'], identity, runtime_root=runtime)
            tickers = open_identity_map(identity, session, runtime_root=runtime)
            provider = ArteExecutionSource(reader, source, args.ledger, session.day,
                                           end_us=bounds(session.day)[1])
            book, certificate = open_sidecar(args.luld_root, session.day, source)
            provider.luld_certificate = certificate
            env = BracketEnvironment(tickers, provider, luld=book)
            started = time.perf_counter()
            frames, steps, summary = collect_session(policy, session, env, device=device,
                max_clocks=args.ppo_clocks, max_orders_per_second=args.max_orders_per_second)
            torch.cuda.synchronize()
            result['timings']['collection_seconds'] = time.perf_counter()-started
            result['replay'] = summary
            result['reconstruction'] = audit_reconstruction(policy, session, frames, device=device)
            started = time.perf_counter()
            result['ppo'] = update_session(policy, optimizer, session, frames, steps,
                device=device, epochs=1, clocks_per_chunk=args.clocks_per_chunk)
            torch.cuda.synchronize()
            result['timings']['ppo_update_seconds'] = time.perf_counter()-started
            if result['ppo'].get('update_epochs',0) < 1:
                raise ValueError('Profile did not exercise a PPO optimizer step')
            result['execution_evidence'] = provider.certificate()
            result['peak_gpu_gib'] = torch.cuda.max_memory_allocated()/2**30
            profiler.disable()
            stats = pstats.Stats(profiler)
            # Bounded structured profile, no retained massive binary trace.
            result['cpu_top_cumulative'] = [
                {'function': f'{Path(key[0]).name}:{key[1]}:{key[2]}', 'calls': value[1],
                 'self_seconds': value[2], 'cumulative_seconds': value[3]}
                for key, value in sorted(stats.stats.items(), key=lambda item: item[1][3], reverse=True)[:30]]
            result['status'] = 'passed_real_teacher_and_ppo_updates'
            _write_json(output/'complete.json', result)
            print(json.dumps(result), flush=True)
        except Exception as error:
            _write_json(output/'failure.json', {**result, 'error': type(error).__name__+': '+str(error)})
            raise
        finally:
            profiler.disable()
            if reader is not None:
                reader.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
