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
