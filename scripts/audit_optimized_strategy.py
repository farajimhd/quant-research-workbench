"""Freeze optimizer evidence and plan native releases; never start trading."""
import os
import sys
from pathlib import Path

os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse
import asyncio
from datetime import datetime
from hashlib import sha256
import json
from decimal import Decimal
from collections.abc import Mapping


def _json(value):
    if isinstance(value, Mapping):
        return {key: _json(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(child) for child in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _write(path, value, runtime_root):
    path = Path(path).resolve()
    root = Path(runtime_root).resolve(strict=True)
    if path == root or not path.is_relative_to(root):
        raise ValueError('Output must be inside the explicit runtime root')
    raw = json.dumps(_json(value), sort_keys=True, indent=2, allow_nan=False).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)
    return sha256(raw).hexdigest()


def _read_frozen(root):
    from src.trading_runtime.optimized_candidate_evidence import read_frozen_candidate_evidence
    return read_frozen_candidate_evidence(root, workers=2)


async def run(args):
    if args.command == 'freeze':
        from src.trading_runtime.optimized_candidate_evidence import freeze_candidate_evidence
        print(f'ACTIVE | Freeze generation {args.generation} | {len(args.serial)} candidates', flush=True)
        manifest = await asyncio.to_thread(freeze_candidate_evidence, args.campaign,
            args.generation, args.serial, args.output, runtime_root=args.runtime_root,
            workers=args.workers)
        print(f"COMPLETE | {len(manifest['sources'])} source files | all recorded training/validation sessions preserved", flush=True)
        print(f'Evidence: {Path(args.output).resolve()}\nBacktests started: 0', flush=True)
        return
    if args.command == 'plan-release':
        from src.trading_runtime.optimized_candidate import restore_candidate
        from src.trading_runtime.optimized_candidate_evidence import verify_v19_source_tree
        from src.backend.optimized_strategy_import import prepare_optimized_release
        from src.backend.backtest_journal_clickhouse import backtest_code_hash
        manifest = await asyncio.to_thread(_read_frozen, args.frozen)
        if await asyncio.to_thread(backtest_code_hash, Path(__file__).resolve().parents[1]) != args.code_hash:
            raise ValueError('Plan code hash differs from the current native Backtest source closure')
        provenance = manifest.get('provenance', {})
        expected_source = provenance.get('source_sha256')
        source_qualification = await asyncio.to_thread(verify_v19_source_tree,
            args.optimizer_code_root, expected_source)
        selected = [row for row in manifest['candidate_envelopes'] if row['serial'] == args.serial]
        if len(selected) != 1:
            raise ValueError('Serial absent from frozen audit evidence')
        candidate = restore_candidate(selected[0])
        prepared = await asyncio.to_thread(prepare_optimized_release, candidate,
            history_code_hash=args.history_code_hash, code_hash=args.code_hash,
            universe_source_hash=args.universe_source_hash, strategy_number=args.strategy_number,
            strategy_id=args.strategy_id, release_id=args.release_id,
            published_at=datetime.fromisoformat(args.published_at))
        plan = dict(status='plan-only', serial=candidate.serial,
            candidate=selected[0], release_rows=prepared.rows,
            executor_registration=dict(strategy_id=args.strategy_id, revision=args.strategy_number,
                expected_content_hash=prepared.release.header['content_hash'],
                expected_code_hash=args.code_hash, expected_winner_hash=candidate.program_hash,
                requires_execution_risk=True),
            source_qualification=source_qualification,
            required_fields=prepared.required_fields,
            execution_envelope=dict(candidate.execution),
            liquidity_resolution_ms=100, backtests_started=0,
            publication_performed=False, mode_qualification_performed=False)
        digest = await asyncio.to_thread(_write, args.output, plan, args.runtime_root)
        print(f'COMPLETE | Native release plan | {len(prepared.required_fields)} QMD fields', flush=True)
        print(f'Plan SHA256: {digest}\nPublication/backtest approval required; no run started.', flush=True)


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest='command', required=True)
    freeze = sub.add_parser('freeze', help='Freeze sealed candidate evidence, without market reads')
    freeze.add_argument('--campaign', required=True)
    freeze.add_argument('--generation', required=True, type=int)
    freeze.add_argument('--serial', required=True, type=int, action='append')
    freeze.add_argument('--workers', type=int, default=2, choices=range(1, 9))
    plan = sub.add_parser('plan-release', help='Plan an immutable shared release; never publish')
    plan.add_argument('--frozen', required=True)
    plan.add_argument('--optimizer-code-root', required=True)
    plan.add_argument('--serial', required=True, type=int)
    for name in ('history-code-hash', 'code-hash', 'universe-source-hash', 'strategy-id',
                 'release-id', 'published-at'):
        plan.add_argument('--' + name, required=True)
    plan.add_argument('--strategy-number', required=True, type=int)
    for command in (freeze, plan):
        command.add_argument('--runtime-root', required=True)
        command.add_argument('--output', required=True)
    return result


def main():
    args = parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print('INTERRUPTED | No trading run started; incomplete freeze folders are not reusable.', file=sys.stderr)
        return 130
    except Exception as exc:
        print(f'FAILED | {type(exc).__name__}: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
