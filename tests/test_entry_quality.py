from src.trading_runtime.entry_quality import blocked


POLICY = dict(quote_clearance_spreads=1., minimum_fee=1., fee_per_share=.005,
              slippage_bps=5., reward_cap_r=2., minimum_reward_cost_multiple=2.)


def test_quote_noise_and_fee_dominated_orders_are_rejected():
    assert blocked(POLICY, bid=10., ask=10.02, stop=9.99, target=11., quantity=100) == 'entry_stop_inside_quote_noise'
    assert blocked(POLICY, bid=10., ask=10.02, stop=9.9, target=11., quantity=1) == 'entry_reward_below_execution_cost'
    assert blocked(POLICY, bid=10., ask=10.02, stop=9.9, target=11., quantity=100) == ''
    assert blocked(POLICY, bid=10., ask=10.02, stop=9.9, target=100., quantity=1) == 'entry_reward_below_execution_cost'


def test_missing_or_crossed_quotes_fail_closed_only_when_enabled():
    for bid, ask in [(None, 10.), (10.1, 10.), (float('nan'), 10.)]:
        assert blocked(POLICY, bid=bid, ask=ask, stop=9., target=11., quantity=100)
        assert blocked(None, bid=bid, ask=ask, stop=9., target=11., quantity=100) == ''
