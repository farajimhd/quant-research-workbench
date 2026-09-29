from datetime import date
from pathlib import Path

import numpy as np

from research.rl_trading.v6.bank import SessionBank
from research.rl_trading.v6.session_data import PackedSession


def test_packed_session_groups_only_actual_candles_and_keeps_identity():
    # Two listing-contiguous arrays have a shared first close and a clock
    # gap. The event axis must be chronological, without gap padding.
    clocks = np.asarray([1_000_000, 3_000_000, 1_000_000, 2_000_000],
                        dtype=np.int64)
    bank = SessionBank(Path('unused'),
        {'offsets': {'A': [0, 2], 'B': [2, 4]}}, clocks,
        np.zeros((4, 37), dtype=np.float32),
        np.zeros((4, 2, 5, 11), dtype=np.float32))
    session = PackedSession(date(2026, 7, 31), 'train', Path('unused'),
        'certificate', bank, None, ('A', 'B'))
    events = list(session.candle_events())
    assert [event.close_us for event in events] == [1_000_000, 2_000_000,
                                                    3_000_000]
    assert events[0].listing_index.tolist() == [0, 1]
    assert events[0].bank_row.tolist() == [0, 2]
    assert events[1].listing_index.tolist() == [1]
