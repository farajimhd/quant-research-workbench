from dataclasses import replace
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from pipelines.certified_research.identity import ListingInterval
from scripts import market_day_population as population
from scripts import build_market_day as builder
from scripts import run_market_data_pipeline as launcher


def test_lifecycle_and_quarantine(monkeypatch):
    ny = ZoneInfo('America/New_York')
    old = ListingInterval('listing','security','issuer','OLD',123,
        datetime(2026,1,2,tzinfo=ny),datetime(2026,1,5,tzinfo=ny),
        'US','XNAS','USD','STK',True,True,False,False,'a'*64)
    new = replace(old,ticker='NEW',valid_from=old.valid_to,
        valid_to=datetime(2026,1,7,tzinfo=ny))
    other = replace(old,listing_id='other',ticker='OTHER',
        valid_from=datetime(2026,1,1,tzinfo=ny),valid_to=datetime(2026,2,1,tzinfo=ny))
    snapshot=dict(ready=True,intervals=(old,new,other),source_hash='b'*64,
        conid_pool={},quarantine=[dict(ticker='BAD',reason='unresolved_conid')],
        quarantine_counts={'unresolved_conid':1},eligibility_census={})
    monkeypatch.setattr(population.retrospective_identity,'load',lambda *a:snapshot)
    args=SimpleNamespace(start=date(2026,1,1),end=date(2026,1,7),
        identity_anchor_date=date(2026,10,15),conid_review_hash='c'*64)
    client=SimpleNamespace(query=lambda *a:pytest.fail('No dated snapshot query expected'))
    rows,days,proof=population.retrospective_population(client,args,
        [date(2026,1,d) for d in (1,2,5,7)])
    assert days=={'2026-01-01':{'OTHER'},'2026-01-02':{'OLD','OTHER'},
        '2026-01-05':{'NEW','OTHER'},'2026-01-07':{'OTHER'}}
    assert proof['quarantine_counts']=={'unresolved_conid':1}
    assert all(not r['certificate']['historical_membership_certified'] for r in rows)
    snapshot['intervals']=(other,replace(other,listing_id='conflict'))
    with pytest.raises(ValueError,match='ambiguous'):
        population.retrospective_population(client,args,[date(2026,1,2)])
    snapshot['intervals']=(replace(other,valid_from=datetime(2026,1,2,12,tzinfo=ny)),)
    with pytest.raises(ValueError,match='Intraday'):
        population.retrospective_population(client,args,[date(2026,1,2)])


def test_launcher_passes_pinned_population(tmp_path,monkeypatch):
    seen=[]
    monkeypatch.setattr(builder,'run',lambda a:seen.append(a) or 0)
    args=launcher.arguments(['backfill','--step','bars','--start-date','2026-01-01',
        '--end-date','2026-10-02','--run-dir',str(tmp_path),'--plan-only',
        '--identity-policy','current_conid_retrospective_v1','--conid-review-hash','a'*64,
        '--identity-anchor-date','2026-10-15','--max-plan-units','1500000'])
    assert launcher.run_bars(args)==0
    assert seen[0].conid_review_hash=='a'*64
    assert seen[0].identity_anchor_date==date(2026,10,15)
    assert seen[0].max_plan_units==1500000
    args.conid_review_hash=None
    with pytest.raises(SystemExit):launcher.run_bars(args)
