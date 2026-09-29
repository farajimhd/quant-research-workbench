"""Packed session bank and restart semantics."""
import numpy as np
import pytest

from research.rl_trading.v6.bank import open_bank, write_bank
from research.rl_trading.v6.features import CandleFeatures, LEVEL_NAMES, SCALAR_NAMES


def _item(stamp: int, count: int) -> CandleFeatures:
    return CandleFeatures(
        np.arange(stamp, stamp + count * 1_000_000, 1_000_000,
                  dtype=np.int64),
        np.ones((count, len(SCALAR_NAMES)), dtype=np.float32),
        np.zeros((count, 2, 5, len(LEVEL_NAMES)), dtype=np.float32),
    )


def test_packed_bank_resumes_and_references_previous_tail(tmp_path):
    previous_root = tmp_path / 'previous'
    current_root = tmp_path / 'current'
    write_bank(previous_root, {'A': 3}, [('A', _item(1_000_000, 3))],
               source_hash='prior-source')

    def interrupted():
        yield 'A', _item(10_000_000, 2)
        raise RuntimeError('process interrupted')

    with pytest.raises(RuntimeError, match='interrupted'):
        write_bank(current_root, {'A': 2, 'B': 1}, interrupted(),
                   source_hash='current-source')
    assert not (current_root / 'complete.json').exists()
    write_bank(current_root, {'A': 2, 'B': 1},
               [('A', _item(10_000_000, 2)),
                ('B', _item(10_000_000, 1))], source_hash='current-source')
    current = open_bank(current_root)
    previous = open_bank(previous_root)
    context = current.history('A', 0, previous)
    assert context.close_us.tolist() == [1_000_000, 2_000_000,
                                         3_000_000, 10_000_000]
    assert current.history('B', 0, previous).close_us.tolist() == [10_000_000]
    assert current.manifest['candle_count'] == 3
    assert write_bank(current_root, {'A': 2, 'B': 1}, (),
                      source_hash='current-source')['candle_count'] == 3


def test_packed_bank_rejects_changed_partial_plan(tmp_path):
    root = tmp_path / 'bank'

    def interrupted():
        yield 'A', _item(1_000_000, 1)
        raise RuntimeError('stop')

    with pytest.raises(RuntimeError):
        write_bank(root, {'A': 1, 'B': 1}, interrupted(), source_hash='first')
    with pytest.raises(ValueError, match='different source or census'):
        write_bank(root, {'A': 1, 'B': 1}, (), source_hash='second')
