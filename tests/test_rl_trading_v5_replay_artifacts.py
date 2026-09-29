from pathlib import Path
from types import SimpleNamespace

import polars as pl

from research.rl_trading.v1.common import file_hash
from research.rl_trading.v1.v5_feature_binding import FeatureBinding
from research.rl_trading.v1.v5_replay_artifacts import save_replay
from src.market_engine.level_book_store import read, write


def test_replay_artifacts_are_hashed_and_reusable(tmp_path: Path, monkeypatch):
    monkeypatch.setattr('research.rl_trading.v1.v5_replay_artifacts.runtime_root',
                        lambda: tmp_path)
    execution = tmp_path / 'execution'
    execution.mkdir()
    write(execution / 'plan.json', dict(plan_hash='execution-hash',
        date='2026-07-30', tickers=['ABC'], feature_bank_hash='features'))
    write(execution / 'complete.json', dict(plan_hash='execution-hash'))
    checkpoint = tmp_path / 'checkpoint.pt'
    checkpoint.write_bytes(b'checkpoint')
    binding = FeatureBinding('2026-07-30', ('ABC',), 2, 1, tmp_path / 'features.npy',
                             'features', tmp_path / 'orders.parquet', 'orders', 'supervision')
    account = SimpleNamespace(
        grid=SimpleNamespace(tickers=('ABC',), seconds=3), second=2,
        config=SimpleNamespace(manifest=lambda: {'initial_cash': 10000}),
        order_trace=[dict(ticker='ABC', side='buy', decision_second=0,
                          arrival_second=1, filled_shares=2.0, status='filled')],
        position_ledger=[dict(ticker='ABC', entry_second=1, exit_second=2,
                              net_pnl=5.0)],
        equity_path=[10000.0, 10001.0, 10005.0],
        summary=lambda: {'net_profit': 5.0, 'periods': []})
    root = save_replay(account, binding, execution, checkpoint,
                       split='train_diagnostic', source_commit='12345678')
    assert save_replay(account, binding, execution, checkpoint,
                       split='train_diagnostic', source_commit='12345678') == root
    assert pl.read_parquet(root / 'orders.parquet').height == 1
    assert pl.read_parquet(root / 'positions.parquet')['net_pnl'].to_list() == [5.0]
    assert read(root / 'metrics.json')['net_profit'] == 5.0
    assert read(root / 'complete.json')['files']['orders.parquet'] == file_hash(root / 'orders.parquet')
