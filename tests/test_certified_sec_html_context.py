from copy import deepcopy
from hashlib import sha256
import pytest
from pipelines.certified_research import sec_html_context as html,sec_precision,sec_html_reviewed
from pipelines.certified_research.contracts import digest


def fixture():
    text='<html><p>December 31, 2018. USD in thousands.</p><table><tr><td>Total assets</td><td>(1,234)</td></tr></table></html>'
    source=sha256(text.encode()).hexdigest()
    fact=dict(concept='{http://fasb.org/us-gaap/2018}Assets',period_start=None,period_end='2018-12-31',
        unit='USD',scale=3,label='Total assets',evidence=['December 31, 2018. USD in thousands.'],
        expected_value='-1234000',decimals='-3',locator=dict(kind='cells',table=0,row=0,columns=[1],label_column=0))
    plan=dict(version=html.VERSION,document_hash=source,cik='0000000001',scope='reviewed_observations',facts=[fact])
    return text,source,seal(plan)


def seal(plan):
    plan=deepcopy(plan);plan.pop('plan_hash',None)
    return dict(plan,plan_hash=digest(plan))


def test_payment_sign_is_explicit_source_checked_and_preserved(monkeypatch):
    text,source,plan=fixture();fact=plan['facts'][0]
    fact.update(concept='{http://fasb.org/us-gaap/2021}PaymentsToAcquirePropertyPlantAndEquipment',
                period_start='2018-01-01',cash_outflow_sign='reported_negative_to_payment_positive')
    plan=seal(plan);out=html.extract(text,source,'1',plan)
    assert out['observations'][0]['value']=='1234000'
    assert out['observations'][0]['html_proof']['source_value']=='-1234000'
    monkeypatch.setattr(sec_html_reviewed,'plan_for',lambda *args:plan)
    html.verify_registered(out,'1')
    fact['concept']='{http://fasb.org/us-gaap/2021}Assets'
    with pytest.raises(ValueError,match='payment semantics'):html.extract(text,source,'1',seal(dict(plan,facts=[fact])))


def test_soulgate_preserves_rmb_and_excludes_duplicate_currency_columns():
    from pipelines.certified_research.sec_html_reviewed_soulgate import REVIEWED
    plan=next(iter(REVIEWED.values()))
    assert len(plan['facts'])==50
    assert {f['unit'] for f in plan['facts']}=={'CNY','CNY/shares','shares'}
    assert len([f for f in plan['facts'] if 'cash_outflow_sign' in f])==4
    assert all(f['locator']['table'] in (420,422,423,425,496,498,499,500) for f in plan['facts'])


def test_explicit_nil_cash_is_not_a_missing_marker(monkeypatch):
    text='<p>As of December 31, 2025, aggregate cash was $Nil. U.S. dollars.</p>'
    source=sha256(text.encode()).hexdigest()
    fact=dict(concept='{http://fasb.org/us-gaap/2025}CashAndCashEquivalentsAtCarryingValue',
        period_start=None,period_end='2025-12-31',unit='USD',scale=0,decimals='0',expected_value='0',
        label='aggregate cash',explicit_nil=True,evidence=['As of December 31, 2025, aggregate cash was $Nil.','U.S. dollars.'],
        locator=dict(kind='quote',quote='As of December 31, 2025, aggregate cash was $Nil.',value_text='Nil'))
    plan=seal(dict(version=html.VERSION,document_hash=source,cik='0000000001',scope='reviewed_observations',facts=[fact]))
    out=html.extract(text,source,'1',plan)
    assert out['observations'][0]['value']=='0' and out['observations'][0]['raw_text']=='Nil'
    monkeypatch.setattr(sec_html_reviewed,'plan_for',lambda *args:plan)
    html.verify_registered(out,'1')
    fact.pop('explicit_nil')
    with pytest.raises(ValueError,match='ambiguous or missing'):html.extract(text,source,'1',seal(dict(plan,facts=[fact])))


def test_current_exhibit_plans_keep_quarters_units_and_consolidation_separate():
    from pipelines.certified_research.sec_html_reviewed_current_ifrs import REVIEWED
    from pipelines.certified_research.sec_html_reviewed_dtss import REVIEWED as DTSS
    mda=next(p for p in REVIEWED.values() if p['cik']=='0001857047')
    revenue=[f for f in mda['facts'] if f['concept'].endswith('}Revenue')]
    assert {f['period_start'] for f in revenue}=={'2026-04-01','2025-04-01','2026-01-01','2025-01-01'}
    assert {f['unit'] for f in revenue}=={'CAD'}
    assert [f['expected_value'] for f in revenue]==['498600000','373300000','962700000','724300000']
    dtss=next(iter(DTSS.values()))
    assert len(dtss['facts'])==26
    assert {f['locator']['table'] for f in dtss['facts']}=={1,2,4}
    assert sum('cash_outflow_sign' in f for f in dtss['facts'])==2


def test_registered_tuple_plan_survives_json_without_allowing_semantic_changes(monkeypatch):
    import json
    text,source,plan=fixture()
    plan['facts']=tuple(plan['facts'])
    plan=seal(plan)
    result=json.loads(json.dumps(html.extract(text,source,'1',plan)))
    monkeypatch.setattr(sec_html_reviewed,'plan_for',lambda *args:plan)
    html.verify_registered(result,'1')
    result['html_plan']['facts'][0]['expected_value']='1234000'
    result['extraction_hash']=digest({k:v for k,v in result.items() if k!='extraction_hash'})
    with pytest.raises(ValueError):html.verify_registered(result,'1')


def test_native_measure_keeps_exact_identity_and_registered_source(monkeypatch):
    from pipelines.certified_research.sec_html_native import definition
    from pipelines.certified_research.sec_taxonomy import identity
    text,source,plan=fixture();fact=plan['facts'][0]
    scope='December 31, 2018. USD in thousands.'
    fact['native']=definition('1',fact['label'],scope)
    fact['concept']=fact['native']['concept']
    fact['table_proof']=dict(heading=scope,headers=[[0,['Total assets','(1,234)']]])
    plan=seal(plan);out=html.extract(text,source,'1',plan)
    assert out['observations'][0]['html_proof']['native']==fact['native']
    assert identity(fact['concept'],allow_extension=True)[0].startswith('html-native|')
    with pytest.raises(ValueError):identity(fact['concept'])
    monkeypatch.setattr(sec_html_reviewed,'plan_for',lambda *args:plan)
    html.verify_registered(out,'1')
    for key,value in [('issuer_cik','0000000002'),('label','Other assets')]:
        changed=deepcopy(plan);changed['facts'][0]['native'][key]=value
        with pytest.raises(ValueError):html.extract(text,source,'1',seal(changed))
    changed=deepcopy(plan);changed['facts'][0].pop('table_proof')
    with pytest.raises(ValueError):html.extract(text,source,'1',seal(changed))


def test_native_symbol_is_not_a_standard_currency_and_requires_local_proof():
    from pipelines.certified_research.sec_html_native import definition
    text='<p>Trust statement at December 31, 2018</p><table><tr><td>Income</td><td>$</td><td>123</td></tr></table>'
    source=sha256(text.encode()).hexdigest()
    native=definition('1','Income','Trust statement at December 31, 2018')
    fact=dict(concept=native['concept'],native=native,period_start=None,period_end='2018-12-31',
        unit='source:$',scale=0,decimals='0',expected_value='123',label='Income',evidence=[native['statement_scope']],
        table_proof=dict(heading=native['statement_scope'],headers=[[0,['Income','$','123']]]),
        locator=dict(kind='cells',table=0,row=0,label_column=0,columns=[2]),unit_locator=dict(table=0,row=0,column=1))
    plan=dict(version=html.VERSION,document_hash=source,cik='0000000001',scope='reviewed_observations',facts=[fact])
    out=html.extract(text,source,'1',seal(plan))
    assert sec_precision.select_reported(out,mode='round-to-nearest')['groups'][0]['unit']=='source:$'
    changed=deepcopy(plan);changed['facts'][0]['unit_locator']['column']=2
    with pytest.raises(ValueError,match='symbol'):html.extract(text,source,'1',seal(changed))
    changed=deepcopy(plan);changed['facts'][0].pop('native');changed['facts'][0]['concept']='{http://fasb.org/us-gaap/2018}Assets'
    with pytest.raises(ValueError,match='unit'):html.extract(text,source,'1',seal(changed))


def test_royalty_plans_preserve_native_accounting_and_unknown_currency():
    from pipelines.certified_research.sec_html_reviewed_royalty import REVIEWED
    assert len(REVIEWED)==7 and sum(len(p['facts']) for p in REVIEWED.values())==76
    for key,plan in REVIEWED.items():
        assert sec_html_reviewed.plan_for(key,plan['cik'])==plan
        assert sec_html_reviewed.plan_for(key,'9999999999') is None
        assert all(f['unit']=='source:$' and f['native']['concept']==f['concept'] for f in plan['facts'])


def test_exact_cells_precision_and_scoped_source_proof(monkeypatch):
    text,source,plan=fixture();out=html.extract(text,source,'1',plan)
    assert out['observations'][0]['value']=='-1234000'
    assert out['observations'][0]['raw_text']=='(1,234)'
    assert out['whole_document_coverage'] is False
    assert len(sec_precision.select_reported(out,mode='round-to-nearest')['groups'])==1
    monkeypatch.setattr(sec_html_reviewed,'plan_for',lambda *args:plan)
    html.verify_registered(out,'1')
    altered=deepcopy(out);altered['observations'][0]['decimals']='0'
    altered.pop('extraction_hash');altered['extraction_hash']=digest(altered)
    with pytest.raises(ValueError,match='semantic'):html.verify_registered(altered,'1')


@pytest.mark.parametrize('change',['hash','issuer','period','unit','value','precision','label','source','scope','duplicate','evidence'])
def test_review_and_original_identity_fail_closed(change):
    text,source,plan=fixture();fact=plan['facts'][0];cik='1'
    if change=='hash':plan['document_hash']='0'*64
    elif change=='issuer':cik='2'
    elif change=='period':fact['period_end']='2018-13-31'
    elif change=='unit':fact['unit']='dollars'
    elif change=='value':fact['expected_value']='-1234001'
    elif change=='precision':fact['decimals']='0'
    elif change=='label':fact['label']='Current assets'
    elif change=='source':text=text.replace('1,234','1,235')
    elif change=='scope':plan['scope']='full_filing'
    elif change=='duplicate':plan['facts'].append(deepcopy(fact))
    elif change=='evidence':fact['evidence']=['2017 USD in millions']
    with pytest.raises(ValueError):html.extract(text,source,cik,seal(plan))


@pytest.mark.parametrize('number',['—','-','N/A','1e3','1,23','1 234','NaN'])
def test_missing_or_ambiguous_cells_never_become_numbers(number):
    text,source,plan=fixture();text=text.replace('(1,234)',number);source=sha256(text.encode()).hexdigest()
    plan['document_hash']=source
    with pytest.raises(ValueError,match='ambiguous or missing'):html.extract(text,source,'1',seal(plan))


@pytest.mark.parametrize('number', ['R$(1,234)', 'R$ (1,234)'])
def test_brazilian_currency_prefix_requires_matching_reviewed_unit(number):
    text, source, plan = fixture()
    text = text.replace('(1,234)', number).replace('USD', 'BRL')
    source = sha256(text.encode()).hexdigest()
    plan['document_hash'] = source
    fact = plan['facts'][0]
    fact.update(unit='BRL', evidence=['December 31, 2018. BRL in thousands.'])
    assert html.extract(text, source, '1', seal(plan))['observations'][0]['value'] == '-1234000'
    fact['unit'] = 'USD'
    with pytest.raises(ValueError, match='currency prefix'):
        html.extract(text, source, '1', seal(plan))


def test_no_fuzzy_quote_or_duplicate_occurrence():
    text='Shares outstanding as of December 31, 2018 were 123.';source=sha256(text.encode()).hexdigest()
    _,_,plan=fixture();plan['document_hash']=source;f=plan['facts'][0]
    f.update(label='Shares',unit='shares',scale=0,decimals='0',expected_value='123',evidence=[text],
        locator=dict(kind='quote',quote=text,value_text='123'))
    assert html.extract(text,source,'1',seal(plan))['observations'][0]['value']=='123'
    doubled=text+' '+text;source=sha256(doubled.encode()).hexdigest();plan['document_hash']=source
    with pytest.raises(ValueError,match='ambiguous'):html.extract(doubled,source,'1',seal(plan))


def test_nested_table_cannot_masquerade_as_flat_financial_row():
    text,source,plan=fixture();text=text.replace('Total assets','Total assets<table><tr><td>hidden</td></tr></table>')
    source=sha256(text.encode()).hexdigest();plan['document_hash']=source
    with pytest.raises(ValueError,match='Nested'):html.extract(text,source,'1',seal(plan))


@pytest.mark.parametrize('change',['subject','header'])
def test_statement_subject_and_physical_period_headers_fail_closed(change):
    text,source,plan=fixture()
    plan['facts'][0]['table_proof']=dict(heading='December 31, 2018. USD in thousands.',
        headers=[[0,['Total assets','(1,234)']]])
    assert html.extract(text,source,'1',seal(plan))['observations'][0]['value']=='-1234000'
    if change=='subject':plan['facts'][0]['table_proof']['heading']='Subsidiary financial statements'
    else:plan['facts'][0]['table_proof']['headers'][0][1]=['Total assets','2017']
    with pytest.raises(ValueError,match='heading|headers'):html.extract(text,source,'1',seal(plan))


def test_share_classes_remain_separate_and_registered_readback_rejects_relabeling(monkeypatch):
    text='At January 31, 2019: 123 shares Class A common stock; 456 shares Class B common stock.'
    source=sha256(text.encode()).hexdigest();_,_,plan=fixture();plan['document_hash']=source
    template=plan['facts'][0];selected=[]
    for value,klass in [('123','Class A common stock'),('456','Class B common stock')]:
        fact=deepcopy(template);quote=value+' shares '+klass
        fact.update(concept='{http://xbrl.sec.gov/dei/2018}EntityCommonStockSharesOutstanding',
            period_start=None,period_end='2019-01-31',unit='shares',scale=0,decimals='0',expected_value=value,
            label='shares',evidence=[text],locator=dict(kind='quote',quote=quote,value_text=value),
            dimensions=[['reviewed-html:share-class',klass]])
        selected.append(fact)
    plan['facts']=selected;plan=seal(plan);out=html.extract(text,source,'1',plan)
    assert len(sec_precision.select_reported(out,mode='round-to-nearest')['groups'])==2
    assert len({r['context_id'] for r in out['observations']})==2
    monkeypatch.setattr(sec_html_reviewed,'plan_for',lambda *args:plan)
    html.verify_registered(out,'1')
    altered=deepcopy(out);altered['observations'][0]['dimensions']=()
    altered.pop('extraction_hash');altered['extraction_hash']=digest(altered)
    with pytest.raises(ValueError,match='semantic'):html.verify_registered(altered,'1')
    plan['facts'][0]['dimensions']=[['reviewed-html:share-class','Class C capital stock']]
    with pytest.raises(ValueError,match='share-class'):html.extract(text,source,'1',seal(plan))


def test_reviewed_registry_keeps_source_issuer_and_reporting_basis():
    from pipelines.certified_research.sec_html_reviewed_2019 import REVIEWED
    assert len(REVIEWED)==24
    assert sum(len(v['facts']) for v in REVIEWED.values())==175
    for source,row in REVIEWED.items():
        plan=sec_html_reviewed.plan_for(source,row['cik'])
        assert plan['scope']=='reviewed_observations'
        assert sec_html_reviewed.plan_for(source,'9999999999') is None
        body=dict(plan);body.pop('plan_hash');assert digest(body)==plan['plan_hash']
    joint='6d1cb007f8718f2abfc052f12c2ea93f36b042dcc6da415ef63144f1d25472dd'
    assert sec_html_reviewed.plan_for(joint,'1592706') is None
    assert sec_html_reviewed.plan_for(joint,'109177')['facts'][0]['expected_value']=='53423237'
    novo=sec_html_reviewed.plan_for('6b92ee048096f8fb19451efcdfa114b5263ee888134dc55113e67e7c0fa7e8c0','353278')
    assert all(f['unit']=='DKK' and f['scale']==6 for f in novo['facts'])
    assert {f['period_end'] for f in novo['facts']}=={f'{year}-12-31' for year in range(2014,2019)}
    ares=sec_html_reviewed.plan_for('783727213c37bb808ad29c6d733c764645e9c793880d878aa385f0f5769d9ab0','1287750')
    assert {f['locator']['table'] for f in ares['facts']}=={270}  # Table 201 is an investee.
