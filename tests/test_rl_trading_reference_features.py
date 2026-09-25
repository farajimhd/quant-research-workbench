from datetime import date

import pytest

from research.rl_trading.v1 import reference_features as rf


def test_reference_uses_pinned_identity_and_opening_cutoff(monkeypatch):
    statements = []
    def rows(_client, statement):
        statements.append(statement)
        if 'structural_level_coverage_v7' in statement:
            return [dict(ticker='AAPL',session_date='2026-08-20',state='complete',
                         level_count=0,available_at='2026-08-21 00:00:00')]
        return []
    monkeypatch.setattr(rf,'query',rows)
    monkeypatch.setattr(rf,'load_seed',lambda *_args,**_kwargs: {'session':'2026-08-20','ticker':'AAPL'})
    listing = dict(ticker='AAPL',symbol_id='symbol:stock',listing_id='listing:stock',
                   security_id='security:stock')
    rf.read_reference(object(),date(2026,8,21),listing)
    for sql in statements[1:]:
        assert "symbol_id='symbol:stock'" in sql
        assert "listing_id='listing:stock'" in sql
        assert "security_id='security:stock'" in sql
        assert '2026-08-21 08:00:00.000000000' in sql


def test_reference_masks_missing_fundamentals_and_distinguishes_reverse_split():
    day = date(2026,8,21)
    values = rf.fundamentals(day,[],[dict(execution_date='2026-08-10',split_from=10,
                                          split_to=1)])
    assert values['float_present'] == 0
    assert values['shares_present'] == 0
    assert values['split_present'] == 1
    assert values['reverse_split_present'] == 1
    with pytest.raises(ValueError,match='split ratio'):
        rf.fundamentals(day,[],[dict(execution_date='2026-08-10',split_from=0,split_to=1)])


def test_seed_preflight_reports_every_uncertified_listing(monkeypatch):
    statements = []
    def rows(_client,statement):
        statements.append(statement)
        return [{'ticker':'AAPL'},{'ticker':'NVDA'}]
    monkeypatch.setattr(rf,'query',rows)
    assert rf.missing_seeds(object(),date(2026,8,21),['NVDA','MISSING','AAPL']) == ['MISSING']
    assert 'arte.structural_level_coverage_v7 FINAL' in statements[0]
    assert "session_date<toDate('2026-08-21')" in statements[0]
