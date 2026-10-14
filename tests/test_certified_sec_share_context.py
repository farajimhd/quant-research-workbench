from hashlib import sha256

import pytest

from pipelines.certified_research.sec_share_context import extract


def document(value='1,234',dimension='',extra='',fact_extra=''):
    return f'''<html xmlns:x="http://www.xbrl.org/2003/instance" xmlns:d="http://xbrl.org/2006/xbrldi"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" xmlns:dei="http://xbrl.sec.gov/dei/2025"
      xmlns:g="http://fasb.org/us-gaap/2025" xmlns:t="http://www.xbrl.org/inlineXBRL/transformation/2020-02-12">
      <x:context id="c"><x:entity><x:identifier scheme="http://www.sec.gov/CIK">0000320193</x:identifier>
      <x:segment>{dimension}</x:segment></x:entity><x:period><x:instant>2025-10-17</x:instant></x:period></x:context>
      <x:unit id="s"><x:measure>x:shares</x:measure></x:unit>{extra}
      <ix:nonFraction id="f" name="dei:EntityCommonStockSharesOutstanding" contextRef="c" unitRef="s"
       format="t:num-dot-decimal" decimals="-3" {fact_extra}>{value}</ix:nonFraction></html>'''


def run(payload,**kwargs):return extract(payload,sha256(payload.encode()).hexdigest(),'320193',**kwargs)


def test_preserves_explicit_class_context_without_certifying_allocation():
    payload=document(dimension='<d:explicitMember dimension="g:StatementClassOfStockAxis">g:CommonStockMember</d:explicitMember>')
    result=run(payload);row=result['observations'][0]
    assert row['value']=='1234' and row['period_end']=='2025-10-17'
    assert row['dimensions']==(('{http://fasb.org/us-gaap/2025}StatementClassOfStockAxis','{http://fasb.org/us-gaap/2025}CommonStockMember'),)
    assert result['rejected']==() and result['allocation_certified'] is False
    assert row['decimals']=='-3' and row['source_text_hash']==sha256(payload.encode()).hexdigest()


def test_unscoped_fact_is_retained_and_large_scaled_number_is_exact():
    row=run(document('123456789012345678901234567890',fact_extra='scale="3"'))['observations'][0]
    assert row['dimensions']==() and row['value']=='123456789012345678901234567890000'


def test_whitespace_before_xml_declaration_is_recorded_without_changing_source_hash():
    payload='\n \t<?xml version="1.0"?>'+document()
    result=run(payload)
    assert result['parsing_prefix_bytes']==3
    assert result['source_text_hash']==sha256(payload.encode()).hexdigest()
    assert len(result['observations'])==1


def test_sec_xbrl_envelope_is_explicit_and_incomplete_envelope_fails():
    payload='<XBRL>\r<?xml version="1.0"?>'+document()+'</XBRL>\r'
    result=run(payload)
    assert result['sec_xbrl_envelope'] is True and result['parsing_prefix_bytes']==7
    assert result['parsing_suffix_bytes']==8 and len(result['observations'])==1
    with pytest.raises(ValueError,match='envelope'):run(payload.replace('</XBRL>',''))


@pytest.mark.parametrize('replacement,reason',[
    (('0000320193','0000000001'),'issuer_context_mismatch'),
    (('x:shares','x:USD'),'unsupported_share_unit'),
    (('t:num-dot-decimal','t:num-comma-decimal'),'unsupported_numeric_transform'),
    (('contextRef="c"','contextRef="absent"'),'missing_context_or_unit'),
    (('1,234','12,34'),'invalid_share_number'),
])
def test_rejections_preserve_candidate_and_reason(replacement,reason):
    result=run(document().replace(*replacement))
    assert result['observations']==() and result['rejected'][0]['reason']==reason


def test_hash_entity_and_complexity_fail_closed():
    with pytest.raises(ValueError,match='hash'):extract(document(),'a'*64,'320193')
    with pytest.raises(ValueError,match='Entity'):run('<!DOCTYPE html [<!ENTITY bad "x">]>'+document())
    with pytest.raises(ValueError,match='byte bound'):run(document(),maximum_bytes=10)
    with pytest.raises(ValueError,match='strict XML'):run(document()[:-7])


def test_unsupported_inline_namespace_is_not_silently_dropped():
    result=run(document().replace('2013/inlineXBRL','2008/inlineXBRL'))
    assert result['observations']==()
    assert result['rejected'][0]['reason']=='unsupported_inline_namespace'
