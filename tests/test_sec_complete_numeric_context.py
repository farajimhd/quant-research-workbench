from hashlib import sha256
import pytest
from pipelines.certified_research import sec_fundamental_context as context
from tests.test_certified_sec_share_context import document


def extract(payload,**kwargs):
    return context.extract(payload,sha256(payload.encode()).hexdigest(),'320193',**kwargs)


def test_full_numeric_scope_keeps_non_catalog_concepts_and_exact_identity():
    payload=document('1234').replace('dei:EntityCommonStockSharesOutstanding','g:AccruedInsuranceCurrent')
    assert not extract(payload)['observations']
    full=extract(payload,all_numeric=True)
    assert len(full['observations'])==1 and not full['rejected']
    assert full['observations'][0]['concept'].endswith('}AccruedInsuranceCurrent')
    assert full['numeric_census']==dict(source_facts=1,extracted=1,rejected=0)
    assert not full['whole_document_coverage']


def test_full_numeric_does_not_drop_unsupported_new_contexts():
    payload=document('1234').replace('dei:EntityCommonStockSharesOutstanding','g:AccruedInsuranceCurrent')
    payload=payload.replace('x:shares','x:unsupportedUnit')
    full=extract(payload,all_numeric=True)
    assert not full['observations']
    assert full['numeric_census']==dict(source_facts=1,extracted=0,rejected=1)
    assert full['rejected'][0]['reason']=='unsupported_fundamental_unit'


def test_full_numeric_keeps_fact_bounds_and_decimal_validation():
    payload=document('not a number').replace('dei:EntityCommonStockSharesOutstanding','g:AccruedInsuranceCurrent')
    assert extract(payload,all_numeric=True)['rejected']
    with pytest.raises(ValueError,match='bound'):
        extract(document('1'),all_numeric=True,maximum_facts=0)


def test_full_numeric_refuses_partial_html_mapping(monkeypatch):
    from pipelines.certified_research import sec_html_reviewed
    monkeypatch.setattr(sec_html_reviewed,'plan_for',lambda *args: {'facts':[]})
    with pytest.raises(ValueError,match='do not establish complete'):
        extract('<html/>',all_numeric=True)


def test_full_numeric_retains_issuer_unit_without_aliasing():
    payload=document('three').replace('xmlns:g=', 'xmlns:issuer="https://issuer.example/2025" xmlns:g=')
    payload=payload.replace('dei:EntityCommonStockSharesOutstanding','g:NumberOfReportableSegments')
    payload=payload.replace('x:shares','issuer:Segment').replace('t:num-dot-decimal','sec:numwordsen')
    payload=payload.replace('xmlns:g=', 'xmlns:sec="http://www.sec.gov/inlineXBRL/transformation/2015-08-31" xmlns:g=')
    full=extract(payload,all_numeric=True)
    assert not full['rejected']
    row=full['observations'][0]
    assert row['unit']=='{https://issuer.example/2025}Segment'
    assert row['value']=='3' and row['raw_text']=='three'
    bad=payload.replace('>three<','>about three<')
    assert extract(bad,all_numeric=True)['rejected'][0]['reason']=='unsupported_numwordsen_input'


def test_typed_date_context_is_distinct_and_validated():
    axis='RevenueRemainingPerformanceObligationExpectedTimingOfSatisfactionStartDateAxis'
    member=f'<d:typedMember dimension="g:{axis}"><g:{axis}.domain>2026-07-01</g:{axis}.domain></d:typedMember>'
    payload=document('0.5')
    payload=payload.replace('dei:EntityCommonStockSharesOutstanding','g:RevenueRemainingPerformanceObligationPercentage')
    payload=payload.replace('</x:entity>',f'<x:segment>{member}</x:segment></x:entity>')
    full=extract(payload,all_numeric=True)
    assert not full['rejected']
    assert full['observations'][0]['dimensions'][0][1].endswith('|2026-07-01')
    assert full['observations'][0]['dimensions'][0][1].startswith('typed-date|')
    assert extract(payload.replace('2026-07-01','2026-02-30'),all_numeric=True)['rejected']
    assert extract(payload.replace(axis,'OtherTypedAxis'),all_numeric=True)['rejected'][0]['reason']=='unsupported_typed_dimension'


@pytest.mark.parametrize('taxonomy,namespace',[
    ('srt','http://fasb.org/srt/2025'),('ecd','http://xbrl.sec.gov/ecd/2025')])
def test_exact_additional_standard_taxonomies(taxonomy,namespace):
    from pipelines.certified_research.sec_taxonomy import identity
    assert identity('{'+namespace+'}NativeConcept')==(taxonomy,'NativeConcept')
    with pytest.raises(ValueError):identity('{'+namespace.replace('/2025','/not-a-version')+'}NativeConcept')
