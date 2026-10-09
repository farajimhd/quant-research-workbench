from research.vectorized_backtest.v6.torch_backtest.previous_close import matched_identities


def test_ticker_reuse_and_duplicate_intervals_fail_closed():
    members=[dict(listing_id='old',ticker='A'),dict(listing_id='new',ticker='B'),dict(listing_id='duplicate',ticker='C')]
    def row(identity,ticker):
        return dict(listing_id=identity,ticker_normalized=ticker,valid_from_date='2026-07-01',valid_to_date_exclusive=None)
    intervals=[row('old','A'),row('different','B'),row('duplicate','C'),row('duplicate','C')]
    matched,rejected=matched_identities(members,intervals,'2026-07-29','2026-07-30')
    assert [r['listing_id'] for r in matched]==['old']
    assert [r['listing_id'] for r in rejected]==['new','duplicate']


def test_identity_interval_must_cover_both_sides_of_close_context():
    members=[dict(listing_id='a',ticker='A')]
    rows=[dict(listing_id='a',ticker_normalized='A',valid_from_date='2026-07-29',valid_to_date_exclusive='2026-07-30')]
    matched,rejected=matched_identities(members,rows,'2026-07-29','2026-07-30')
    assert not matched and len(rejected)==1
