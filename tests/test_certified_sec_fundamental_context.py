from hashlib import sha256
import os

import pytest

from pipelines.certified_research import sec_fundamental_context as context
from tests.test_certified_sec_share_context import document


def run(payload):return context.extract(payload,sha256(payload.encode()).hexdigest(),'320193')


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
