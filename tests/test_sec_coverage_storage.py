import json
import pytest
from pipelines.sec.edgar.sec_pipeline.coverage import SecCoverageConfig,ensure_coverage_table,verify_coverage_storage


class Client:
    def __init__(self,policy='live_market_ssd',disk='live_market_ssd'):
        self.exists=False;self.policy=policy;self.disk=disk;self.commands=[]
    def execute(self,sql):
        self.commands.append(sql)
        if 'CREATE TABLE' in sql:self.exists=True;return ''
        if 'system.storage_policies' in sql:rows=[dict(disk='live_market_ssd')]
        elif 'system.tables' in sql:rows=[dict(storage_policy=self.policy)] if self.exists else []
        else:rows=[dict(disk_name=self.disk)]
        return '\n'.join(json.dumps(r) for r in rows)


def test_empty_policy_is_explicit_ssd_not_clickhouse_default():
    client=Client();ensure_coverage_table(client,SecCoverageConfig('q_live','coverage',''))
    ddl=next(sql for sql in client.commands if 'CREATE TABLE' in sql)
    assert "storage_policy = 'live_market_ssd'" in ddl and "ZSTD(1)" in ddl


def test_existing_misplaced_table_blocks_without_ddl_or_migration():
    client=Client(policy='default');client.exists=True
    with pytest.raises(ValueError,match='misplaced'):ensure_coverage_table(client,SecCoverageConfig('q_live','coverage'))
    assert not any('CREATE TABLE' in sql for sql in client.commands)


def test_wrong_part_disk_or_explicit_backup_policy_blocks():
    client=Client(disk='default');client.exists=True
    with pytest.raises(ValueError,match='parts'):verify_coverage_storage(client,SecCoverageConfig('q_live','coverage'))
    with pytest.raises(ValueError,match='live_market_ssd'):
        ensure_coverage_table(Client(),SecCoverageConfig('q_live','coverage','hdd'))
