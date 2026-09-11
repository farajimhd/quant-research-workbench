import pytest

from pipelines.market_sip.execution_clock_recovery import normalize_trade,reconcile


def source(**changes):
    r=dict(sip_timestamp=1000000,participant_timestamp=999000,sequence_number=1,
           price=2.12,size=100,exchange=11,tape=3,conditions=[12,41])
    r.update(changes);return r


def archive(record,ordinal=1):
    key,_,_=normalize_trade(record,{0:0,12:4,41:8})
    return dict(ordinal=ordinal,sip_timestamp_us=key[0],event_meta=key[1],price_primary_int=key[2],
                size_primary=key[3],exchange_primary=key[4],**{f'condition_token_{i}':key[i+4] for i in range(1,6)})


def test_unique_exact_match():
    s=source();matched,report=reconcile([archive(s)],[s],{0:0,12:4,41:8})
    assert report['complete']
    assert matched==[dict(ordinal=1,sip_timestamp_us=1000,execution_timestamp_us=999)]


def test_same_fingerprint_different_clock_is_ambiguous():
    s=source();other=source(sequence_number=2,participant_timestamp=998000)
    matched,r=reconcile([archive(s),archive(s,2)],[s,other],{0:0,12:4,41:8})
    assert not matched and not r['complete'] and r['ambiguous']==2


def test_duplicate_fingerprint_same_clock_is_unambiguous():
    s=source();other=source(sequence_number=2)
    matched,r=reconcile([archive(s),archive(s,2)],[s,other],{0:0,12:4,41:8})
    assert r['complete'] and len(matched)==2


def test_proven_import_order_resolves_distinct_duplicate_clocks():
    s=source();other=source(sequence_number=2,participant_timestamp=998000)
    matched,r=reconcile([archive(s,2),archive(s,1)],[other,s],{0:0,12:4,41:8},verified_order_contract=True)
    assert r['complete'] and r['resolved_by_verified_order']==2
    assert [x['execution_timestamp_us'] for x in matched]==[999,998]


def test_order_resolution_requires_full_sequence_equality():
    s=source();other=source(sequence_number=2,price=3)
    with pytest.raises(ValueError,match='ordering mismatch'):
        reconcile([archive(s,2),archive(other,1)],[s,other],{0:0,12:4,41:8},verified_order_contract=True)


def test_unmatched_source_or_archive_fails():
    s=source()
    assert not reconcile([archive(s)],[],{0:0,12:4,41:8})[1]['complete']
    assert not reconcile([],[s],{0:0,12:4,41:8})[1]['complete']


def test_corrections_counted_separately():
    s=source();_,r=reconcile([archive(s)],[s,source(correction=7)],{0:0,12:4,41:8})
    assert r['complete'] and r['excluded_corrections']==1


def test_missing_timestamp_never_falls_back_to_sip():
    with pytest.raises(ValueError,match='timestamp'):
        normalize_trade(source(participant_timestamp=0),{})


def test_sequence_duplicates_fail_even_if_prices_differ():
    with pytest.raises(ValueError,match='source sequence'):
        reconcile([],[source(),source(price=3)],{})


def test_decimal_size_preserves_fractional_shares():
    key,_,_=normalize_trade(source(size=0,decimal_size='0.388000'),{})
    assert key[3]==pytest.approx(.388)


@pytest.mark.parametrize('price,encoded,scale',[(2.12,212,0),(.99,9900,1),(2.1234,21234,1),(0,0,0)])
def test_canonical_price_scale(price,encoded,scale):
    key,_,_=normalize_trade(source(price=price),{})
    assert key[2]==encoded
    assert (key[1]>>1)&1==scale
