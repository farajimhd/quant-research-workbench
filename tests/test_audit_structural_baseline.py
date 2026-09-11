"""Research accounting must not select entries using future resolution."""
from copy import deepcopy

import pytest

from scripts import audit_structural_baseline as study


def quote(at, bid=10, ask=10.01):
    return dict(at=at, bid=bid, ask=ask)


def decision(at=100):
    level = dict(lower=9.9, upper=10, side=-1, unified_level_id=str(at))
    return dict(at=at, bar=dict(close=10), levels=[level, dict(lower=11, upper=11.01, side=-1)],
                events=[dict(state='breakout', level=level)], direction='up', progression={},
                sequence=[], shares=200000, dollars=2000000, rate10=10, rate60=10)


def test_entry_uses_post_latency_ask_and_exit_executable_bid():
    quotes = [quote(100, 9, 9.01), quote(100.1), quote(101, 11, 11.01), quote(130)]
    result = study.outcome(dict(at=100, stop=9.5, target=11), quotes, [q['at'] for q in quotes])
    assert result['entry_at'] == 100.1
    assert result['entry'] == pytest.approx(10.01 * 1.0005)
    assert result['exit_at'] == 101
    assert result['reason'] == 'target'
    assert result['net'] == pytest.approx((11 * .9995 - 10.01 * 1.0005) * 100 - 2)


def test_known_stop_resolves_even_when_horizon_is_missing():
    quotes = [quote(100.1), quote(101, 9, 9.01)]
    result = study.outcome(dict(at=100, stop=9.5, target=11), quotes, [q['at'] for q in quotes])
    assert result['status'] == 'resolved'
    assert result['reason'] == 'stop'
    assert result['horizon_observed'] is False
    assert result['horizon_return_bps'] is None


def test_unresolved_position_blocks_later_entries(monkeypatch):
    def outcome(sample, *args):
        if sample['at'] == 100:
            return dict(status='censored', reserved_until=130)
        return dict(status='resolved', exit_at=125, net=10, reason='target')
    monkeypatch.setattr(study, 'outcome', outcome)
    _, summary = study.evaluate_rows([decision(100), decision(120)], [quote(100), quote(120)])
    assert summary['breakout']['eligible'] == 2
    assert summary['breakout']['unresolved_positions'] == 1
    assert summary['breakout']['trades'] == 0


def test_future_quotes_do_not_change_decision_features():
    prefix = [quote(99.9)]
    first, _ = study.evaluate_rows([decision()], prefix)
    later, _ = study.evaluate_rows([decision()], prefix + [quote(100.1), quote(101, 11), quote(130)])
    for samples in (first, later):
        samples[0].pop('outcome')
    assert first == later


def test_quote_after_decision_cannot_satisfy_tradability_gate():
    samples, summary = study.evaluate_rows([decision()], [quote(100.1), quote(130)])
    assert 'quote_unavailable' in samples[0]['blocked']
    assert summary['breakout']['trades'] == 0


@pytest.mark.parametrize('change,reason', [
    ({'shares': 0}, 'session_volume'), ({'dollars': 0}, 'session_volume'),
    ({'rate60': 0}, 'activity'),
])
def test_required_gates_remain_mandatory(change, reason):
    row = decision(); row.update(change)
    samples, summary = study.evaluate_rows([row], [quote(100), quote(100.1), quote(130)])
    assert reason in samples[0]['blocked']
    assert summary['breakout']['trades'] == 0


def test_revalidate_stop_after_latency():
    quotes = [quote(100.1, 9.4, 9.6)]
    assert study.outcome(dict(at=100, stop=9.5, target=11), quotes, [100.1])['status'] == 'invalid_at_execution'


def test_out_of_order_source_fails_closed():
    with pytest.raises(ValueError, match='Quotes'):
        study.evaluate_rows([decision()], [quote(101), quote(100)])
    with pytest.raises(ValueError, match='candles'):
        study.evaluate_rows([decision(), deepcopy(decision())], [quote(100)])


def test_future_confirmed_level_fails_closed():
    row = decision()
    row['levels'][0]['confirmed_at_ms'] = 100001
    with pytest.raises(ValueError, match='Future level'):
        study.evaluate_rows([row], [quote(100)])
