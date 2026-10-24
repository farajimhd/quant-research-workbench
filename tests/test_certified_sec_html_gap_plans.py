from pipelines.certified_research.sec_html_reviewed_gaps import plan_for,ADRX_HASH


def test_adrx_review_is_exact_and_preserves_actual_periods():
    assert plan_for(ADRX_HASH,'1') is None
    assert plan_for('a'*64,'1802369') is None
    plan=plan_for(ADRX_HASH,'1802369')
    assert len(plan['facts'])==31
    assert all(f['table_proof']['headers'] for f in plan['facts'])
    rows={ (f['concept'].split('}')[-1],f['period_start'],f['period_end']):f['expected_value'] for f in plan['facts'] }
    assert len(rows)==31
    assert rows[('Assets',None,'2026-06-30')]=='456332000'
    assert rows[('NetIncomeLoss','2026-01-01','2026-06-30')]=='-48426000'
    assert rows[('NetCashProvidedByUsedInOperatingActivities','2025-01-01','2025-06-30')]=='300739000'
    assert set(f['locator']['table'] for f in plan['facts'])=={663,664,666,701,702,705}
