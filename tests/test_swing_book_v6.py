import json
import pytest
from src.market_engine.swing_book_v6 import StreamingSwingBookV6, VERSION, FIELDS


def found(e, price, side, qualified=True, scale='major', confirmed_at=2.):
    e._found({'scale':scale},(price,confirmed_at-1,.3),side,confirmed_at)
    r=e.active[e.sequence]
    if qualified and scale=='major':r['best_departure']=r['history_threshold'];e._level_updated(r)
    e.last_time=confirmed_at+1
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


@pytest.mark.parametrize('side', ['support','resistance'])
@pytest.mark.parametrize('change', ['flip','remove','republish'])
def test_merged_pending_area_retains_qualified_survivor(side,change):
    e=StreamingSwingBookV6(opening=0.)
    old=found(e,10.,side)
    new=found(e,10.04,side,confirmed_at=3.)
    merged=e.snapshot()['unified_levels'][0]
    assert merged['member_count']==2
    for r in (old,new):
        r.update(state='retest_contact',accepted_crossings=1)
        e._level_updated(r)
    e.snapshot()
    if change=='remove':
        del e.active[new['level_id']];e._level_removed(new['level_id'])
    else:
        new.update(state='active',accepted_crossings=0)
        if change=='flip':
            new.update(side='support' if side=='resistance' else 'resistance',last_role_change_at=4.,role_retests=0)
        e._level_updated(new)
    e.last_time=5.
    result=e.snapshot()['unified_levels']
    retained=next(r for r in result if r.get('retained_qualified_'+side))
    assert retained['selection_members']==[str(old['level_id'])]
    assert retained['lower']==old['lower'] and retained['upper']==old['upper']
    assert retained['unified_level_id']!=merged['unified_level_id']
    assert retained['selection_score']==40.
    assert retained['oldest_member_confirmed_at_ms']==2000
    assert len([k for r in result for k in r['selection_members']])==len({k for r in result for k in r['selection_members']})
    old.update(state='active',side='support' if side=='resistance' else 'resistance',last_role_change_at=6.,role_retests=0)
    e._level_updated(old)
    assert not any(r.get('retained_qualified_'+side) for r in e.snapshot()['unified_levels'])


def test_split_does_not_transfer_departed_members_qualification():
    e=StreamingSwingBookV6(opening=0.)
    weak=found(e,10.,'resistance',qualified=False)
    strong=found(e,10.04,'resistance')
    assert e.snapshot()['unified_levels'][0]['member_count']==2
    for r in (weak,strong):
        r['state']='retest_contact';e._level_updated(r)
    e.snapshot()
    del e.active[strong['level_id']];e._level_removed(strong['level_id'])
    assert not e.snapshot()['unified_levels']


@pytest.mark.parametrize('side', ['support', 'resistance'])
def test_merged_area_preserves_oldest_member_confirmation(side):
    historical=StreamingSwingBookV6(opening=0.)
    found(historical,10.,side)
    seed=historical.closing_state(10.)
    current=StreamingSwingBookV6(seed,100.)
    new=found(current,10.04,side,confirmed_at=102.)
    merged=current.snapshot()['unified_levels']
    assert len(merged)==1 and merged[0]['member_count']==2
    assert merged[0]['oldest_member_confirmed_at_ms']==2000
    assert merged[0]['confirmed_at_ms']==102000
    # The surviving member permanently inherits the historical origin.
    del current.active[seed['levels'][0]['level_id']]
    current.selection_dirty=True
    assert current.snapshot()['unified_levels'][0]['oldest_member_confirmed_at_ms']==2000
    new.update(confirmed_at=104.,last_role_change_at=104.,side='support' if side=='resistance' else 'resistance',role_retests=2)
    current.last_time=105.
    current._level_updated(new)
    assert new['origin_confirmed_at']==2.
    closed=current.closing_state(106.)
    assert closed['levels'][0]['origin_confirmed_at']==2.
    resumed=StreamingSwingBookV6(json.loads(json.dumps(closed)),200.)
    assert resumed.snapshot()['unified_levels'][0]['oldest_member_confirmed_at_ms']==2000


def test_legacy_seed_upgrades_without_mutating_certified_input():
    e=StreamingSwingBookV6(opening=0.)
    found(e,10.,'resistance')
    seed=e.closing_state(10.)
    seed['levels'][0].pop('origin_confirmed_at')
    original=json.dumps(seed,sort_keys=True)
    loaded=StreamingSwingBookV6(seed,100.)
    assert loaded.snapshot()['unified_levels'][0]['oldest_member_confirmed_at_ms']==2000
    assert json.dumps(seed,sort_keys=True)==original
    seed['levels'][0]['origin_confirmed_at']=float('nan')
    with pytest.raises(ValueError,match='survivor origin'):
        StreamingSwingBookV6(seed,100.)


def test_checkpoint_validation_is_exact_for_the_persisted_format():
    from copy import deepcopy
    from src.market_engine.swing_book_v6 import matches_checkpoint
    e=StreamingSwingBookV6(opening=0.)
    found(e,10.,'resistance')
    current=e.closing_state(10.)
    legacy=deepcopy(current)
    legacy['levels'][0].pop('origin_confirmed_at')
    assert matches_checkpoint(current,current)
    assert matches_checkpoint(current,legacy)
    wrong=deepcopy(current);wrong['levels'][0]['origin_confirmed_at']=1.
    assert not matches_checkpoint(wrong,current)
    wrong=deepcopy(current);wrong['levels'][0]['upper']+=.01
    assert not matches_checkpoint(wrong,legacy)
    wrong=deepcopy(current);wrong['levels'][0]['unexpected']=1
    assert not matches_checkpoint(wrong,legacy)


def test_merge_ancestry_is_independent_of_snapshot_polling_and_transitive():
    from copy import deepcopy
    seed_engine=StreamingSwingBookV6(opening=0.)
    old=found(seed_engine,10.,'resistance')
    seed=seed_engine.closing_state(10.)
    a=StreamingSwingBookV6(seed,100.)
    new=found(a,10.04,'resistance',confirmed_at=102.)
    b=deepcopy(a)
    a.snapshot()  # b has no chart consumer.
    for e in (a,b):
        e.observe(104.,11.,11.,11.)
        e.active.pop(old['level_id']);e._level_removed(old['level_id'])
        newest=found(e,10.06,'resistance',confirmed_at=105.)
        e.observe(107.,11.,11.,11.)
        assert newest['origin_confirmed_at']==2.
        assert e.active[new['level_id']]['origin_confirmed_at']==2.
    assert a.snapshot()==b.snapshot()


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
