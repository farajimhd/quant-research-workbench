"""Immutable, hash-bound V5 model replay ledgers and readable metrics."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from research.rl_trading.v1.common import digest, exclusive, file_hash
from research.rl_trading.v1.v5_feature_binding import FeatureBinding
from research.rl_trading.v1.v5_replay import ReplayAccount
from src.market_engine.level_book_store import read, write
from src.runtime_paths import runtime_root

VERSION = 'rl-trading-v5-model-replay-ledger-v1'


def _parquet(rows: list[dict], path: Path, schema: dict[str, pl.DataType]):
    frame = pl.DataFrame(rows, strict=False) if rows else pl.DataFrame(schema=schema)
    temporary = path.with_suffix('.parquet.tmp')
    frame.write_parquet(temporary, compression='zstd')
    temporary.replace(path)


def save_replay(account: ReplayAccount, binding: FeatureBinding,
                execution_root: Path, checkpoint: Path, *,
                split: str, source_commit: str) -> Path:
    """Persist every order, completed position, equity mark and period result.

    Model profit always includes open marked holdings when terminal fills fail;
    ``valid_terminal`` and ``open_positions`` expose that condition. This is a
    modeled next-open scenario, never a claim of actual broker fills.
    """
    runtime = runtime_root().resolve()
    execution_root = Path(execution_root).resolve()
    checkpoint = Path(checkpoint).resolve()
    if (not runtime.is_dir() or not execution_root.is_relative_to(runtime) or
            not checkpoint.is_relative_to(runtime) or
            split not in ('train_diagnostic', 'development', 'test_sealed') or
            len(source_commit) < 8 or
            account.grid.tickers != binding.tickers or
            account.second != account.grid.seconds-1):
        raise ValueError('V5 replay artifact inputs are incomplete or outside runtime')
    execution = read(execution_root / 'plan.json')
    certified = read(execution_root / 'complete.json')
    if (execution.get('plan_hash') != certified.get('plan_hash') or
            execution.get('date') != binding.date or
            tuple(execution.get('tickers', ())) != binding.tickers or
            execution.get('feature_bank_hash') != binding.features_hash):
        raise ValueError('Replay execution and causal feature certificates differ')
    plan = dict(version=VERSION, date=binding.date, split=split,
        checkpoint=str(checkpoint), checkpoint_hash=file_hash(checkpoint),
        source_commit=source_commit, feature_bank_hash=binding.features_hash,
        supervision_plan_hash=binding.supervision_plan_hash,
        execution_root=str(execution_root),
        execution_plan_hash=execution['plan_hash'],
        execution_complete_hash=file_hash(execution_root / 'complete.json'),
        config=account.config.manifest(),
        decision_seconds=binding.decision_seconds,
        modeled_execution='next_second_open_price_only_partial_or_unfilled')
    plan['plan_hash'] = digest(plan)
    root = runtime / 'rl-trading-v5-replay' / binding.date / plan['plan_hash'][:20]
    root.mkdir(parents=True, exist_ok=True)
    write(root / 'plan.json', plan)
    with exclusive(root / 'run.lock'):
        complete_path = root / 'complete.json'
        if complete_path.exists():
            complete = read(complete_path)
            if (complete.get('plan_hash') != plan['plan_hash'] or
                    any(file_hash(root / name) != expected for name, expected
                        in complete['files'].items())):
                raise ValueError('Completed model replay artifacts changed')
            return root
        _parquet(account.order_trace, root / 'orders.parquet',
                 {'ticker':pl.String, 'side':pl.String,
                  'decision_second':pl.Int64, 'arrival_second':pl.Int64})
        _parquet(account.position_ledger, root / 'positions.parquet',
                 {'ticker':pl.String, 'entry_second':pl.Int64,
                  'exit_second':pl.Int64, 'net_pnl':pl.Float64})
        _parquet([dict(second=i, equity=float(value)) for i, value in
                  enumerate(account.equity_path)], root / 'equity.parquet',
                 {'second':pl.Int64, 'equity':pl.Float64})
        summary = account.summary()
        summary.update(date=binding.date, split=split,
                       checkpoint_hash=plan['checkpoint_hash'],
                       execution_plan_hash=execution['plan_hash'])
        write(root / 'metrics.json', summary)
        names = ('orders.parquet','positions.parquet','equity.parquet','metrics.json')
        write(complete_path, dict(plan_hash=plan['plan_hash'],
            order_rows=len(account.order_trace),
            position_rows=len(account.position_ledger),
            equity_marks=len(account.equity_path),
            files={name:file_hash(root / name) for name in names}))
    return root
