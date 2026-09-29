import pytest

from research.rl_trading.v6.oms import BracketAccount, Quote


def quote(clock, *, bid=9.9, ask=10., bid_size=100,
          ask_size=100, valid=True):
    return Quote(clock, clock-50_000, bid, ask, bid_size, ask_size, valid)


def test_quote_bound_bracket_partial_target_and_teacher_profit_sweep():
    account = BracketAccount(sweep_teacher_profits=True)
    bought = account.enter_long('ABC', decision_us=0,
                                decision_close=10., budget=1000.,
                                quote=quote(100_000))
    assert bought == 99 and account.positions['ABC'].entry_price == 10.
    with pytest.raises(ValueError):
        account.set_target('ABC', price=11., clock_us=100_000)
    account.set_stop('ABC', price=8., clock_us=200_000)
    account.set_target('ABC', price=11., clock_us=300_000)
    assert account.target_bucket('ABC', clock_us=400_000,
                                 price_level_volume_cap=20.) == 20
    assert account.positions['ABC'].shares == 79
    assert account.orders[-1]['status'] == 'partial'
    assert account.profit_bank > 0
    assert account.closed[0]['exit_price'] == 11.
    assert account.marked_equity({'ABC': 10.}) == pytest.approx(
        account.cash+account.profit_bank+79*10.)


def test_collision_stops_target_and_waits_for_fresh_later_bid():
    account = BracketAccount()
    assert account.enter_long('ABC', decision_us=0, decision_close=10.,
                              budget=1000., quote=quote(100_000)) == 99
    account.set_stop('ABC', price=8., clock_us=200_000)
    account.set_target('ABC', price=11., clock_us=300_000)
    assert account.target_bucket('ABC', clock_us=400_000,
        price_level_volume_cap=99., stop_touched=True) == 0
    assert len(account.closed) == 0
    assert account.exit_long('ABC', decision_us=400_000,
        quote=quote(500_000, valid=False), action='stop_market') == 0
    assert account.exit_long('ABC', decision_us=400_000,
        quote=quote(600_000, bid=7.9, bid_size=20),
        action='stop_market') == 20
    assert account.positions['ABC'].shares == 79
    assert account.positions['ABC'].target is None
    assert account.orders[-1]['status'] == 'partial'


def test_model_cash_can_compound_but_teacher_does_not():
    model = BracketAccount()
    teacher = BracketAccount(sweep_teacher_profits=True)
    for account in (model, teacher):
        account.enter_long('ABC', decision_us=0, decision_close=10.,
                           budget=1000., quote=quote(100_000))
        account.exit_long('ABC', decision_us=100_000,
                          quote=quote(200_000, bid=12., ask=12.1))
    assert model.cash > model.initial_cash
    assert teacher.cash == pytest.approx(teacher.initial_cash)
    assert teacher.profit_bank == pytest.approx(model.cash-model.initial_cash)
