from pipelines.certified_research import facts, sec_html_reviewed
from pipelines.certified_research.contracts import digest
from pipelines.certified_research.sec_html_reviewed_issuer_recovery import REVIEWED


def issuer(cik):
    return next(p for p in REVIEWED.values() if p['cik'] == cik)


def test_reviewed_sources_are_sealed_isolated_and_not_standard_aliases():
    for source_hash, expected in REVIEWED.items():
        plan = sec_html_reviewed.plan_for(source_hash, expected['cik'])
        assert plan == expected
        assert sec_html_reviewed.plan_for(source_hash, '0000000001') is None
        assert digest({k: v for k, v in plan.items() if k != 'plan_hash'}) == plan['plan_hash']
        positions = set()
        for fact in plan['facts']:
            locator = fact['locator']
            position = (locator['table'], locator['row'], tuple(locator['columns']))
            assert position not in positions
            positions.add(position)
            assert 'sec-html-native' in fact['concept']
            assert 'pro forma' not in fact['native']['statement_scope'].lower()
        plan['facts'].clear()
        assert sec_html_reviewed.plan_for(source_hash, expected['cik'])['facts']


def test_original_currencies_not_duplicate_convenience_translations():
    for cik, currency in [('0002046919', 'HKD'), ('0001854384', 'CNY'), ('0001872309', 'RUB')]:
        assert {f['unit'] for f in issuer(cik)['facts']} == {currency}
    assert facts.CURRENCY_UNITS['TWD'] == 23
    assert facts.CURRENCY_UNITS['RUB'] == 24
    assert facts.UNITS['RUB/share'] == 124


def test_successor_stub_is_not_mislabeled_full_year():
    rows = [f for f in issuer('0002125355')['facts'] if f['locator']['table'] == 58]
    assert {(f['period_start'], f['period_end']) for f in rows} == {
        ('2025-12-05', '2025-12-31'), ('2025-01-01', '2025-12-04'), ('2024-01-01', '2024-12-31')}
    assert all('Successor' in str(f['table_proof']['headers']) for f in rows)


def test_parent_predecessor_and_acquired_entities_do_not_collapse():
    joss = issuer('0002067746')['facts']
    assert any(f['native']['statement_scope'].startswith('JOSS REALTY REIT, INC.') for f in joss)
    assert any(f['native']['statement_scope'].startswith('JOSS REALTY COMBINED') for f in joss)
    assert not any('NAPA SQUARE' in f['native']['statement_scope'] for f in joss)
    assert all(f['native']['statement_scope'].startswith('Dion Holdco Limited')
               for f in issuer('0001873221')['facts'])
    assert all(f['locator']['table'] not in (717, 719, 720)
               for f in issuer('0001854384')['facts'])


def test_lirum_loss_sign_comes_from_cash_flow_statement_not_unsigned_display():
    rows = [f for f in issuer('0001965411')['facts'] if f['label'] == 'Net loss']
    assert rows and all(f['expected_value'].startswith('-') for f in rows)
    assert {f['locator']['table'] for f in rows} == {28, 36}
def test_earlier_vintages_preserve_semantics_and_are_hash_bound():
    from pipelines.certified_research.sec_html_reviewed_vintages import REGISTRY,plan_for
    from pipelines.certified_research.sec_html_reviewed_issuer_recovery import plan_for as original
    from pipelines.certified_research.contracts import digest
    assert len(REGISTRY)==35
    for h,row in REGISTRY.items():
        plan=plan_for(h,row['cik']);base=original(row['template'],row['cik'])
        body=dict(plan);seal=body.pop('plan_hash');assert digest(body)==seal
        assert plan_for(h,'0000000001') is None
        for fact,item in zip(plan['facts'],row['items']):
            expected=base['facts'][item['index']]
            assert {k:v for k,v in fact.items() if k not in ('locator','unit_locator','table_proof')} == {k:v for k,v in expected.items() if k not in ('locator','unit_locator','table_proof')}
