"""Rolling context uses UTC elapsed time across weekends and DST boundaries."""
from datetime import date,datetime,time,timedelta,timezone
from zoneinfo import ZoneInfo

from pipelines.certified_research.context import plan,StateSeed,elapsed_sessions
from pipelines.certified_research.contracts import CampaignConfig


def weekdays(start,end):
    return tuple(start+timedelta(days=i) for i in range((end-start).days+1)
        if (start+timedelta(days=i)).weekday()<5)


def test_selected_week_window_extends_context_beyond_latest_state():
    first=date(2026,3,9) # Monday after spring DST change.
    config=CampaignConfig(first,first,volume_windows_seconds=(60,7*86400))
    seed=StateSeed(date(2026,3,6),'a'*64,True)
    result=plan(config,indicator_seed=seed,v7_seed=seed,calendar=weekdays)
    assert not result['blocked']
    assert result['market_context'][0]==date(2026,2,27)
    rolling=next(r for r in result['requirements'] if r['product']=='rolling_activity')
    assert rolling['earliest_required_at']==datetime(2026,3,2,8,tzinfo=timezone.utc)
    assert len(rolling['sessions'])==6


def test_actual_exchange_calendar_covers_spring_dst_window():
    first=date(2026,3,9);seed=StateSeed(date(2026,3,6),'a'*64,True)
    result=plan(CampaignConfig(first,first,volume_windows_seconds=(7*86400,)),
        indicator_seed=seed,v7_seed=seed)
    assert result['market_context'][0]==date(2026,2,27)
    assert result['market_context'][-1]==date(2026,3,6)
    assert not result['blocked']


def test_elapsed_cutoff_origin_precedes_window_across_fall_dst():
    for first in (date(2026,3,9),date(2026,11,2)):
        sessions,cutoff=elapsed_sessions(first,7*86400,calendar=weekdays)
        origin=datetime.combine(sessions[0],time(4),ZoneInfo('America/New_York')).astimezone(timezone.utc)
        assert origin<=cutoff
        assert all(day<first for day in sessions)


def test_source_floor_blocks_long_window_even_with_compatible_seed():
    first=date(2026,1,2);seed=StateSeed(date(2025,12,31),'a'*64,True)
    result=plan(CampaignConfig(first,first,volume_windows_seconds=(30*86400,)),
        indicator_seed=seed,v7_seed=seed,source_floor=date(2025,12,20),calendar=weekdays)
    assert 'Required market context precedes the canonical source floor' in result['blocked']


def test_activity_only_prefix_does_not_replay_state_before_recent_checkpoint():
    from pipelines.certified_research.planning import MarketSession,market_units
    first=date(2026,3,9);days=(date(2026,3,2),date(2026,3,6),first)
    plans=market_units(CampaignConfig(first,first,volume_windows_seconds=(7*86400,)),
        tuple(MarketSession(d,1,'TEST') for d in days),source_parents={d:('source:'+str(d),) for d in days},
        universe_parent='universe',seed_parents={1:('indicator_seed','v7_seed')},corporate_parents={1:'corporate'},
        state_start_dates={1:(first,first)})
    technical=[p for p in plans if p.unit.product=='technical'];v7=[p for p in plans if p.unit.product=='v7']
    assert len(technical)==len(v7)==1
    assert 'indicator_seed' in technical[0].unit.parents and 'v7_seed' in v7[0].unit.parents
    assert len([p for p in plans if p.unit.product=='market_aggregate'])==3
    current=next(p for p in plans if p.unit.key==f'market:1:{first}:activity')
    assert len(current.unit.parents)==3
