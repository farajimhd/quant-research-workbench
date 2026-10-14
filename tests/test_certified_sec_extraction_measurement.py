from hashlib import sha256

import pytest

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
