from copy import deepcopy
import pytest
from src.market_engine.reaction_band import estimate,partition,CONFIG
from src.market_engine.streaming_level_book import StreamingLevelBook,EXTRACTION_VERSION
from src.market_engine.historical_level_checkpoint import digest


def observations(prices):
    return [dict(price=p,at=i*10+1,resolved_at=i*10+2,session='2026-08-20',role='resistance',resolution=.0001) for i,p in enumerate(prices)]


def empty():
    p=dict(ticker='TEST',session='2026-08-20',available_at=0,levels=[],source_extraction_version=EXTRACTION_VERSION,band_config=CONFIG)
    p['checkpoint_hash']=digest(p)
    return StreamingLevelBook(p,ticker='TEST',session='2026-08-21',start=1000,end=10000)


def test_mle_width_tracks_each_levels_dispersion_and_is_not_a_noise_width():
    tight=estimate(observations([4.99,5,5.01,5,5.005]))
    wide=estimate(observations([4.9,5,5.1,5,5.05]))
    assert tight['status']==wide['status']=='estimated'
    assert wide['upper']-wide['lower']>8*(tight['upper']-tight['lower'])
    assert estimate(observations([5,5.01]))['status']=='insufficient_evidence'


def test_distinct_reaction_modes_split_instead_of_one_wide_band():
    modes=partition(observations([5,5.001,4.999,5.002,5.2,5.201,5.199,5.202]))
    assert len(modes)==2
    assert modes[0][1]['upper']<modes[1][1]['lower']


def test_new_reaction_outside_old_band_refits_without_redrawing_old_segments():
    s=empty()
    for i,p in enumerate([5,5.001,4.999]):s._proposal(p,1100+i*10,'resistance',{'t':1102+i*10},.2)
    row=s.rows[0];before=deepcopy(row['segments']);old_center=row['price'];old_upper=row['upper']
    assert row['qualified'] and old_upper<5.03
    s._proposal(5.03,1200,'resistance',{'t':1202},.2)
    assert row['price']!=old_center
    assert row['segments'][:len(before)]==before
    assert row['segments'][-1]['start']==1202
    assert len(row['observations'])==4


def test_split_adjustment_scales_observations_and_fitted_distribution_once():
    s=empty()
    for i,p in enumerate([.8,.801,.799]):s._proposal(p,1100+i*10,'resistance',{'t':1102+i*10},.1)
    book=s.historical_checkpoint('input')
    next_day=StreamingLevelBook(book,ticker='TEST',session='2026-08-24',start=11000,end=20000,split_factor=5,split_evidence=[{'split_from':5,'split_to':1}])
    a=s.rows[0];b=next_day.rows[0]
    assert b['historical'] and b['id']==a['id']
    assert b['upper']==pytest.approx(a['upper']*5)
    assert b['fit']['scale']==pytest.approx(a['fit']['scale']*5)
    assert b['observations'][0]['price']==pytest.approx(a['observations'][0]['price']*5)


def test_failed_refit_does_not_replace_valid_geometry_or_observations(monkeypatch):
    s=empty()
    for i,p in enumerate([5,5.001,4.999]):s._proposal(p,1100+i*10,'resistance',{'t':1102+i*10},.2)
    before=deepcopy(s.rows[0])
    monkeypatch.setattr('src.market_engine.streaming_level_book.partition',lambda obs,coverage:[(obs,dict(status='fit_failed'))])
    with pytest.raises(ValueError,match='no stale-band fallback'):
        s._proposal(5.03,1200,'resistance',{'t':1202},.2)
    assert s.rows[0]==before
