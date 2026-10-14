from hashlib import sha256
import os

import pytest

from pipelines.certified_research import sec_security_cover as cover
from tests.test_certified_sec_share_context import document


def payload(extra=''):
    fields=''.join(f'<ix:nonNumeric name="dei:{tag}" contextRef="c">{value}</ix:nonNumeric>' for tag,value in
        [('Security12bTitle','Common stock'),('TradingSymbol','AAPL'),('SecurityExchangeName','Nasdaq')])
    return document(extra=fields+extra)


def run(text):return cover.extract(text,sha256(text.encode()).hexdigest(),'320193')


def test_cover_fields_are_candidates_without_class_completeness():
    result=run(payload());row=result['candidates'][0]
    assert row['status']=='candidate' and row['fields']['TradingSymbol']==('AAPL',)
    assert row['period_end']=='2025-10-17'
    assert result['class_completeness_proven'] is False and result['allocation_certified'] is False


def test_conflicting_or_continued_cover_fields_remain_unresolved():
    row=run(payload('<ix:nonNumeric name="dei:TradingSymbol" contextRef="c">OTHER</ix:nonNumeric>'))['candidates'][0]
    assert row['reason']=='competing_cover_fields'
    result=run(payload().replace('name="dei:TradingSymbol"','continuedAt="later" name="dei:TradingSymbol"'))
    assert result['rejected'] and result['candidates'][0]['reason']=='incomplete_cover_fields'


def test_reviewed_sec_exchange_transform_keeps_raw_value_and_normalized_code():
    text=payload().replace('xmlns:g=', 'xmlns:s="http://www.sec.gov/inlineXBRL/transformation/2015-08-31" xmlns:g=')
    text=text.replace('name="dei:SecurityExchangeName"','format="s:exchnameen" name="dei:SecurityExchangeName"')
    text=text.replace('>Nasdaq</ix:nonNumeric>','>The Nasdaq Stock Market LLC</ix:nonNumeric>')
    result=run(text)
    assert result['candidates'][0]['fields']['SecurityExchangeName']==('NASDAQ',)
    row=next(r for r in result['observations'] if r['concept'].endswith('SecurityExchangeName'))
    assert row['value']=='The Nasdaq Stock Market LLC' and row['transformed_value']=='NASDAQ'
    assert run(text.replace('The Nasdaq Stock Market LLC','Unknown exchange'))['rejected'][0]['reason']=='unsupported_exchange_transform_input'


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='explicit ClickHouse opt-in')
def test_real_retained_apple_security_cover():
    from scripts import build_market_day as builder
    args=builder.parse_args(['--date','2026-01-02','--env-file',str(builder.ROOT/'.env')])
    client=builder.Client(args,persistent=False,readonly=True)
    try:
        rows=client.query("SELECT source_text,content_sha256 FROM q_live.sec_filing_text_v3 WHERE cik IN ('320193','0000320193') "
            "AND accession_number='0000320193-25-000079' AND document_type='10-K' AND length(source_text)<=16777216 LIMIT 2")
        assert len(rows)==1
        result=cover.extract(rows[0]['source_text'],rows[0]['content_sha256'],'320193')
        assert any('AAPL' in row['fields']['TradingSymbol'] for row in result['candidates'])
        assert result['allocation_certified'] is False
    finally:client.close()
