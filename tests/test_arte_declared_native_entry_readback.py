"""Actual scalar query/readback against complete own producer fixtures."""
import json
from dataclasses import replace

import pytest

from test_arte_declared_native_command_v4 import case
from src.trading_runtime import arte_declared_native_entry_readback as module


def install_wire(c, mutate=None, readonly='1'):
    client = c.x.client
    original = client.execute
    calls = []
    def execute(sql):
        if sql == "SELECT getSetting('readonly')":
            return readonly
        for name, rows in c.packet.families:
            if f'FROM arte.{name} ' in sql:
                calls.append(sql)
                wire = [dict(row) for row in rows]
                if mutate is not None:
                    mutate(name, wire)
                return '\n'.join(json.dumps(row) for row in wire)
        return original(sql)
    client.execute = execute
    return calls


def read(c, base=None):
    return module.read_declared_entry_rows(c.packet.base if base is None else base,
        **c.args, predecessor=c.predecessor)


def test_complete_database_scalar_readback_reloads_all_source_families(case):
    calls = install_wire(case)
    assert read(case) == case.packet
    assert len(calls) == 5
    assert all('WHERE run_id=' in sql and 'AND batch_id=toUUID(' in sql for sql in calls)
    assert all('parent_record_id=' not in sql for sql in calls)
    assert all('output_format_json_quote_64bit_integers=0' in sql for sql in calls)
    assert ['LIMIT 2' in calls[0], 'LIMIT 5' in calls[1]] == [True, True]


@pytest.mark.parametrize('failure', ['missing', 'extra', 'foreign', 'alias', 'column', 'order', 'hash'])
def test_wire_graph_rejects_omission_foreign_sibling_or_scalar_drift(case, failure):
    def mutate(name, rows):
        if name != module.TABLES[1].name:
            return
        if failure == 'missing': rows.pop()
        elif failure == 'extra': rows.append(rows[0])
        elif failure == 'foreign': rows[0]['parent_record_id'] = '11111111-1111-4111-8111-111111111111'
        elif failure == 'alias': rows[0]['ordinal'] = True
        elif failure == 'column': rows[0]['metadata'] = '{}'
        elif failure == 'order': rows.reverse()
        else: rows[0]['content_hash'] = 'f'*64
    install_wire(case, mutate)
    with pytest.raises(ValueError): read(case)


def test_mutable_principal_or_foreign_run_rejects_before_table_reads(case):
    calls = install_wire(case, readonly='0')
    with pytest.raises(ValueError, match='SELECT-only'): read(case)
    assert calls == []
    with pytest.raises(ValueError, match='crossed run'):
        read(case, replace(case.packet.base, run_id='11111111-1111-4111-8111-111111111111'))
    assert calls == []


def test_rows_do_not_grant_historical_financial_admission(case):
    install_wire(case)
    packet = read(case)
    from src.trading_runtime.arte_declared_native_command_v4 import verify_declared_entry_financial_admission
    with pytest.raises(ValueError, match='historical'):
        verify_declared_entry_financial_admission(packet, **case.args, predecessor=case.predecessor)


def test_wire_duplicate_scalar_columns_cannot_be_silently_normalized(case):
    install_wire(case)
    original = case.x.client.execute
    def execute(sql):
        raw = original(sql)
        if f'FROM arte.{module.TABLES[0].name} ' in sql:
            return raw.replace('{', '{"revision":0,', 1)
        return raw
    case.x.client.execute = execute
    with pytest.raises(ValueError, match='duplicate scalar columns'):
        read(case)
