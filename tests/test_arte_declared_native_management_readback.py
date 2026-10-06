"""Complete command recovery from scalar SQL responses, with original replay."""
import json
from dataclasses import replace

import pytest

from test_arte_declared_native_management_v4 import packet, case
from src.trading_runtime import arte_declared_native_management_readback as module


def install(p, mutate=None, readonly='1'):
    client = p.args['resolver'].client
    original = client.execute
    calls = []
    def execute(sql):
        if sql == "SELECT getSetting('readonly')":
            return readonly
        for name, rows in p.packet.families:
            if f'FROM arte.{name} ' in sql:
                calls.append(sql)
                wire = [dict(row) for row in rows]
                if mutate:
                    mutate(name, wire)
                return '\n'.join(json.dumps(row) for row in wire)
        return original(sql)
    client.execute = execute
    return calls


def read(p, bases=None):
    return module.read_declared_management_rows(p.packet.bases if bases is None else bases, **p.args)


def test_complete_readback_replays_original_command(packet):
    calls = install(packet)
    assert read(packet) == packet.packet
    assert len(calls) == 9
    assert all('LIMIT 65537' in sql and 'batch_id=toUUID(' in sql for sql in calls)
    assert all('WHERE parent_record_id=' not in sql for sql in calls)
    assert all("max_result_bytes=16777216" in sql and "result_overflow_mode='throw'" in sql for sql in calls)


@pytest.mark.parametrize('failure', ['missing', 'extra', 'foreign', 'alias', 'column', 'hash'])
def test_recovery_rejects_incomplete_or_changed_graph(packet, failure):
    def mutate(name, rows):
        if name != module.TABLES[0].name:
            return
        if failure == 'missing': rows.clear()
        elif failure == 'extra': rows.append(rows[0])
        elif failure == 'foreign': rows[0]['parent_record_id'] = '11111111-1111-4111-8111-111111111111'
        elif failure == 'alias': rows[0]['revision'] = True
        elif failure == 'column': rows[0]['metadata'] = '{}'
        else: rows[0]['content_hash'] = 'f'*64
    install(packet, mutate)
    with pytest.raises(ValueError):
        read(packet)


def test_mutable_principal_and_foreign_batch_reject_before_family_queries(packet):
    calls = install(packet, readonly='0')
    with pytest.raises(ValueError, match='SELECT-only'):
        read(packet)
    assert calls == []
    bases = (replace(packet.packet.bases[0], run_id='11111111-1111-4111-8111-111111111111'),)
    with pytest.raises(ValueError, match='crossed run'):
        read(packet, bases)
    assert calls == []


def test_duplicate_wire_columns_rejected(packet):
    install(packet)
    client = packet.args['resolver'].client
    original = client.execute
    def execute(sql):
        raw = original(sql)
        if f'FROM arte.{module.TABLES[0].name} ' in sql:
            return raw.replace('{', '{"revision":0,', 1)
        return raw
    client.execute = execute
    with pytest.raises(ValueError, match='duplicate scalar columns'):
        read(packet)


@pytest.mark.parametrize('failure', ['wire_type', 'wire_size', 'row_count'])
def test_database_response_bounds_are_enforced_before_decode(packet, failure):
    install(packet)
    client = packet.args['resolver'].client
    original = client.execute
    def execute(sql):
        if f'FROM arte.{module.TABLES[0].name} ' in sql:
            if failure == 'wire_type': return b'{}'
            if failure == 'wire_size': return ' ' * (16 * 1024 * 1024 + 1)
            return '{}\n' * 65537
        return original(sql)
    client.execute = execute
    with pytest.raises(ValueError, match='output|bounded|bound'):
        read(packet)
