from pipelines.certified_research.sec_html_reviewed_gaps import plan_for,ADRX_HASH


def test_xjet_empro_keep_units_contexts_and_taxonomies():
    from pipelines.certified_research.sec_html_reviewed_xjet_empro import REVIEWED, plan_for
    for h, spec in REVIEWED.items():
        facts = plan_for(h, spec['cik'])['facts']
        assert plan_for(h, '1') is None
        assert len({(f['concept'], f['period_start'], f['period_end'], f['unit']) for f in facts}) == len(facts)
        if spec['cik'] == '0001960081':
            assert len(facts) == 22
            assert all(f['scale'] == 0 for f in facts if f['unit'] != 'USD')
            assert all(f['locator']['table'] == 38 for f in facts if f['concept'].endswith('}NetIncomeLoss'))
        else:
            assert len(facts) == 16
            assert all('ifrs-full}' in f['concept'] for f in facts)
            assert not any(f['unit'] == 'USD/shares' for f in facts)
            for f in facts:
                if f['label'] == '':
                    assert len(f['table_proof']['headers']) == 4


def test_origin_liftoff_keep_actual_contexts_and_share_units():
    from pipelines.certified_research.sec_html_reviewed_origin_liftoff import REVIEWED, plan_for
    for h, spec in REVIEWED.items():
        facts = plan_for(h, spec['cik'])['facts']
        assert len({(f['concept'], f['period_start'], f['period_end'], f['unit']) for f in facts}) == len(facts)
        assert all(f['scale'] == 0 for f in facts if f['unit'] in ('shares', 'USD/shares'))
        assert not any(f['concept'].endswith('}PaymentsToAcquirePropertyPlantAndEquipment') for f in facts)
        if spec['cik'] == '0001573901':
            assert len(facts) == 46
            assert not any(f['concept'].endswith('}Revenues') for f in facts)
        else:
            assert len(facts) == 48
            assert all(f['locator']['table'] not in (87, 88, 89) for f in facts)


def test_medical_actual_periods_do_not_repeat_comparative_balances_or_map_parent_investment():
    import json
    from pipelines.certified_research.sec_html_reviewed_medical import REVIEWED, plan_for
    counts = {'0001838003': 39, '0001958489': 27}
    for h, spec in REVIEWED.items():
        plan = plan_for(h, spec['cik'])
        assert plan_for(h, '1') is None
        assert json.loads(json.dumps(plan)) == plan
        facts = plan['facts']
        assert len(facts) == counts[spec['cik']]
        assert len({(f['concept'], f['period_start'], f['period_end'], f['unit']) for f in facts}) == len(facts)
        assert not any('parent deficit' in f['label'].lower() for f in facts)
        if spec['cik'] == '0001958489':
            cash = [f for f in facts if f['concept'].endswith('}CashAndCashEquivalentsAtCarryingValue')]
            assert len(cash) == 3 and all('There were no cash equivalents' in f['evidence'][-1] for f in cash)
            assert all(f['scale'] == 3 for f in facts)


def test_fort_actual_statements_keep_negative_parentheses_and_per_share_scale():
    from pipelines.certified_research.sec_html_reviewed_fort import plan_for, HASH
    plan = plan_for(HASH, '2092967')
    assert plan_for(HASH, '1') is None
    rows = {(f['concept'].split('}')[-1], f['period_end']): f for f in plan['facts']}
    assert len(rows) == 22
    eps = rows['DilutedEarningsLossPerShare', '2025-12-31']
    assert eps['expected_value'] == '-0.69' and eps['scale'] == 0
    assert eps['locator']['columns'] == [5, 6]
    assert rows['ProfitLoss', '2025-12-31']['expected_value'] == '-5830000'
    assert rows['CashFlowsFromUsedInOperatingActivities', '2025-12-31']['locator']['columns'] == [3, 4]
    assert not any('Weighted' in f['concept'] for f in plan['facts'])


def test_aya_consolidated_totals_preserve_units_and_section_boundaries():
    import json
    from pipelines.certified_research.sec_html_reviewed_aya import plan_for, HASH
    plan = plan_for(HASH, '1826836')
    assert plan_for(HASH, '1') is None
    assert json.loads(json.dumps(plan)) == plan
    rows = {(f['concept'].split('}')[-1], f['period_end']): f for f in plan['facts']}
    assert len(rows) == 28
    assert rows['Assets', '2025-12-31']['expected_value'] == '631733000'
    assert rows['DilutedEarningsLossPerShare', '2025-12-31']['expected_value'] == '0.32'
    assert rows['AdjustedWeightedAverageShares', '2025-12-31']['expected_value'] == '142370945'
    assert rows['ProfitLoss', '2025-12-31']['expected_value'] == '46280000'
    for fact in plan['facts']:
        if not fact['label']:
            assert len(fact['table_proof']['headers']) >= 3


def test_btq_dash_is_not_zero_and_canadian_units_are_preserved():
    from pipelines.certified_research.sec_html_reviewed_btq import plan_for,HASH
    rows=plan_for(HASH,'1821866')['facts'];assert len(rows)==17
    revenues=[r for r in rows if r['concept'].endswith('}Revenue')]
    assert len(revenues)==1 and revenues[0]['period_end']=='2025-03-31'
    assert all(r['unit'] in ('CAD','CAD/shares','shares') for r in rows)
    assert not any(r['expected_value']=='0' for r in rows)


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


def test_moove_keeps_reporting_currency_parent_profit_and_periods():
    from pipelines.certified_research.sec_html_reviewed_moove import HASH, plan_for
    plan = plan_for(HASH, '2026793')
    assert plan_for(HASH, '1') is None
    rows = {(f['concept'].split('}')[-1], f['period_start'], f['period_end']): f for f in plan['facts']}
    assert len(rows) == len(plan['facts']) == 68
    assert rows['ProfitLoss', '2023-01-01', '2023-12-31']['expected_value'] == '265984000'
    assert rows['ProfitLossAttributableToOwnersOfParent', '2023-01-01', '2023-12-31']['expected_value'] == '265694000'
    assert rows['DilutedEarningsLossPerShare', '2023-01-01', '2023-06-30']['expected_value'] == '-1.67'
    assert rows['Revenue', '2024-04-01', '2024-06-30']['expected_value'] == '2579624000'
    assert all(f['unit'] in ('BRL', 'BRL/shares') for f in plan['facts'])
    assert not any('StockholdersEquity' in f['concept'] for f in plan['facts'])
    assert all(len(f['table_proof']['headers']) >= 4 for f in plan['facts'] if not f['label'])


def test_optimi_fiscal_periods_and_lexical_precision():
    from pipelines.certified_research.sec_html_reviewed_optimi import HASH, plan_for
    plan = plan_for(HASH, '2027329')
    assert plan_for(HASH, '1') is None
    rows = {(f['concept'].split('}')[-1], f['period_start'], f['period_end']): f for f in plan['facts']}
    assert len(rows) == len(plan['facts']) == 38
    annual = rows['DilutedEarningsLossPerShare', '2024-10-01', '2025-09-30']
    assert (annual['expected_value'], annual['decimals'], annual['unit']) == ('-1.2', '1', 'CAD/shares')
    quarter = rows['DilutedEarningsLossPerShare', '2025-10-01', '2025-12-31']
    assert (quarter['expected_value'], quarter['decimals']) == ('-0.48', '2')
    assert rows['Assets', None, '2025-12-31']['expected_value'] == '14236203'
    assert all(f['unit'] in ('CAD', 'CAD/shares', 'shares') for f in plan['facts'])
    assert not any(f['expected_value'] == '0' for f in plan['facts'])


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
