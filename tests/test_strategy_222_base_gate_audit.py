import sys
from pathlib import Path
from copy import deepcopy
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from strategy_222_base_gate_audit import recovery_audit,label_bases,decision_rows,H,digest


def test_prefix_compares_instants_across_timezone_offsets():
    import sqlite3
    with sqlite3.connect(':memory:') as connection:
        connection.execute('create table journal(sequence integer,event_time text,payload_json text,category text)')
        connection.executemany('insert into journal values(?,?,?,?)', [
            (1,'2026-08-21T04:20:00-07:00','{}','strategy_decision'),
            (2,'2026-08-21T04:20:01-07:00','{}','strategy_decision'),
            (3,'2026-08-21T11:19:59+00:00','{}','strategy_decision')])
        assert [r[0] for r in decision_rows(connection,'2026-08-21T11:20:00+00:00')]==[1,3]


def sample():
    settings=dict(H.DEFAULTS,setup_recovery_enabled=1,setup_recovery_stop_gain_guard=1,
        setup_base_recovery_maximum_range_pct=0)
    metadata=dict(reference_price=10.,research_base_assessment=dict(status='measured',observed_at=100.,
        checks={'fresh_support':True},failed=[],swing=dict(lower=9.8,pivot_at=95.,confirmed_at=99.),
        range={'start':90.,'end':99.},range_pct=2.,risk_pct=3.),setup_recovery={'last_exit':dict(
            at=80.,stop=10.1,body_high=10.5,setup={'phase':'post_breakout','breakout_threshold':10.2})})
    return metadata,settings


def test_passing_base_can_have_an_independent_recovery_blocker_without_mutation():
    metadata,settings=sample();original=deepcopy(metadata)
    result=recovery_audit(metadata,100.,settings)
    assert result['status']=='base_passed'
    assert result['recovery_reason']=='waiting_for_post_move_recovery_or_higher_base'
    assert metadata==original
    assert recovery_audit(metadata,100.,dict(settings,setup_base_recovery_maximum_range_pct=3))['recovery_reason']==''


def test_missing_and_failed_are_not_treated_as_passing():
    metadata,settings=sample()
    assert recovery_audit({},100.,settings)['status']=='unavailable'
    metadata['research_base_assessment']['checks']['fresh_support']=False
    metadata['research_base_assessment']['failed']=['fresh_support']
    assert recovery_audit(metadata,100.,settings)['status']=='base_failed'


def test_future_or_missing_recovery_evidence_fails_closed():
    metadata,settings=sample()
    with pytest.raises(ValueError,match='clock'):recovery_audit(metadata,99.,settings)
    metadata['setup_recovery']['last_exit']['at']=101.
    with pytest.raises(ValueError,match='Future'):recovery_audit(metadata,100.,settings)
    metadata.pop('setup_recovery')
    with pytest.raises(ValueError,match='authority'):recovery_audit(metadata,100.,settings)


def test_future_quotes_change_labels_not_recorded_stop_and_hash_drift_is_rejected(tmp_path):
    import json
    import numpy as np
    from datetime import datetime,timezone
    stamp=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
    row=dict(sequence=1,time=stamp(100),symbol='X',support_lower=9.8,first_blocker='macd',recovery_reason='')
    quotes=np.array([[100200000,10.,10.01,200.,200.],[101000000,10.6,10.61,200.,200.],[401000000,10.6,10.61,200.,200.]])
    path=tmp_path/'quotes-one.npz';ledger=tmp_path/'quote-ledger.json'
    def write():
        np.savez(path,data=quotes)
        ledger.write_text(json.dumps({'one':dict(status='completed',window=dict(symbol='X',start=stamp(99),end=stamp(410)),
            source_revision={'complete_for_history':True},sha256=digest(path))}))
    write();before=deepcopy(row)
    first,_=label_bases([row],H.DEFAULTS,.01,ledger)
    assert first[0]['stop']==pytest.approx(9.79) and first[0]['label']['profitable']
    quotes[1:,1:3]=[9.,9.01];write()
    second,_=label_bases([row],H.DEFAULTS,.01,ledger)
    assert second[0]['stop']==first[0]['stop'] and not second[0]['label']['profitable']
    assert row==before
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError,match='source changed'):label_bases([row],H.DEFAULTS,.01,ledger)
