import math

import pytest
import torch

from research.rl_trading.v6.action_adapter import decode_proposal


def _decode(token: int):
    # Two listings, one confirmed holding: HOLD, two ENTERs, EXIT, STOP,
    # TARGET. Only the selected token is unmasked in this fixture.
    logits = torch.full((6,), -100.)
    logits[token] = 5.
    return decode_proposal(logits, torch.tensor([.25, .75]),
        torch.tensor([-2.]), torch.tensor([-1.]),
        held_index=torch.tensor([1]), entry_prices=(10.,),
        held_tick_sizes=(.01,))


def test_five_action_translation_uses_confirmed_holding_and_explicit_tick():
    assert _decode(0).action == 'hold'
    buy = _decode(2)
    assert (buy.action, buy.listing_index, buy.cash_fraction) == (
        'enter_long', 1, .75)
    assert (_decode(3).action, _decode(3).listing_index) == ('exit_long', 1)
    stop = _decode(4)
    target = _decode(5)
    assert stop.action == 'set_stop' and 0 < stop.price < 10
    assert target.action == 'set_target' and target.price > 10
    assert math.isclose(stop.price/.01, round(stop.price/.01), abs_tol=1e-6)
    assert math.isclose(target.price/.01, round(target.price/.01), abs_tol=1e-6)


def test_bracket_translation_fails_without_tick_authority():
    logits = torch.tensor([-100., -100., -100., -100., 1., -100.])
    with pytest.raises(ValueError, match='tick authority'):
        decode_proposal(logits, torch.tensor([.2, .3]), torch.tensor([-2.]),
            torch.tensor([-1.]), held_index=torch.tensor([1]),
            entry_prices=(10.,), held_tick_sizes=(0.,))
