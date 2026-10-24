from dataclasses import asdict, replace
from datetime import date, datetime, timezone
import pytest
from pipelines.certified_research.contracts import digest
from pipelines.fundamentals.producer import Batch, publish
from pipelines.fundamentals.v1 import FINANCIAL, financial_sql, gap_sql, derived
from tests.test_fundamentals_retained import live


def test_empty_forward_completion_is_not_displayed_as_planning():
    from pipelines.fundamentals.progress import render
    text=render(dict(state='complete',mode='forward-fill',counts={},total=0,queued=0,elapsed=0))
    assert 'No new eligible observations' in text and 'Planning scope' not in text


def test_legacy_receipt_identity_preserved():
    b=Batch('borrow','202601',('S',),date(2026,1,1),date(2026,2,1),'p')
    old=asdict(b)
    for key in ('selected','observed_since','observed_until'):old.pop(key)
    assert b.key==digest(old)
    assert replace(b,selected=True).key!=b.key


def test_contexts_and_gaps_are_not_per_filing_cartesian_products():
    sql=gap_sql(['0000320193'],datetime.now(timezone.utc))
    assert 'GROUP BY cik,period_start,period_end,unit' in sql
    assert 'groupUniqArray(accession)' in sql
    assert 'America/New_York' in sql
    with pytest.raises(ValueError):financial_sql(['0000320193'],datetime.now())
    assert 'LongTermDebtAndFinanceLeaseObligationsIncludingCurrentMaturities' not in FINANCIAL['debt_total'][1]


def test_qualified_per_share_unit_retains_provenance_and_canonicalizes_alias():
    sql=financial_sql(['0001318605'],datetime.now(timezone.utc))
    assert "match(c.unit,'^[A-Z]{3}/share$')" in sql
    assert "concat(substring(c.unit,1,3),'/shares')" in sql
    assert 'c.unit source_unit' in sql


def test_consolidated_reader_does_not_alias_source_native_local_names():
    sql=financial_sql(['0001318605'],datetime.now(timezone.utc))
    assert "arrayElement(splitByChar(':',metric),1) IN ('us-gaap','ifrs-full','dei')" in sql
    assert "c.taxonomy IN ('us-gaap','ifrs-full','dei')" in sql
    assert "f.metric=concat(c.taxonomy,':'" in sql


def test_ratios_do_not_mix_period_currency_or_subsidiary():
    rows=[dict(cik='A',period_start='2025-01-01',period_end='2025-12-31',unit='USD',feature=f,value=v)
          for f,v in [('revenue','100'),('operating_income','20')]]
    assert derived(rows)[0]['value']=='0.2'
    assert not derived([rows[0],dict(rows[1],unit='EUR')])
    assert not derived([rows[0],dict(rows[1],cik='B')])
    assert not derived([rows[0],dict(rows[1],conflict=True)])


def test_live_selected_reuses_legacy_then_adds_only_gap(live):
    q,db,b,insert=live
    insert('first','2026-10-01 14:00:00')
    assert publish(q,b,db)['rows']==5
    selected=replace(b,selected=True,partition_id='*')
    assert publish(q,selected,db)['state']=='unchanged'
    assert q(f'SELECT count() n FROM {db}.reference_fact_v1 FINAL')[0]['n']==5
    q(f'OPTIMIZE TABLE {db}.borrow FINAL',read=False)
    assert publish(q,selected,db)['state']=='unchanged'
    insert('second','2026-10-02 14:00:00')
    result=publish(q,selected,db)
    assert result['state']=='published' and result['rows']==5
    assert q(f'SELECT count() n FROM {db}.reference_fact_v1 FINAL')[0]['n']==10
    assert publish(q,selected,db)['state']=='unchanged'


def test_live_forward_window_includes_late_observations(live):
    q,db,b,insert=live
    insert('old','2026-10-01 14:00:00',period='2020-12-31')
    insert('new','2026-10-03 14:00:00',period='2020-12-31')
    window=replace(b,selected=True,partition_id='*',
        observed_since=datetime(2026,10,2,tzinfo=timezone.utc),
        observed_until=datetime(2026,10,4,tzinfo=timezone.utc))
    assert publish(q,window,db)['rows']==5
    assert q(f'SELECT event_key FROM {db}.reference_source_v1 FINAL')==[{'event_key':'new'}]
    assert publish(q,window,db)['state']=='unchanged'
