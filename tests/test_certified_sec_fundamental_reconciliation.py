from hashlib import sha256
import os
from unittest.mock import Mock

import pytest

from pipelines.certified_research import sec_fundamental_context,sec_fundamental_reconciliation as parity,sec_public_archive_time
from pipelines.certified_research.contracts import digest
from tests.test_certified_sec_share_context import document
from tests.test_certified_sec_public_archive_time import inputs


def inputs_for_parity():
    payload=document();extracted=sec_fundamental_context.extract(payload,sha256(payload.encode()).hexdigest(),'320193')
    witness,receipt=inputs();witness.pop('witness_hash');witness['document_hash']=extracted['source_text_hash']
    witness['witness_hash']=digest(witness)
    qualified=sec_public_archive_time.qualify(witness,receipt)
    row=dict(source_record='source1',taxonomy='dei',tag='EntityCommonStockSharesOutstanding',unit='shares',
        period_start=None,period_end='2025-10-17',dimensions=(),value='1234',source_numeric_type='String')
    return extracted,[row],witness,qualified,receipt


def test_exact_match_keeps_original_context_and_public_clock_without_certifying():
    result=parity.reconcile(*inputs_for_parity())
    assert result['all_bulk_matched'] and result['comparisons'][0]['original_positions']==(0,)
    assert result['available_at']=='2025-11-01T04:00:00+00:00'
    assert result['certified'] is False and result['allocation_certified'] is False


@pytest.mark.parametrize('field,value,status',[
    ('value','1235','value_mismatch'),('period_end','2025-10-18','missing_original_context'),
    ('dimensions',(('{axis}','{class}'),),'missing_original_context'),
    ('source_numeric_type','Float64','source_precision_unqualified')])
def test_mismatch_or_lossy_bulk_storage_cannot_pass(field,value,status):
    args=inputs_for_parity();args[1][0][field]=value
    result=parity.reconcile(*args)
    assert not result['all_bulk_matched'] and result['comparisons'][0]['status']==status


def test_mutation_and_float_transport_are_rejected():
    args=inputs_for_parity();args[0]['observations'][0]['value']='1'
    with pytest.raises(ValueError,match='fingerprint'):parity.reconcile(*args)
    args=inputs_for_parity();args[1][0]['value']=1234.0
    with pytest.raises(ValueError,match='decimal text'):parity.reconcile(*args)


def test_production_fundamentals_cannot_publish_without_original_parity():
    from pipelines.certified_research.reference_producer import run_sec
    query=Mock()
    with pytest.raises(ValueError,match='original-document parity'):
        run_sec(Mock(),'320193',(),query,Mock(),Mock())
    query.assert_not_called()


@pytest.mark.skipif(os.environ.get('CERTIFIED_RESEARCH_CLICKHOUSE_TEST')!='1',reason='explicit ClickHouse opt-in')
def test_real_retained_bulk_type_and_context_transport():
    from scripts import build_market_day as builder
    from pipelines.certified_research.facts import catalog
    args=builder.parse_args(['--date','2026-01-02','--env-file',str(builder.ROOT/'.env')])
    client=builder.Client(args,persistent=False,readonly=True)
    try:
        tags=tuple(sorted({tag for metric in catalog() for tag in metric.tags}))
        rows=parity.load_bulk(client.query,'320193','0000320193-25-000079',tags)
        assert rows and all(isinstance(row['value'],str) and isinstance(row['dimensions'],tuple) for row in rows)
        assert all(row['source_numeric_type'] for row in rows)
    finally:client.close()
