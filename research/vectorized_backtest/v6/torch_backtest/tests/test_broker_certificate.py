"""Fail-closed producer/broker lineage and physical-content certification."""
import sqlite3
from types import SimpleNamespace

import pytest

from research.vectorized_backtest.v6.torch_backtest import broker_certificate as module


@pytest.fixture
def audit(tmp_path, monkeypatch):
    ledger = tmp_path / 'ledger.sqlite3'
    with sqlite3.connect(ledger) as db:
        db.execute('CREATE TABLE units(build_id,session_date,ticker,stage,attempt_id,source_hash,output_rows,output_hash,status)')
        db.execute('INSERT INTO units VALUES(?,?,?,?,?,?,?,?,?)',
                   ('build','2026-07-30','AA','broker_100ms','attempt','canonical',2,'42','complete'))
    source = dict(build_id='build',definition_hash='definition',
                  units={'2026-07-30': {'AA': {'bars': {'source_hash':'canonical'}}}})
    actual = [dict(ticker='AA',n=2,u=2,hash='42')]
    statements=[]
    monkeypatch.setattr(module,'assert_liquidity_storage',lambda reader: None)
    def query(reader,statement):
        statements.append(statement)
        return actual
    monkeypatch.setattr(module,'query',query)
    return SimpleNamespace(ledger=ledger,source=source,actual=actual,statements=statements)


def certify(audit):
    return module.certify_broker_units(None,audit.source,audit.ledger,'2026-07-30',('AA',))


def test_full_unit_content_is_bound_without_app_authority(audit):
    first=certify(audit)
    assert first.token == certify(audit).token
    assert first.units[0].output_rows == 2
    assert first.evidence['app_release_fence_created'] is False
    assert first.evidence['market_data_written'] is False
    assert 'bucket_index>=' not in audit.statements[0]  # Full day, not replay slice.
    assert '(ticker,attempt_id) IN' in audit.statements[0]


@pytest.mark.parametrize('field,value',[('n',1),('u',1),('hash','43')])
def test_physical_corruption_fails_closed(audit,field,value):
    audit.actual[0][field]=value
    with pytest.raises(ValueError,match='content/key mismatch'):
        certify(audit)


def test_source_lineage_mismatch_fails_before_market_read(audit):
    audit.source['units']['2026-07-30']['AA']['bars']['source_hash']='different'
    with pytest.raises(ValueError,match='canonical source fingerprint mismatch'):
        certify(audit)
    assert not audit.statements


def test_incomplete_unit_fails_before_market_read(audit):
    with sqlite3.connect(audit.ledger) as db:
        db.execute("UPDATE units SET status='running'")
    with pytest.raises(ValueError,match='Incomplete producer broker unit'):
        certify(audit)
    assert not audit.statements


def test_missing_physical_unit_fails_closed(audit):
    audit.actual.clear()
    with pytest.raises(ValueError,match='content/key mismatch'):
        certify(audit)
