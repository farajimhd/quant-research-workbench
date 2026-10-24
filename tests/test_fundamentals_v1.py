from dataclasses import asdict, replace
from datetime import date, datetime, timezone
import pytest
from pipelines.certified_research.contracts import digest
from pipelines.fundamentals.producer import Batch, publish
from pipelines.fundamentals.v1 import FINANCIAL, financial_sql, gap_sql, derived
from tests.test_fundamentals_retained import live


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
