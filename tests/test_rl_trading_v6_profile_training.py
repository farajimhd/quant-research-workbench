from types import SimpleNamespace
import pytest
from research.rl_trading.v6.profile_training import PrefixSession


def test_profile_prefix_preserves_bank_identity_and_limits_clocks():
    bank = object()
    calls = []
    class Session:
        day = 'train-day'
        def __init__(self):
            self.bank = bank
        def candle_events(self):
            for clock in range(10):
                calls.append(clock)
                yield SimpleNamespace(close_us=clock)
    prefix = PrefixSession(Session(), 3)
    assert prefix.bank is bank
    assert prefix.day == 'train-day'
    assert [e.close_us for e in prefix.candle_events()] == [0, 1, 2]
    assert calls == [0, 1, 2]  # No future event traversed or copied.
    assert [e.close_us for e in prefix.candle_events()] == [0, 1, 2]


def test_profile_rejects_empty_prefix():
    session = SimpleNamespace(candle_events=lambda: iter(()))
    with pytest.raises(ValueError, match='Empty profile'):
        PrefixSession(session, 3)
