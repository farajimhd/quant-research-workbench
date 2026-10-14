import pytest

from pipelines.certified_research.sec_precision import assess
from pipelines.certified_research.sec_precision import inventory
from pipelines.certified_research import sec_fundamental_context
from tests.test_certified_sec_share_context import document
from hashlib import sha256


def rows(*values):return tuple(dict(source_position=i,value=value,decimals=decimals) for i,(value,decimals) in enumerate(values))


def test_meta_different_display_precision_has_overlapping_intervals():
    result=assess(rows(('29754000000','-6'),('29750000000','-7')),mode='round-to-nearest')
    assert result['status']=='compatible_intervals'
    assert result['intersection']['lower']=='29753500000' and result['intersection']['upper']=='29754500000'
    assert result['certified'] is False and result['value_selected'] is False


def test_rounding_closed_endpoint_and_truncation_open_endpoint_differ():
    source=rows(('5000','-3'),('6000','-3'))
    assert assess(source,mode='round-to-nearest')['status']=='compatible_intervals'
    assert assess(source,mode='truncation')['status']=='disjoint_intervals'


def test_negative_zero_and_exact_infinite_precision():
    assert assess(rows(('-5000','-3'),('-5500','INF')),mode='truncation')['status']=='compatible_intervals'
    assert assess(rows(('0','0'),('1','INF')),mode='truncation')['status']=='disjoint_intervals'
    assert assess(rows(('10','INF'),('11','INF')),mode='round-to-nearest')['status']=='disjoint_intervals'


@pytest.mark.parametrize('value,decimals',[('1100000','-6'),('1',''),('NaN','INF'),('1e999999','0')])
def test_missing_excess_or_invalid_precision_cannot_pass(value,decimals):
    with pytest.raises(ValueError):assess(rows((value,decimals)),mode='round-to-nearest')


def test_inventory_includes_dimensioned_groups_and_reports_invalid_precision():
    payload=document(dimension='<d:explicitMember dimension="g:StatementClassOfStockAxis">g:CommonStockMember</d:explicitMember>')
    extraction=sec_fundamental_context.extract(payload,sha256(payload.encode()).hexdigest(),'320193')
    result=inventory(extraction)
    assert result['observations']==result['semantic_groups']==result['dimensioned_groups']==1
    assert result['mode_status_counts']['round-to-nearest']=={'unqualified_precision':1}
    assert result['certified'] is False


def test_inventory_rejects_mutated_extraction():
    payload=document();extraction=sec_fundamental_context.extract(payload,sha256(payload.encode()).hexdigest(),'320193')
    extraction['observations'][0]['value']='1'
    with pytest.raises(ValueError,match='extraction'):inventory(extraction)
