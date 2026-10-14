from hashlib import sha256

import pytest
from unittest.mock import patch

from pipelines.certified_research import sec_extraction_measurement as measurement
from tests.test_certified_sec_share_context import document
from scripts.prepare_certified_research import parse_args


def test_readonly_measurement_fetches_one_payload_and_does_not_qualify_rates():
    payload=document();calls=[]
    def query(sql):
        calls.append(sql)
        if 'count()' in sql:return [dict(n=1,bytes=len(payload.encode()),hashes=1,content_hash=sha256(payload.encode()).hexdigest())]
        return [dict(source_text=payload)]
    result=measurement.measure(query,(('320193','0000320193-25-000079','filing.htm'),))
    row=result['documents'][0]
    assert row['payload_fetches']==1 and len(calls)==3 and row['observations']==1
    assert row['fetch_seconds']>=0 and row['parse_seconds']>=0
    assert result['upper_bound_proven'] is False and result['storage_rate_qualified'] is False
    assert all(sql.startswith('SELECT ') for sql in calls)


def test_cli_measurement_is_explicit_bounded_and_cannot_acquire():
    base=['measure-sec-extraction','--measurement-sec-document','320193,0000320193-25-000079,filing.htm']
    assert len(parse_args(base).measurement_sec_document)==1
    for args in (['measure-sec-extraction'],base+['--acquire-missing'],base+['--acquire-sec-missing']):
        with pytest.raises(SystemExit):parse_args(args)


def test_readonly_conflict_report_retains_both_values_and_precision_fields():
    payload=document(extra='<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="c" unitRef="s" decimals="0">1235</ix:nonFraction>')
    def query(sql):
        if 'count()' in sql:return [dict(n=1,bytes=len(payload.encode()),hashes=1,content_hash=sha256(payload.encode()).hexdigest())]
        return [dict(source_text=payload)]
    bulk=(dict(source_record='s',taxonomy='dei',tag='EntityCommonStockSharesOutstanding',unit='shares',period_start=None,
        period_end='2025-10-17',dimensions=(),value='1234',source_numeric_type='Nullable(String)'),)
    with patch.object(measurement.sec_fundamental_reconciliation,'load_bulk',return_value=bulk):
        row=measurement.measure(query,(('320193','0000320193-25-000079','filing.htm'),),compare_bulk=True)['documents'][0]
    assert row['comparison_status_counts']=={'original_value_conflict':1}
    assert row['unmatched_original_context_counts']=={'unscoped':2}
    example=row['conflict_examples'][0]
    assert {v['value'] for v in example['originals']}=={'1234','1235'}
    assert all('decimals' in v and 'context_id' in v for v in example['originals'])
    assert row['public_time_qualified'] is False
