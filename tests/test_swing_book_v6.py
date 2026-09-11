import json
import pytest
from src.market_engine.swing_book_v6 import StreamingSwingBookV6, VERSION, FIELDS


def found(e, price, side, qualified=True, scale='major'):
    e._found({'scale':scale},(price,1.,.3),side,2.)
    r=e.active[e.sequence]
    if qualified and scale=='major':r['best_departure']=r['history_threshold'];e._level_updated(r)
    e.last_time=3.
    return r


def test_only_selected_active_major_survivors_cross_sessions():
    e=StreamingSwingBookV6(opening=0.)
    support=found(e,10.,'support');resistance=found(e,12.,'resistance')
    rejected=found(e,15.,'resistance',False)
    broken=found(e,20.,'support');broken['state']='awaiting_retest';e._level_updated(broken)
    found(e,9.,'support',False,'local')
    seed=e.closing_state(10.)
    assert {r['level_id'] for r in seed['levels']}=={support['level_id'],resistance['level_id']}
    assert all(set(r)==set(FIELDS) for r in seed['levels'])
    resumed=StreamingSwingBookV6(json.loads(json.dumps(seed)),100.)
    assert rejected['level_id'] not in resumed.active
    assert {r['side'] for r in resumed.snapshot()['unified_levels']}=={-1,1}
    from datetime import datetime,timezone
    from src.trading_runtime.structure_level_contract import strategy_snapshot
    assert len(strategy_snapshot(resumed.snapshot(),datetime.fromtimestamp(100.,timezone.utc))['unified_levels'])==2


def test_duplicate_area_keeps_one_representative_and_union_band():
    e=StreamingSwingBookV6(opening=0.)
    first=found(e,10.,'support');second=found(e,10.04,'support')
    seed=e.closing_state(10.)
    assert len(seed['levels'])==1
    assert seed['levels'][0]['lower']==first['lower']
    assert seed['levels'][0]['upper']==second['upper']
    assert seed['levels'][0]['level_id']==second['level_id']


def test_daily_reload_matches_serialized_restart_and_split():
    a=StreamingSwingBookV6(opening=0.)
    for t,p in enumerate([10.,10.5,11.,10.3,9.,9.5,10.]*8,1):a.observe(t,p,p,p)
    seed=a.closing_state(100.)
    b=StreamingSwingBookV6(seed,200.,.5)
    c=StreamingSwingBookV6(json.loads(json.dumps(seed)),200.,.5)
    for t,p in enumerate([5.,5.2,5.7,5.1,4.8]*10,201):
        b.observe(t,p,p,p);c.observe(t,p,p,p)
        assert b.snapshot()==c.snapshot()
    assert b.closing_state(300.)==c.closing_state(300.)
    assert all(row['book_version']==VERSION for row in c.snapshot()['unified_levels'])


def test_rejects_candidate_checkpoint():
    with pytest.raises(ValueError,match='survivor-only'):
        StreamingSwingBookV6(dict(version='causal-swing-closing-book-4'),10.)


@pytest.mark.parametrize('side', ['support', 'resistance'])
def test_merged_area_preserves_oldest_member_confirmation(side):
    historical=StreamingSwingBookV6(opening=0.)
    found(historical,10.,side)
    seed=historical.closing_state(10.)
    current=StreamingSwingBookV6(seed,100.)
    new=found(current,10.04,side)
    new.update(pivot_at=101.,confirmed_at=102.,formed_at=102.)
    current.last_time=103.
    current._level_updated(new)
    merged=current.snapshot()['unified_levels']
    assert len(merged)==1 and merged[0]['member_count']==2
    assert merged[0]['oldest_member_confirmed_at_ms']==2000
    assert merged[0]['confirmed_at_ms']==102000
    # After the historical member leaves, current-only membership is new.
    del current.active[seed['levels'][0]['level_id']]
    current.selection_dirty=True
    assert current.snapshot()['unified_levels'][0]['oldest_member_confirmed_at_ms']==102000


def test_pruning_an_oversize_separator_finishes_merging_before_persistence():
    e=StreamingSwingBookV6(opening=0.)
    a=found(e,10.,'support');separator=found(e,20.,'support',False);b=found(e,30.,'support')
    a.update(price=10.005,lower=10.,upper=10.01)
    separator.update(price=10.25,lower=10.02,upper=10.5)
    b.update(price=10.045,lower=10.04,upper=10.05)
    seed=e.closing_state(10.)
    assert len(seed['levels'])==1
    assert seed['levels'][0]['lower']==10. and seed['levels'][0]['upper']==10.05
    assert StreamingSwingBookV6(seed,10.).closing_state(10.)==seed
