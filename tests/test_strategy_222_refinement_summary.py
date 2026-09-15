import json
import sqlite3
import tempfile
from pathlib import Path
import pytest
from scripts.summarize_strategy_222_refinement import episodes, terminal_account


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


def test_exit_uses_preceding_effective_stop_not_frozen_metadata_or_future_updates(tmp_path):
    path=tmp_path/'journal.sqlite3';connection=sqlite3.connect(path)
    connection.execute('create table journal(sequence integer,event_time text,payload_json text,category text,entity_type text)')
    def fill(side,order):
        return dict(symbol='X',side=side,size=10,price=6 if side=='B' else 5.79,commission=1,
                    order_id=order,canonical_metadata=dict(active_stop=5.77))
    def stop(price,phase='effective'):
        return dict(ticker='X',order_id='stop',kind='stop',phase=phase,active=True,price=price)
    records=[('execution','fill',fill('B','entry')),('protection','protection_change',stop(5.77)),
        ('protection','protection_change',stop(5.84)),('protection','protection_change',stop(5.9,'requested')),
        ('execution','fill',fill('S','stop')),('protection','protection_change',stop(6.1))]
    for i,(category,kind,payload) in enumerate(records):
        connection.execute('insert into journal values(?,?,?,?,?)',(i,f'2026-08-21T08:00:0{i}+00:00',json.dumps(payload),category,kind))
    connection.commit();connection.close()
    buys,sells=episodes(path)['episodes'][0]['fills']
    assert buys['effective_order_stop'] is None
    assert sells['stop']==5.77 and sells['stop_reference']=='frozen_order_metadata'
    assert sells['effective_order_stop']['price']==5.84
    assert sells['effective_order_stop']['sequence']==2


def account_journal(path, *, mark_time='2026-08-21T08:01:00+00:00', cash=9438., equity=10068., quantity=6., currency='USD'):
    with sqlite3.connect(path) as connection:
        connection.execute('create table journal(sequence integer,event_time text,payload_json text,category text,entity_type text)')
        records=[('execution','fill',dict(symbol='X',side='B',size=10,price=100.,commission=1.)),
                 ('execution','fill',dict(symbol='X',side='S',size=4,price=110.,commission=1.))]
        for i,(category,kind,payload) in enumerate(records):
            connection.execute('insert into journal values(?,?,?,?,?)',(i,f'2026-08-21T08:00:0{i}+00:00',json.dumps(payload),category,kind))
        if mark_time is not None:
            account=dict(totalcashvalue=dict(amount=cash,currency=currency),netliquidation=dict(amount=equity,currency=currency))
            position=dict(contractDesc='X',position=quantity,mktPrice=105.,mktValue=quantity*105.,currency=currency)
            for i,kind,payload in [(2,'portfolio',account),(3,'position',position)]:
                connection.execute('insert into journal values(?,?,?,?,?)',(i,mark_time,json.dumps(payload),'snapshot',kind))


def test_terminal_marks_include_realized_partial_exit_and_open_fees(tmp_path):
    path=tmp_path/'journal.sqlite3';account_journal(path)
    actual=episodes(path)['episodes']
    assert 'net' not in actual[0]  # Six shares remain open after selling four.
    result=terminal_account(path,actual,not_before='2026-08-21T08:01:00+00:00')
    assert result['status']=='verified'
    assert result['closed_net']==0 and result['marked_gain']==68
    assert result['open_episode_marked_net']==68 and result['open_episode_fees']==2
    assert result['cash']==9438 and result['position_market_value']==630
    assert result['reconciliation_error']==0


@pytest.mark.parametrize('updates,reason',[
    ({'quantity':5.},'position quantities'),
    ({'cash':9439.},'cash does not reconcile'),
    ({'equity':10069.},'equity does not reconcile'),
    ({'equity':float('nan')},'Invalid numeric'),
    ({'currency':'CAD'},'requires USD'),
])
def test_account_reconciliation_rejects_inconsistent_or_unsupported_marks(tmp_path,updates,reason):
    path=tmp_path/'journal.sqlite3';account_journal(path,**updates)
    with pytest.raises(ValueError,match=reason):terminal_account(path,episodes(path)['episodes'])


@pytest.mark.parametrize('mark_time,reason',[
    (None,'terminal_portfolio_snapshot_missing'),
    ('2026-08-21T07:59:00+00:00','portfolio_snapshot_precedes_run_or_fills'),
])
def test_missing_or_stale_account_marks_are_explicitly_unavailable(tmp_path,mark_time,reason):
    path=tmp_path/'journal.sqlite3';account_journal(path,mark_time=mark_time)
    result=terminal_account(path,episodes(path)['episodes'])
    assert result['status']=='unavailable' and result['reason']==reason
    assert 'marked_gain' not in result


def test_account_reconciliation_refuses_live_wal(tmp_path):
    path=tmp_path/'journal.sqlite3';Path(str(path)+'-wal').write_bytes(b'writer is active')
    with pytest.raises(ValueError,match='closed journal'):terminal_account(path,[])
