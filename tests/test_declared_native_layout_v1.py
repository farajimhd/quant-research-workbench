"""Restart-safe exact installation; no real database or authority fixture."""
import pytest
from src.trading_runtime.arte_journal_schema import TableContract
from src.backend import declared_native_layout_v1 as subject


def test_decimal_rendering_preserves_every_semantic_type_and_layout_property():
    t=TableContract('type_rendering_fixture',(('a','Decimal(38,18)'),
        ('b','Nullable(Decimal(20,7))'),('c','UInt64'),('d',"DateTime64(6, 'UTC')")),
        'tuple()', 'a')
    normalized,=subject.declared_native_storage_contracts((t,))
    assert normalized.columns==(('a','Decimal(38, 18)'),('b','Nullable(Decimal(20, 7))'),
        ('c','UInt64'),('d',"DateTime64(6, 'UTC')"))
    assert (normalized.name,normalized.partition,normalized.order,normalized.allow_nullable_key)==(
        t.name,t.partition,t.order,t.allow_nullable_key)
    assert t.columns[0][1]=='Decimal(38,18)'


class Client:
    def __init__(self):self.created=[];self.existing=set();self.fail=None
    def execute(self,sql):
        if sql.startswith('SELECT count()'):return '1'
        assert sql.startswith('CREATE TABLE IF NOT EXISTS arte.')
        name=sql.split('arte.')[1].split(' ')[0].split('\n')[0]
        self.existing.add(name);self.created.append(name)
        if name==self.fail:raise RuntimeError('fixture lost response')
        return ''


def configure(monkeypatch,client,bad=False):
    monkeypatch.setattr(subject,'_rows',lambda c,q: ([{'disks':['live_market_ssd']}] if 'storage_policies' in q
        else [{'name':n} for n in sorted(client.existing)]))
    def preflight(c,*,tables):
        if bad:raise RuntimeError('incompatible existing schema')
        assert all(t.name in c.existing for t in tables)
    monkeypatch.setattr(subject,'storage_preflight',preflight)


def test_plan_is_readonly_and_complete(monkeypatch):
    c=Client();configure(monkeypatch,c)
    result=subject.install_declared_native_layout(c)
    assert len(result.missing)==16 and result.created==() and c.created==[]


def test_exact_create_and_repeat_is_idempotent(monkeypatch):
    c=Client();configure(monkeypatch,c)
    result=subject.install_declared_native_layout(c,apply=True)
    assert len(result.created)==16
    assert subject.install_declared_native_layout(c,apply=True).missing==()
    assert len(c.created)==16


def test_incompatible_existing_blocks_before_any_ddl(monkeypatch):
    c=Client();c.existing.add(subject.CONTRACTS[0].name);configure(monkeypatch,c,bad=True)
    with pytest.raises(RuntimeError,match='incompatible'):subject.install_declared_native_layout(c,apply=True)
    assert c.created==[]


def test_lost_create_response_resumes_without_recreating_saved_table(monkeypatch):
    c=Client();configure(monkeypatch,c);c.fail=subject.CONTRACTS[0].name
    with pytest.raises(RuntimeError,match='lost response'):subject.install_declared_native_layout(c,apply=True)
    c.fail=None
    result=subject.install_declared_native_layout(c,apply=True)
    assert len(result.created)==15 and len(c.created)==16
