from hashlib import sha256
from unittest.mock import Mock

import pytest

from pipelines.certified_research.sec_document_payload import load


def read(query, payload='original', **kwargs):
    return load(query, '320193', '0000320193-25-000079', 'filing.htm',
                sha256(payload.encode()).hexdigest(), **kwargs)


def test_distinct_copies_keep_exact_bytes_and_index_predicate():
    payload='original é'
    query=Mock(side_effect=[
        [dict(content_sha256=sha256(payload.encode()).hexdigest(), bytes=len(payload.encode()))],
        [dict(source_text=payload)]])
    assert read(query,payload)==payload
    for call in query.call_args_list:
        sql=call.args[0]
        assert 'SELECT DISTINCT' in sql and 'PREWHERE cik IN' in sql
        assert 'content_sha256=' not in sql and 'FINAL' not in sql


@pytest.mark.parametrize('kind', ['missing','versions','oversized','corrupt','competing'])
def test_fail_closed_for_nonidentical_or_unbounded_payload(kind):
    metadata=[dict(content_sha256=sha256(b'original').hexdigest(),bytes=8)]
    rows=[dict(source_text='original')]
    if kind=='missing':metadata=[]
    if kind=='versions':metadata=metadata+[dict(content_sha256='a'*64,bytes=8)]
    if kind=='oversized':metadata[0]['bytes']=17*1024**2
    if kind=='corrupt':rows=[dict(source_text='tampered')]
    if kind=='competing':rows=rows+[dict(source_text='tampered')]
    query=Mock(side_effect=[metadata,rows])
    with pytest.raises(ValueError):read(query)
    assert query.call_count==(1 if kind in ('missing','versions','oversized') else 2)
