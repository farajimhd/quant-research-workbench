import json
import sqlite3
import tempfile
from pathlib import Path
import pytest
from scripts.summarize_strategy_222_refinement import episodes


def test_fill_aggregation_separates_closed_profit_from_open_cash_outlay():
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'journal.sqlite3'
        connection=sqlite3.connect(path)
        connection.execute('create table journal(sequence integer,event_time text,payload_json text,category text,entity_type text)')
        fills=[('B',10,5.,1.),('B',5,6.,1.),('S',15,7.,1.),('B',2,8.,1.)]
        for i,(side,size,price,commission) in enumerate(fills):
            connection.execute('insert into journal values(?,?,?,?,?)',(i,f'2026-08-21T08:00:0{i}+00:00',
                json.dumps(dict(symbol='X',side=side,size=size,price=price,commission=commission)), 'execution','fill'))
        connection.commit();connection.close()
        result=episodes(path)['episodes']
        assert len(result)==2 and result[0]['net']==22.
        assert result[0]['quantity']==0 and result[1]['quantity']==2
        assert 'net' not in result[1] and 'closed_at' not in result[1]
        connection=sqlite3.connect(path)
        connection.execute("update journal set payload_json=? where sequence=2",(json.dumps(dict(symbol='X',side='S',size=16,price=7.,commission=1.)),))
        connection.commit();connection.close()
        with pytest.raises(ValueError,match='Oversold'):episodes(path)


def test_interleaved_symbols_keep_independent_positions_and_costs(tmp_path):
    path=tmp_path/'journal.sqlite3'
    connection=sqlite3.connect(path)
    connection.execute('create table journal(sequence integer,event_time text,payload_json text,category text,entity_type text)')
    fills=[('X','B',10,5.),('Y','B',2,100.),('X','S',10,6.),('Y','S',2,90.)]
    for i,(symbol,side,size,price) in enumerate(fills):
        connection.execute('insert into journal values(?,?,?,?,?)',(i,f'2026-08-21T08:00:0{i}+00:00',
            json.dumps(dict(symbol=symbol,side=side,size=size,price=price,commission=1.)), 'execution','fill'))
    connection.commit();connection.close()
    result=episodes(path)['episodes']
    assert [(e['symbol'],e['net']) for e in result]==[('X',8.),('Y',-22.)]
