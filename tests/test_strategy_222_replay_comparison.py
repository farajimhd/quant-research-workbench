import json
import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from scripts import compare_strategy_222_replays as C


def at(second):
    return (datetime(2026, 8, 19, 8, tzinfo=timezone.utc)+timedelta(seconds=second)).isoformat()


def fill(second, side, quantity, price, fee=1):
    return dict(time=at(second), side=side, quantity=quantity, price=price, fee=fee)


def episode(start, end, *, symbol='TEST', price=10, exit_price=11):
    return dict(symbol=symbol, net=999999, fills=[fill(start, 'B', 10, price), fill(end, 'S', 10, exit_price)])


def test_prefix_rebuilds_partial_position_without_later_outcome():
    row=episode(0, 30)
    row['fills'].insert(1, fill(10, 'S', 4, 12))
    row['fills'][-1]['quantity']=6
    prefix=C.prefix_episodes([row], at(20))[0]
    assert prefix['quantity']==6 and prefix['sell']==48 and prefix['fees']==2
    assert 'net' not in prefix and 'closed_at' not in prefix
    terminal=C.prefix_episodes([row], at(30))[0]
    assert terminal['net']==11 and terminal['quantity']==0
    assert row['net']==999999  # Input labels were neither imported nor mutated.


def test_delayed_and_many_to_many_entries_do_not_disappear_or_double_count():
    before=[episode(0, 20), episode(25, 40)]
    after=[episode(10, 30, exit_price=9), episode(32, 45)]
    result=C.compare(before, after, at(50))
    assert result['matches'][0]['overlapping_candidate_indices']==[0]
    assert result['matches'][1]['overlapping_candidate_indices']==[0, 1]
    assert result['match_counts']=={'overlapping_shifted_entry':2}
    assert result['matches'][0]['entry_time_changes_seconds']==[10]
    assert result['summaries']['candidate']['closed']==2
    assert result['summaries']['candidate']['closed_net']==-4
    assert result['candidate_entries_without_same_time_baseline']==[0, 1]


def test_inclusive_cutoff_and_timezone_equivalent_entry():
    before=[episode(10, 20)]
    after=[episode(10, 20)]
    after[0]['fills'][0]['time']='2026-08-19T04:00:10-04:00'
    result=C.compare(before, after, at(10))
    assert result['match_counts']=={'same_time_entry':1}
    assert result['matches'][0]['overlapping_candidate_indices']==[0]
    assert result['summaries']['candidate']['closed']==0
    assert result['summaries']['candidate']['open']==[dict(symbol='TEST', quantity=10)]


def test_adjacent_positions_are_not_claimed_as_overlaps():
    result=C.compare([episode(0, 10)], [episode(10, 20)], at(30))
    assert result['match_counts']=={'no_overlapping_candidate_entry':1}
    assert result['candidate_entries_without_same_time_baseline']==[0]


def test_bad_timestamps_and_oversold_positions_fail_closed():
    with pytest.raises(ValueError, match='timezone'):
        C.compare([], [], '2026-08-19T08:00:00')
    row=episode(0, 10);row['fills'][-1]['quantity']=11
    with pytest.raises(ValueError, match='sufficient position'):
        C.prefix_episodes([row], at(20))


@pytest.mark.parametrize('status,error,cutoff,valid',[
    ('completed', '', at(30), True), ('stopped', '', at(30), True),
    ('running', '', at(30), False), ('failed', '', at(30), False),
    ('stopped', 'checkpoint failed', at(30), False),
    ('completed', '', at(31), False),
    ('completed', '', '2026-08-18T08:00:20+00:00', False),
])
def test_terminal_authority_and_session_boundary(tmp_path, monkeypatch, status, error, cutoff, valid):
    monkeypatch.setattr(C, 'RUNTIME', tmp_path)
    monkeypatch.setattr(C, 'episodes', lambda path: dict(episodes=[]))
    rid=str(uuid4());path=tmp_path/'trading/backtest'/rid/'journal.sqlite3';path.parent.mkdir(parents=True)
    c=sqlite3.connect(path)
    c.execute('create table journal(run_id text,category text,sequence integer,event_time text,payload_json text)')
    for seq,stamp,payload in [(1,at(0),dict(status='running',config=dict(anchor_date='2026-08-19'))),
                              (2,at(30),dict(status=status,error=error))]:
        c.execute('insert into journal values(?,?,?,?,?)',(rid,'lifecycle',seq,stamp,json.dumps(payload)))
    c.commit();c.close()
    if valid:
        rows,authority=C.read_terminal_run(rid,cutoff)
        assert not rows and authority['terminal_time']==at(30)
    else:
        with pytest.raises(ValueError):C.read_terminal_run(rid,cutoff)
