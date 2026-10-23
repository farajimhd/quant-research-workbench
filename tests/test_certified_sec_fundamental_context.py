from hashlib import sha256
import os

import pytest

from pipelines.certified_research import sec_fundamental_context as context
from tests.test_certified_sec_share_context import document


def run(payload):return context.extract(payload,sha256(payload.encode()).hexdigest(),'320193')


@pytest.mark.parametrize('scheme', ('http', 'https'))
def test_ifrs_exact_namespace_and_bulk_identity(scheme):
    from pipelines.certified_research.sec_fundamental_reconciliation import compare_original
    payload=document('1234').replace('dei:EntityCommonStockSharesOutstanding','g:Assets')
    payload=payload.replace('http://fasb.org/us-gaap/2025',scheme+'://xbrl.ifrs.org/taxonomy/2025-03-27/ifrs-full')
    result=run(payload)
    assert not result['rejected']
    row=result['observations'][0]
    assert row['value']=='1234' and '/ifrs-full}' in row['concept']
    bulk=dict(row,source_record='test',taxonomy='ifrs-full',tag='Assets',source_numeric_type='String')
    assert compare_original(result,[bulk])['comparisons'][0]['status']=='matched'
    bulk['taxonomy']='us-gaap'
    assert compare_original(result,[bulk])['comparisons'][0]['status']=='missing_original_context'
    bad=payload.replace('xbrl.ifrs.org','xbrl.ifrs.org.example.com')
    assert run(bad)['rejected'][0]['reason']=='unsupported_share_taxonomy'


def test_duration_currency_and_negative_scaled_decimal_are_exact():
    payload=document('12345678901234567890.12',fact_extra='sign="-" scale="3"')
    payload=payload.replace('xmlns:g=', 'xmlns:iso="http://www.xbrl.org/2003/iso4217" xmlns:g=')
    payload=payload.replace('dei:EntityCommonStockSharesOutstanding','g:NetIncomeLoss').replace('x:shares','iso:USD')
    payload=payload.replace('<x:instant>2025-10-17</x:instant>',
        '<x:startDate>2025-01-01</x:startDate><x:endDate>2025-09-30</x:endDate>')
    result=run(payload);row=result['observations'][0]
    assert row['value']=='-12345678901234567890120'
    assert (row['period_start'],row['period_end'],row['unit'])==('2025-01-01','2025-09-30','USD')
    assert result['certified'] is False and result['original_vintage_qualified'] is False


def test_reversed_duration_and_unknown_taxonomy_are_retained_rejections():
    payload=document().replace('dei:EntityCommonStockSharesOutstanding','g:NetIncomeLoss')
    payload=payload.replace('<x:instant>2025-10-17</x:instant>',
        '<x:startDate>2025-10-17</x:startDate><x:endDate>2025-09-30</x:endDate>')
    assert run(payload)['rejected'][0]['reason']=='invalid_duration_order'
    payload=document().replace('http://xbrl.sec.gov/dei/2025','http://example.com/dei/2025')
    assert run(payload)['rejected'][0]['reason']=='unsupported_share_taxonomy'


def test_per_share_currency_unit_is_preserved():
    payload=document('2.50').replace('xmlns:g=', 'xmlns:iso="http://www.xbrl.org/2003/iso4217" xmlns:g=')
    payload=payload.replace('dei:EntityCommonStockSharesOutstanding','g:EarningsPerShareBasic')
    payload=payload.replace('<x:measure>x:shares</x:measure>',
        '<x:divide><x:unitNumerator><x:measure>iso:USD</x:measure></x:unitNumerator>'
        '<x:unitDenominator><x:measure>x:shares</x:measure></x:unitDenominator></x:divide>')
    row=run(payload)['observations'][0]
    assert row['value']=='2.50' and row['unit']=='USD/share'


def test_fixed_zero_preserves_display_text_and_rejects_wrong_registry():
    payload=document('—').replace('t:num-dot-decimal','t:fixed-zero')
    result=run(payload);row=result['observations'][0]
    assert row['value']=='0' and row['raw_text']=='—'
    result=run(payload.replace('transformation/2020-02-12','transformation/2015-02-26'))
    assert result['rejected'][0]['reason']=='unsupported_fixed_zero_namespace'


@pytest.mark.parametrize('text', ('no','None','NO'))
def test_sec_word_zero_preserves_original_text_and_exact_namespace(text):
    payload=document(text).replace('t:num-dot-decimal','t:numwordsen').replace(
        'http://www.xbrl.org/inlineXBRL/transformation/2020-02-12',
        'http://www.sec.gov/inlineXBRL/transformation/2015-08-31')
    row=run(payload)['observations'][0]
    assert row['value']=='0' and row['raw_text']==text
    bad=payload.replace('www.sec.gov/inlineXBRL','example.com/inlineXBRL')
    assert run(bad)['rejected'][0]['reason']=='unsupported_transform_namespace'


def test_unsupported_english_numbers_are_not_guessed_or_converted_to_zero():
    payload=document('not known').replace('t:num-dot-decimal','t:numwordsen').replace(
        'http://www.xbrl.org/inlineXBRL/transformation/2020-02-12',
        'http://www.sec.gov/inlineXBRL/transformation/2015-08-31')
    assert run(payload)['rejected'][0]['reason']=='unsupported_numwordsen_input'


def test_presentation_nodes_do_not_consume_evidence_tree_budget():
    payload=document().replace('</html>', '<div>'+'<span>x</span>'*250001+'</div></html>')
    extracted=run(payload)
    assert extracted['observations'][0]['value']=='1234'
    assert extracted['observations'][0]['source_position']==0
    assert not extracted['rejected']


def test_streaming_preserves_nested_fact_text_tail_and_positions():
    payload=document('1234').replace('>1234</ix:nonFraction>',
        '>1<span>2</span>34</ix:nonFraction>')
    row=run(payload)['observations'][0]
    assert row['raw_text']=='1234' and row['value']=='1234' and row['source_position']==0


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='explicit ClickHouse opt-in')
def test_real_retained_apple_catalog_extraction():
    from scripts import build_market_day as builder
    args=builder.parse_args(['--date','2026-01-02','--env-file',str(builder.ROOT/'.env')])
    client=builder.Client(args,persistent=False,readonly=True)
    try:
        rows=client.query("SELECT source_text,content_sha256 FROM q_live.sec_filing_text_v3 WHERE cik IN ('320193','0000320193') "
            "AND accession_number='0000320193-25-000079' AND document_type='10-K' AND length(source_text)<=16777216 LIMIT 2")
        assert len(rows)==1
        result=context.extract(rows[0]['source_text'],rows[0]['content_sha256'],'320193')
        assert len(result['observations'])>50
        assert any(row['period_start'] is not None for row in result['observations'])
        assert any(row['unit']=='USD' for row in result['observations'])
        assert result['certified'] is False
    finally:client.close()


def test_explicit_nil_is_retained_missing_not_zero_or_unresolved_numeric():
    from pipelines.certified_research import sec_precision
    payload=document('1234').replace('decimals="-3"','decimals="0"').replace('</html>',
        '<ix:nonFraction xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'name="g:Assets" contextRef="c" unitRef="u" xsi:nil="true" decimals="0"></ix:nonFraction></html>')
    # Use the fixture's exact context/unit IDs.
    import re
    payload=payload.replace('contextRef="c"', 'contextRef="'+re.search(r'contextRef="([^"]+)"',payload)[1]+'"')
    payload=payload.replace('unitRef="u"', 'unitRef="'+re.search(r'unitRef="([^"]+)"',payload)[1]+'"')
    result=run(payload)
    assert len(result['observations'])==1
    assert result['rejected'][0]['reason']=='nil_share_fact'
    inventory=sec_precision.inventory(result)
    assert inventory['declared_nil_positions']==(1,) and inventory['rejected_numeric_facts']==0
    assert sec_precision.gate(result,mode='round-to-nearest')['ready']
    bad=run(payload.replace('decimals="0"></ix:nonFraction>', 'decimals="0">123</ix:nonFraction>'))
    assert bad['rejected'][0]['reason']=='nil_fact_has_content'
    assert not sec_precision.gate(bad,mode='round-to-nearest')['ready']
