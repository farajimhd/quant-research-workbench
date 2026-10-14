"""Ownership/source loss must stop every reference publication boundary."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pipelines.certified_research import reference_producer


@pytest.mark.parametrize('failed_write', [1, 2, 3, 4])
def test_materialization_checks_source_before_each_write(failed_write):
    writes=[]
    def source_guard():
        if len(writes)+1 == failed_write:
            raise ValueError('source changed')
    def query(sql, *, read=True):
        if not read:
            writes.append(sql)
            return []
        if 'HAVING count()!=1' in sql:return []
        if ' bad ' in sql:return [{'bad':0}]
        return [{'n':1,'unique_keys':1,'h0':1,'h1':2,'h2':3,'h3':4}]
    class Publisher:
        def __init__(self,query,database,claim):self.query=query
        def receipt(self,scope):return None
        def publish(self,scope,attempt,expected):
            self.query('INSERT INTO receipt',read=False)
            return expected
    scope=SimpleNamespace(rows_where=lambda attempt:'1')
    claim=SimpleNamespace(check=lambda:None)
    with patch.object(reference_producer,'ReferencePublisher',Publisher):
        with pytest.raises(ValueError,match='source changed'):
            reference_producer._materialize(scope,query,claim,source_guard,'arte',lambda attempt:'SELECT 1')
    assert len(writes)==failed_write-1


def test_source_check_cannot_outlive_claim_before_first_write():
    state={'owned':True};writes=[]
    def check():
        if not state['owned']:raise RuntimeError('ownership lost')
    def source_guard():state['owned']=False
    def query(sql,*,read=True):
        if not read:writes.append(sql)
        return []
    with pytest.raises(RuntimeError,match='ownership lost'):
        reference_producer._materialize(None,query,SimpleNamespace(check=check),source_guard,
            'arte',lambda attempt:'SELECT 1')
    assert writes==[]
