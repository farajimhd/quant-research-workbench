"""Missing V7 is a masked source state, never a reason to discard a listing."""
from datetime import date

import pytest

from research.rl_trading.v6 import reference


def test_missing_prior_v7_keeps_pit_fundamentals(monkeypatch):
    day = date(2026, 7, 31)
    listing = {'ticker': 'ABC', 'listing_id': 'abc'}

    def absent(*_args):
        raise ValueError('No certified prior-session structural V7 coverage: ABC')

    seen = []

    def rows(_client, sql):
        seen.append(sql)
        return []

    monkeypatch.setattr(reference.reference_features, 'read_reference', absent)
    monkeypatch.setattr(reference.reference_features, '_identity', lambda _: 'listing_id=1')
    monkeypatch.setattr(reference, 'query', rows)
    seed, splits, values, evidence = reference.read_reference(None, day, listing)
    assert seed is None and splits == []
    assert values['float_present'] == 0
    assert evidence['v7_coverage'] == 'absent' and len(evidence['hash']) == 64
    assert len(seen) == 3 and 'inserted_at<=' in seen[1]


def test_existing_invalid_v7_fails_closed(monkeypatch):
    monkeypatch.setattr(reference.reference_features, 'read_reference',
        lambda *_: (_ for _ in ()).throw(ValueError(
            'No certified prior-session structural V7 coverage')))
    monkeypatch.setattr(reference, 'query', lambda *_: [{'state': 'failed'}])
    with pytest.raises(ValueError, match='invalid'):
        reference.read_reference(None, date(2026, 7, 31), {'ticker': 'ABC'})
