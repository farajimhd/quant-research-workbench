import pytest
from pipelines.market_sip.events.native_reporting_repair import desired_flags_sql


def test_majority_class_avoids_building_the_largest_key_set():
    sql=desired_flags_sql('(SELECT * FROM evidence)',dict(n=100,timely=90,delayed=7,unknown=3))
    assert '=192)' in sql and '=0)' in sql and '=64)' not in sql
    assert sql.endswith(',64)')
    assert desired_flags_sql('evidence',dict(n=100,timely=100,delayed=0,unknown=0))=='64'
    assert desired_flags_sql('evidence',dict(n=100,timely=0,delayed=0,unknown=100))=='0'
    with pytest.raises(ValueError,match='cover'):desired_flags_sql('evidence',dict(n=100,timely=90,delayed=0,unknown=0))
