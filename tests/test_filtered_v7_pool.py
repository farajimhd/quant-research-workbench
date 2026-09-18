import asyncio
from contextlib import asynccontextmanager
import pytest
from src.backend import filtered_v7_preparation as m
from src.market_engine import v7_catalog as V
from src.market_engine import filtered_v7_history as H
from src.market_engine.derived_trade_policy import POLICY


@pytest.mark.parametrize('failure',[False,True])
def test_pool_bound_and_failure_reaps_siblings(tmp_path,monkeypatch,failure):
    built=set();live=set();peak=0;reports=[]
    class Catalog:
        root=tmp_path
        def select(self,ticker,day):return (dict(input_policy=POLICY) if ticker in built else {}),{}
        def sources(self,ticker):return [(None,{},None)]
    monkeypatch.setattr(V,'Catalog',Catalog)
    monkeypatch.setattr(H,'successor',lambda root,parent,ticker:(root/ticker,dict(rows=[dict(directory=ticker)])))
    @asynccontextmanager
    async def process(*args,**kwargs):
        nonlocal peak
        ticker=args[-1];live.add(ticker);peak=max(peak,len(live))
        class Child:
            returncode=None
            pid=1
            def poll(self):return self.returncode
        child=Child()
        async def done():
            await asyncio.sleep(.05 if failure and ticker=='A' else .3)
            built.add(ticker);child.returncode=1 if failure and ticker=='A' else 0
        task=asyncio.create_task(done())
        try:yield child
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True);live.remove(ticker)
    monkeypatch.setattr(m,'preparation_process',process)
    async def report(*args):pass
    async def detail(value):reports.append(value)
    async def run():
        await m.prepare(list('ABCDE'),['2026-08-21'],report,publish_details=detail,workers=2)
    if failure:
        with pytest.raises(ValueError,match='failed for A'):asyncio.run(run())
        assert 'E' not in built
        assert reports[-1]['failed']==1 and reports[-1]['active']==0
        assert {w['state'] for w in reports[-1]['workers']}=={'failed','cancelled'}
    else:
        asyncio.run(run())
        assert reports[-1]['completed']==5 and reports[-1]['built']==5
        assert [w['slot'] for w in reports[-1]['workers']]==[1,2]
    assert peak==2 and not live


def test_simultaneous_runs_share_process_budget():
    async def run():
        active=0;peak=0
        async def work():
            nonlocal active,peak
            async with m.process_limit():
                active+=1;peak=max(peak,active)
                await asyncio.sleep(.01)
                active-=1
        await asyncio.gather(*(work() for _ in range(12)))
        assert peak==4 and active==0
    asyncio.run(run())
