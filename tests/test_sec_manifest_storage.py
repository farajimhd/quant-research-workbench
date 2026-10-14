from types import SimpleNamespace
import json
import pytest
from pipelines.sec.edgar import sec_filing_entity_backfill as entity
from pipelines.sec.edgar import sec_filing_text_rendered_v3_rebuild as rendered


class Client:
    def __init__(self,exists=False,policy='live_market_ssd'):
        self.exists=exists;self.policy=policy;self.commands=[]
    def execute(self,sql):
        self.commands.append(sql)
        if 'CREATE TABLE' in sql:self.exists=True;return ''
        if 'ALTER TABLE' in sql:return ''
        if 'system.storage_policies' in sql:rows=[dict(disk='live_market_ssd')]
        elif 'system.tables' in sql:rows=[dict(name='manifest',storage_policy=self.policy)] if self.exists else []
        else:rows=[]
        return '\n'.join(json.dumps(r) for r in rows)


@pytest.mark.parametrize('create',(entity.create_manifest_table,rendered.ensure_manifest_table,rendered.ensure_bundle_manifest_table))
def test_manifest_bootstrap_is_explicit_ssd_and_compressed(create):
    client=Client();args=SimpleNamespace(database='q_live',manifest_table='manifest',bundle_manifest_table='manifest')
    create(client,args)
    ddl=next(sql for sql in client.commands if 'CREATE TABLE' in sql)
    assert "storage_policy='live_market_ssd'" in ddl and "default_compression_codec='ZSTD(1)'" in ddl


@pytest.mark.parametrize('create',(entity.create_manifest_table,rendered.ensure_manifest_table,rendered.ensure_bundle_manifest_table))
def test_misplaced_manifest_blocks_before_create_or_alter(create):
    client=Client(exists=True,policy='default')
    args=SimpleNamespace(database='q_live',manifest_table='manifest',bundle_manifest_table='manifest')
    with pytest.raises(ValueError,match='misplaced'):create(client,args)
    assert not any('CREATE TABLE' in sql or 'ALTER TABLE' in sql for sql in client.commands)


def test_misplaced_manifest_blocks_before_row_or_payload_processing():
    client=Client(exists=True,policy='default')
    job=SimpleNamespace(database='q_live',manifest_table='manifest',bundle_manifest_table='manifest')
    for insert in (rendered.insert_partition_manifest,rendered.insert_bundle_manifest):
        with pytest.raises(ValueError,match='misplaced'):insert(client,job,None)
    with pytest.raises(ValueError,match='misplaced'):entity.insert_rows(client,'q_live','manifest',[dict(archive_key='fixture')])
    assert not any(sql.startswith('INSERT') for sql in client.commands)
