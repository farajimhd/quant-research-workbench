import json
import pytest
from pipelines.sec.edgar.sec_pipeline.clickhouse_writer import infer_storage_policy,verify_sec_write_storage,SecClickHouseWriter


class Client:
    def __init__(self,exists=True,policy='live_market_ssd',disk='live_market_ssd'):
        self.exists=exists;self.policy=policy;self.disk=disk
        self.commands=[]
    def execute(self,sql):
        self.commands.append(sql)
        if 'system.storage_policies' in sql:rows=[dict(disk='live_market_ssd')]
        elif 'system.tables' in sql:rows=[dict(name='sec_filing_v3',storage_policy=self.policy)] if self.exists else []
        else:rows=[dict(table='sec_filing_v3',disk_name=self.disk)] if self.exists else []
        return '\n'.join(json.dumps(r) for r in rows)


def test_absent_template_uses_verified_explicit_ssd_policy():
    assert infer_storage_policy(Client(exists=False),'q_live',['sec_filing_v3'])=='live_market_ssd'


def test_existing_policy_cannot_authorize_backup_storage():
    with pytest.raises(ValueError,match='misplaced'):
        infer_storage_policy(Client(policy='default'),'q_live',['sec_filing_v3'])


def test_existing_misplaced_parts_and_missing_writer_tables_block():
    with pytest.raises(ValueError,match='parts'):
        verify_sec_write_storage(Client(disk='default'),'q_live',['sec_filing_v3'])
    with pytest.raises(ValueError,match='missing'):
        verify_sec_write_storage(Client(exists=False),'q_live',['sec_filing_v3'])


def test_misplaced_writer_rejects_before_payload_insert():
    client=Client(policy='default')
    with pytest.raises(ValueError,match='misplaced'):
        SecClickHouseWriter(client,database='q_live').insert_rows('sec_filing_v3',[dict(accession_number='fixture')])
    assert not any(sql.startswith('INSERT') for sql in client.commands)


def test_inactive_parts_are_included_in_the_write_guard():
    client=Client(disk='default')
    with pytest.raises(ValueError,match='parts'):
        verify_sec_write_storage(client,'q_live',['sec_filing_v3'])
    part_query=next(sql for sql in client.commands if 'system.parts' in sql)
    assert 'WHERE active' not in part_query
