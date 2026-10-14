import hashlib
from types import SimpleNamespace
import pytest
from scripts.migrate_sec_acquisition_manifest_storage import Adapter,parse_args
from scripts.migrate_structure_checkpoint_storage import table_digest


def test_execution_requires_explicit_maintenance_and_nonnegative_reserve():
    with pytest.raises(SystemExit):parse_args(['--execute'])
    with pytest.raises(SystemExit):parse_args(['--reserve-gib','-1'])
    assert parse_args(['--execute','--writers-stopped']).execute


def test_streaming_digest_preserves_duplicate_raw_versions():
    client=SimpleNamespace(http=SimpleNamespace(iter_json_each_row=lambda sql:iter([dict(h='A'*64),dict(h='A'*64)])))
    result=Adapter(client).digest('SELECT')
    assert result==dict(rows=2,sha256=hashlib.sha256(('A'*64+'\n').encode()*2).hexdigest())
    client.http.iter_json_each_row=lambda sql:iter([dict(h='invalid')])
    with pytest.raises(RuntimeError):Adapter(client).digest('SELECT')


def test_raw_version_digest_does_not_use_final():
    from unittest.mock import Mock
    ch=Mock();ch.rows.return_value=[dict(p='202601')];ch.digest.return_value=dict(rows=2,sha256='a'*64)
    table_digest(ch,'source','value',final=False)
    assert ' FINAL ' not in ch.digest.call_args.args[0]
