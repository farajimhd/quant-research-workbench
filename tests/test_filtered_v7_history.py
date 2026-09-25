from copy import deepcopy
import pytest

from src.market_engine import v7_catalog as V
from src.market_engine.filtered_v7_history import successor
from src.market_engine.level_book_store import read, write
from src.market_engine.historical_level_checkpoint import digest
from tests.test_v7_catalog import prepared


def publish(root, target, parent, ticker='TEST'):
    folder, plan = successor(root, parent, ticker)
    write(folder/'plan.json', plan)
    output = folder/'tickers'/ticker
    source = read(target/'source-plan.json')
    source['plan_hash'] = plan['plan_hash']
    source['reporting_coverage_hash'] = '0'*64
    write(output/'source-plan.json', source)
    book = read(target/'books'/'2026-08-20.json.gz')
    book['input_policy'] = V.POLICY
    book['checkpoint_hash'] = digest({k:v for k,v in book.items() if k!='checkpoint_hash'})
    write(output/'books'/'2026-08-20.json.gz', book)
    receipt = read(target/'receipts'/'2026-08-20.json')
    receipt['checkpoint_hash'] = book['checkpoint_hash']
    write(output/'receipts'/'2026-08-20.json', receipt)
    return output, plan


def publish_ready(output,plan):
    write(output/'ready.json',dict(plan_hash=plan['plan_hash'],
        source_plan_hash=digest(read(output/'source-plan.json'))))


def test_qmd_catalog_sees_atomic_successor_without_restart_or_hash_drift(tmp_path, monkeypatch):
    target,_ = prepared(tmp_path, monkeypatch)
    catalog = V.Catalog(tmp_path)
    parent = read(tmp_path/'main'/'plan.json')
    before = deepcopy(parent)
    fingerprint = catalog.fingerprint
    assert catalog.select('TEST','2026-08-21')[1]['campaign']=='main'
    output,plan = publish(tmp_path,target,parent)
    # Incomplete builds cannot replace the serving authority.
    assert catalog.select('TEST','2026-08-21')[1]['campaign']=='main'
    publish_ready(output,plan)
    book,provenance = catalog.select('TEST','2026-08-21')
    assert book['input_policy']==V.POLICY
    assert provenance['campaign'].replace('\\','/').startswith('filtered-v7-on-demand-v1/')
    assert catalog.fingerprint==fingerprint
    assert parent==before
    with pytest.raises(ValueError,match='no legacy fallback'):
        catalog.select('TEST','2026-08-22')


def test_corrupt_successor_cannot_fall_back_to_unfiltered_history(tmp_path,monkeypatch):
    target,_=prepared(tmp_path,monkeypatch)
    output,plan=publish(tmp_path,target,read(tmp_path/'main'/'plan.json'))
    write(output/'ready.json',dict(plan_hash='wrong'))
    with pytest.raises(ValueError,match='publication identity'):
        V.Catalog(tmp_path).select('TEST','2026-08-21')


def test_numerical_source_change_creates_distinct_immutable_successor(tmp_path,monkeypatch):
    from src.market_engine import filtered_v7_history as module
    prepared(tmp_path,monkeypatch)
    parent=read(tmp_path/'main'/'plan.json');before=deepcopy(parent)
    kernel=deepcopy(module.kernel())
    monkeypatch.setattr(module,'kernel',lambda:deepcopy(kernel))
    old_path,old=successor(tmp_path,parent,'TEST')
    kernel['source_files']['src/market_engine/reaction_center.py']='f'*64
    new_path,new=successor(tmp_path,parent,'TEST')
    assert old_path!=new_path and old['plan_hash']!=new['plan_hash']
    assert old['rows']==new['rows'] and old['parent_plan_hash']==new['parent_plan_hash']
    assert parent==before


def test_preparation_reuses_filtered_history_without_spawning(tmp_path,monkeypatch):
    import asyncio
    from src.backend.filtered_v7_preparation import prepare
    prepared(tmp_path,monkeypatch)
    catalog=V.Catalog(tmp_path)
    monkeypatch.setattr(V,'Catalog',lambda:catalog)
    def forbidden(*args,**kwargs):
        raise AssertionError('A verified history must not rebuild')
    monkeypatch.setattr('src.backend.filtered_v7_preparation.subprocess.Popen',forbidden)
    progress=[]
    async def report(*args):progress.append(args)
    asyncio.run(prepare(['TEST'],['2026-08-21'],report))
    assert progress[-1][:2]==(1,1)


def test_backtest_verifies_unfiltered_history_without_building(tmp_path,monkeypatch):
    import asyncio
    from src.backend.filtered_v7_preparation import prepare
    target,_=prepared(tmp_path,monkeypatch)
    path=target/'books'/'2026-08-20.json.gz'
    book=read(path);book.pop('input_policy',None)
    book['checkpoint_hash']=digest({k:v for k,v in book.items() if k!='checkpoint_hash'})
    write(path,book,immutable=False)
    path=target/'receipts'/'2026-08-20.json'
    receipt=read(path);receipt['checkpoint_hash']=book['checkpoint_hash']
    write(path,receipt,immutable=False)
    catalog=V.Catalog(tmp_path)
    monkeypatch.setattr(V,'Catalog',lambda:catalog)
    monkeypatch.setattr('src.backend.filtered_v7_preparation.subprocess.Popen',
        lambda *args,**kwargs: (_ for _ in ()).throw(AssertionError('no backtest builder')))
    snapshots=[]
    async def report(*args):pass
    async def details(value):snapshots.append(value)
    asyncio.run(prepare(['TEST'],['2026-08-21'],report,
        publish_details=details,verify_only=True))
    assert snapshots[-1]['built']==0
    assert snapshots[-1]['unavailable']==1
    assert catalog.select('TEST','2026-08-21')[0].get('input_policy')!=V.POLICY


@pytest.mark.parametrize('outcome', ['complete', 'failed', 'cancelled'])
def test_preparation_builds_unfiltered_ticker_then_reuses_publication(tmp_path,monkeypatch,outcome):
    import asyncio
    from src.backend.filtered_v7_preparation import prepare
    target,_=prepared(tmp_path,monkeypatch)
    path=target/'books'/'2026-08-20.json.gz'
    book=read(path);book.pop('input_policy',None)
    book['checkpoint_hash']=digest({k:v for k,v in book.items() if k!='checkpoint_hash'})
    write(path,book,immutable=False)
    path=target/'receipts'/'2026-08-20.json'
    receipt=read(path);receipt['checkpoint_hash']=book['checkpoint_hash'];write(path,receipt,immutable=False)
    catalog=V.Catalog(tmp_path)
    monkeypatch.setattr(V,'Catalog',lambda:catalog)
    calls=[]
    class Process:
        returncode=None
        terminated=False
        def poll(self):
            return self.returncode
        def wait(self, timeout=None):
            self.returncode=-1 if self.terminated else 0
        def terminate(self):
            self.terminated=True
    child=Process()
    def spawn(args,**kwargs):
        calls.append(args)
        if outcome=='cancelled':
            return child
        if outcome=='failed':
            child.returncode=1
            return child
        output,plan=publish(tmp_path,target,read(tmp_path/'main'/'plan.json'))
        publish_ready(output,plan)
        child.returncode=0
        return child
    monkeypatch.setattr('src.backend.filtered_v7_preparation.subprocess.Popen',spawn)
    async def report(*args):
        if calls and outcome=='cancelled':
            raise asyncio.CancelledError()
    if outcome=='cancelled':
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(prepare(['TEST'],['2026-08-21'],report))
        assert child.terminated and child.returncode==-1
        return
    if outcome=='failed':
        with pytest.raises(ValueError,match='preparation failed for TEST'):
            asyncio.run(prepare(['TEST'],['2026-08-21'],report))
        return
    asyncio.run(prepare(['TEST'],['2026-08-21'],report))
    asyncio.run(prepare(['TEST'],['2026-08-21'],report))
    assert len(calls)==1
    assert calls[0][-2:]==('--ticker','TEST')
    assert catalog.select('TEST','2026-08-21')[0]['input_policy']==V.POLICY


def test_prefix_serves_only_certified_boundary_and_detects_corruption(tmp_path,monkeypatch):
    target,_=prepared(tmp_path,monkeypatch)
    output,plan=publish(tmp_path,target,read(tmp_path/'main'/'plan.json'))
    source=read(output/'source-plan.json')
    receipt=read(output/'receipts'/'2026-08-20.json')
    value=dict(version=1,plan_hash=plan['plan_hash'],ticker='TEST',before='2026-08-21',through='2026-08-20',sessions=1,source_plan_hash=digest(source),checkpoint_hash=receipt['checkpoint_hash'])
    value['prefix_hash']=digest(value)
    path=output/'prefixes'/'2026-08-21.json';write(path,value)
    catalog=V.Catalog(tmp_path)
    assert catalog.select('TEST','2026-08-21')[1]['campaign']!='main'
    with pytest.raises(V.CoverageUnavailable):catalog.select('TEST','2026-08-22')
    value['sessions']=2;write(path,value,immutable=False)
    with pytest.raises(ValueError,match='prefix integrity'):catalog.select('TEST','2026-08-21')
    value['sessions']=1;write(path,value,immutable=False)
    (output/'books'/'2026-08-20.json.gz').unlink()
    with pytest.raises(ValueError,match='no legacy fallback'):catalog.select('TEST','2026-08-21')


def test_published_filtered_empty_history_cannot_serve_legacy_book(tmp_path,monkeypatch):
    target,_=prepared(tmp_path,monkeypatch)
    output,plan=publish(tmp_path,target,read(tmp_path/'main'/'plan.json'))
    receipt=read(output/'receipts'/'2026-08-20.json')
    receipt.update(state='empty',parent_hash=None);receipt.pop('checkpoint_hash')
    write(output/'receipts'/'2026-08-20.json',receipt,immutable=False)
    publish_ready(output,plan)
    with pytest.raises(V.CoverageUnavailable,match='No eligible preceding filtered'):
        V.Catalog(tmp_path).select('TEST','2026-08-21')
