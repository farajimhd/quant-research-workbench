from datetime import date

import pytest

from scripts.backfill_structural_sessions import warmup_dependency_plan


def test_unseeded_ticker_blocks_without_changing_the_population():
    calls=[]
    def query(sql):
        calls.append(sql)
        if 'structural_level_coverage_v7' in sql:
            return [dict(ticker='SEEDED',seed='2025-12-31')]
        return [dict(ticker='AHH',missing_days=248,first_missing='2025-01-02',examples=['2025-01-02'])]
    population={'AHH':['2026-01-02','2026-01-05'],'SEEDED':['2026-01-02']}
    result=warmup_dependency_plan(query,population,date(2025,1,1),{'2025-12-31','2026-01-02'})
    assert result['blocked_units']==2
    assert result['ready_units']==1
    assert result['blocked']['AHH']['first_missing']=='2025-01-02'
    assert population=={'AHH':['2026-01-02','2026-01-05'],'SEEDED':['2026-01-02']}
    assert "['2024-12-31','2025-12-31']" in calls[1]
    assert 'events_ordinal_continuity' in calls[1] and 'events_2025' not in calls[1]
    assert "source_date NOT IN ('2025-12-31','2026-01-02')" in calls[1]


def test_later_seed_cannot_bypass_earlier_target_and_new_listings_can_start_empty():
    calls=[]
    def query(sql):
        calls.append(sql)
        return []
    result=warmup_dependency_plan(query,{'IPO':['2026-08-03']},date(2025,1,1),{'2026-08-03'})
    assert result['blocked_units']==0 and result['ready_units']==1
    assert "session_date<toDate(transform(ticker,['IPO'],['2026-08-03']" in calls[0]
    assert "['2024-12-31']" in calls[1]
    assert "source_date<toDate(transform(ticker,['IPO'],['2026-08-03']" in calls[1]


def test_preflight_is_batched_and_all_blocked_units_are_retained():
    calls=[]
    def query(sql):
        calls.append(sql)
        return []
    result=warmup_dependency_plan(query,{'A':['2026-01-02'],'B':['2026-01-02'],'C':['2026-01-02']},
                                  date(2025,1,1),set(),batch_size=2)
    assert len(calls)==4
    assert result['ready_tickers']==3
    with pytest.raises(ValueError):
        warmup_dependency_plan(query,{},date(2025,1,1),set(),batch_size=1000)
