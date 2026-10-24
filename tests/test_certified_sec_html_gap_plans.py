from pipelines.certified_research.sec_html_reviewed_gaps import plan_for,ADRX_HASH


def test_qumulus_actual_periods_and_parent_equity_are_separate():
    import json
    from pipelines.certified_research.sec_html_reviewed_qumulus import plan_for,HASH
    plan=plan_for(HASH,'2084026');assert len(plan['facts'])==28
    assert plan_for(HASH,'1') is None and json.loads(json.dumps(plan))==plan
    rows={(f['concept'].split('}')[-1],f['period_start'],f['period_end']):f for f in plan['facts']}
    assert len(rows)==28
    assert rows[('StockholdersEquity',None,'2026-06-30')]['expected_value']=='-3567675'
    assert rows[('Revenues','2026-04-01','2026-06-30')]['expected_value']=='6712805'
    assert rows[('Revenues','2026-01-01','2026-06-30')]['expected_value']=='10132723'
    assert rows[('EarningsPerShareDiluted','2026-01-01','2026-06-30')]['unit']=='USD/shares'


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


def test_registration_currency_scaling_and_periods():
    from pipelines.certified_research import sec_html_reviewed_registration as p
    for fingerprint,cik,count in ((p.OGC,'1487326',18),(p.NUR,'1680637',20),(p.AIBZ,'2100457',12)):
        plan=p.plan_for(fingerprint,cik)
        assert len(plan['facts'])==count
        assert p.plan_for(fingerprint,'1') is None
        assert all(f['table_proof']['headers'] for f in plan['facts'])
        # Registered plans must compare exactly after normalized JSON readback.
        import json
        assert json.loads(json.dumps(plan)) == plan
        keys={(f['concept'],f['period_start'],f['period_end'],f['unit']) for f in plan['facts']}
        assert len(keys)==count
    ogc=p.plan_for(p.OGC,'1487326')['facts']
    assert ogc[0]['expected_value']=='476500000' and ogc[0]['decimals']=='-5'
    assert not any(f['concept'].endswith('}Inventories') for f in ogc)
    nur=p.plan_for(p.NUR,'1680637')['facts']
    assert all(f['unit']=='CAD' and f['scale']==0 for f in nur)
    bitzero=p.plan_for(p.AIBZ,'2100457')['facts']
    cashflow=[f for f in bitzero if f['concept'].endswith('}CashFlowsFromUsedInOperatingActivities')]
    assert [(f['period_start'],f['period_end'],f['expected_value']) for f in cashflow]==[
        ('2025-10-01','2026-03-31','-8225035'),('2024-10-01','2025-03-31','-12102491')]
