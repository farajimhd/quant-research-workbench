"""Real normalized graph/cold replay; DB transport and source cert are seams."""
from copy import deepcopy
import asyncio
import json
from types import SimpleNamespace

import pytest

from test_profit_armed_structural_rejection_snapshot import packet
from test_profit_armed_structural_rejection_management import RUN
from test_profit_armed_structural_rejection_management import financial
from src.trading_runtime import profit_armed_structural_rejection_snapshot as snapshot
from src.trading_runtime.strategy_one_management_snapshot import SOURCE,BREAK,HIGH,CLOSED,FIRST_HELD
from src.trading_runtime.strategy_one_protection_snapshot import TABLES as PROTECTION


def source(monkeypatch):
    import test_profit_armed_structural_rejection_snapshot as fixtures
    from src.trading_runtime.strategy_one_stateful import StrategyOneEntryProposal
    def start(manager):
        async def drive():
            await manager.on_entry_proposal(StrategyOneEntryProposal('A1','DU1','AAA',100,0,
                10.,9.,12.,'ENTRY-R',.5,100,'S1',strategy_number=57))
            await manager.on_management(financial(),{},1000)
        asyncio.run(drive())
    monkeypatch.setattr(fixtures,'start',start)
    _,_,rows,bindings=packet(monkeypatch)
    old=rows.inherited
    families={snapshot.PARENT.name:(rows.snapshot,),
        **{contract.name:values for contract,values in zip(snapshot.CHILDREN,
            (rows.states,rows.bars,rows.links,rows.levels))},
        SOURCE.name:old.sources,BREAK.name:old.pending_breaks,HIGH.name:old.position_highs,
        CLOSED.name:old.closed_positions,FIRST_HELD.name:old.first_held_boundaries,
        PROTECTION[0].name:(old.protection.snapshot,),PROTECTION[1].name:old.protection.states,
        PROTECTION[2].name:old.protection.resistances}
    queries=[]
    def execute(sql):
        assert sql.startswith('SELECT ') and 'LIMIT ' in sql and 'FORMAT JSONEachRow' in sql
        queries.append(sql)
        table=sql.split(' FROM arte.',1)[1].split(' ',1)[0]
        return '\n'.join(json.dumps(row,default=str) for row in families[table])
    return SimpleNamespace(execute=execute),families,queries,rows,bindings


def test_complete_historical_read_replays_against_independent_source_bindings(monkeypatch):
    client,_,queries,rows,bindings=source(monkeypatch)
    loaded=snapshot.load_unattested_structural_rejection_snapshot(client,run_id=RUN,checkpoint_sequence=9)
    assert loaded==rows
    decoded=snapshot.restore_structural_rejection_snapshot(loaded,**bindings)
    assert len(decoded)==1 and decoded[0][3] is not None
    assert len(queries)==13 and all('SELECT ' in sql for sql in queries)
    assert all('trading_strategy_one_manager_snapshot' not in sql for sql in queries)


@pytest.mark.parametrize('kind',('missing-parent','duplicate-parent','parent-hash','foreign-cursor',
    'missing-bar','extra-bar','bar-hash','missing-protection','missing-entry','empty-child-orphan'))
def test_partial_foreign_and_resealed_graphs_are_not_adopted(monkeypatch,kind):
    client,families,_,_,_=source(monkeypatch)
    if kind=='missing-parent': families[snapshot.PARENT.name]=()
    elif kind=='duplicate-parent': families[snapshot.PARENT.name]*=2
    elif kind in ('parent-hash','foreign-cursor'):
        row=dict(families[snapshot.PARENT.name][0])
        row['content_hash' if kind=='parent-hash' else 'checkpoint_sequence']='0'*64 if kind=='parent-hash' else 10
        families[snapshot.PARENT.name]=(row,)
    elif kind=='missing-bar': families[snapshot.BAR.name]=families[snapshot.BAR.name][:-1]
    elif kind=='extra-bar': families[snapshot.BAR.name]+=families[snapshot.BAR.name][:1]
    elif kind=='bar-hash':
        row=deepcopy(families[snapshot.BAR.name][0]);row['close_int']+=1
        families[snapshot.BAR.name]=(row,*families[snapshot.BAR.name][1:])
    elif kind=='missing-protection': families[PROTECTION[0].name]=()
    elif kind=='missing-entry': families[SOURCE.name]=()
    else: families[CLOSED.name]=({'unexpected':True},)
    with pytest.raises((ValueError,RuntimeError)):
        snapshot.load_unattested_structural_rejection_snapshot(client,run_id=RUN,checkpoint_sequence=9)


def test_uint64_wire_strings_are_normalized_without_losing_bits(monkeypatch):
    client,families,_,rows,_=source(monkeypatch)
    for contract in snapshot.TABLES:
        uints={name for name,kind in contract.columns if 'UInt64' in kind}
        families[contract.name]=tuple({name:str(value) if name in uints and value is not None else value
            for name,value in row.items()} for row in families[contract.name])
    assert snapshot.load_unattested_structural_rejection_snapshot(client,run_id=RUN,checkpoint_sequence=9)==rows


@pytest.mark.parametrize('kind',('response','aggregate','nontext'))
def test_reader_enforces_transport_byte_bounds(monkeypatch,kind):
    client,_,_,_,_=source(monkeypatch)
    if kind=='nontext': client.execute=lambda sql:[]
    elif kind=='response': monkeypatch.setattr(snapshot,'MAX_COLD_RESPONSE_BYTES',10)
    else: monkeypatch.setattr(snapshot,'MAX_COLD_TOTAL_BYTES',10)
    with pytest.raises(ValueError,match='byte bound'):
        snapshot.load_unattested_structural_rejection_snapshot(client,run_id=RUN,checkpoint_sequence=9)
