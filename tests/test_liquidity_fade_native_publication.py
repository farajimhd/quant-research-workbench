"""Real journal preparation/readback gates; no connected Strategy 35 run."""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.trading_runtime.arte_journal_writer import typed_row, _CONTRACTS, _datetime_wire
from src.trading_runtime.arte_journal_commit_v4 import (
    _publish_typed_batch_v4, _publish_sealed_batch_v4, prepare_commit_v4, load_verified_commit_v4,
)
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, prepare_compound_v4_families
from src.trading_runtime.arte_liquidity_fade_failure_v4 import LIQUIDITY_FADE_FAILURE
from src.trading_runtime.strategy_liquidity_fade_transport import V4LiquidityFadeFailureBatch
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from test_liquidity_fade_prepared_transport import transport
from tests.test_arte_journal_writer import batch, MemoryClient


def native_unit():
    row, base = transport()
    event = {key: value for key, value in batch().events[0].items() if key != 'content_hash'}
    event.update(base.events[0])
    event.update(event_month=row['event_month'], recorded_at=event['event_time'], attempt_id=base.attempt_id)
    return V4LiquidityFadeFailureBatch(replace(base, events=(event,),
        prior_batch_id='00000000-0000-0000-0000-000000000005'), row)


def test_real_float_family_insert_uses_binary_and_preserves_exact_hash():
    import struct
    from src.trading_runtime.arte_journal_writer import _insert, _canonical_typed_content
    from src.trading_runtime.journal_contract import canonical_json
    from hashlib import sha256
    row = dict(native_unit().failure)
    row['macd_line'] = 5.810210334455945e-05
    sealed = typed_row(LIQUIDITY_FADE_FAILURE.name, row)
    target = MemoryClient()
    request = _insert(target, LIQUIDITY_FADE_FAILURE.name, (sealed,), 'exact-float-test',
                      journal_profile='backtest_v4')
    assert isinstance(request, bytes)
    assert request.split(b'\n', 1)[0].endswith(b'FORMAT RowBinary')
    saved = target.tables[LIQUIDITY_FADE_FAILURE.name][0]
    assert struct.pack('<d', saved['macd_line']) == struct.pack('<d', row['macd_line'])
    content = {key: value for key, value in saved.items() if key != 'content_hash'}
    assert sha256(canonical_json(_canonical_typed_content(
        LIQUIDITY_FADE_FAILURE.name, content, stored_utc=True)).encode()).hexdigest() == saved['content_hash']


def test_compound_detail_route_preserves_exact_float_bits():
    import struct
    from src.trading_runtime.arte_journal_commit_v4 import _insert_detail_families_v4
    requests = []

    class CapturingClient(MemoryClient):
        def execute(self, sql, *args, **kwargs):
            requests.append(sql)
            return super().execute(sql, *args, **kwargs)

    row = dict(native_unit().failure)
    row['macd_line'] = 5.810210334455945e-05
    sealed = typed_row(LIQUIDITY_FADE_FAILURE.name, row)
    target = CapturingClient()
    _insert_detail_families_v4(target, batch(),
        ((LIQUIDITY_FADE_FAILURE.name, (sealed,)),), journal_profile='backtest_v4')
    assert isinstance(requests[0], bytes)
    assert requests[0].split(b'\n', 1)[0].endswith(b'FORMAT RowBinary')
    saved = target.tables[LIQUIDITY_FADE_FAILURE.name][0]
    assert struct.pack('<d', saved['macd_line']) == struct.pack('<d', row['macd_line'])
    assert typed_row(LIQUIDITY_FADE_FAILURE.name,
        {key: value for key, value in saved.items() if key != 'content_hash'})['content_hash'] == sealed['content_hash']


def client():
    def forbidden(*args, **kwargs):
        raise AssertionError('Preparation/gate rejection must not execute SQL')
    return SimpleNamespace(typed_insert_strict=True,
        typed_insert_dispatch=TypedInsertDispatch(object()), execute=forbidden)


def micro(unit, target):
    return _publish_typed_batch_v4(target, unit.base,
        liquidity_fade_rows=(unit.failure,), _prepare_only=True)


def test_native_micro_preparation_retains_complete_scalar_family_without_sql():
    unit = native_unit()
    base, families = micro(unit, client())
    assert dict(families)[LIQUIDITY_FADE_FAILURE.name] == (typed_row(LIQUIDITY_FADE_FAILURE.name, unit.failure),)
    from src.trading_runtime.strategy_liquidity_fade_exit import REASON
    assert dict(base)['trading_strategy_intent_v1'][0]['reason'] == REASON


def test_direct_publication_without_certified_market_authority_rejects_before_sql():
    unit = native_unit()
    with pytest.raises(ValueError, match='certified run market authority'):
        _publish_typed_batch_v4(client(), unit.base, liquidity_fade_rows=(unit.failure,))


def test_live_cannot_prepare_backtest_liquidity_family():
    unit, target = native_unit(), client()
    target.live_v4_lease = SimpleNamespace(run_id=unit.base.run_id, assert_current=lambda: None)
    with pytest.raises(ValueError, match='Backtest-only'):
        micro(unit, target)


def test_compound_rekeys_liquidity_family_but_requires_native_authority_before_publish():
    unit = native_unit()
    next_id = '00000000-0000-0000-0000-000000000003'
    event = dict(unit.base.events[0], batch_id=next_id, sequence=66,
                 record_id='00000000-0000-0000-0000-000000000004', category='run_state', entity_type='lifecycle')
    suffix = replace(unit.base, batch_id=next_id, prior_batch_id=unit.base.batch_id,
        first_sequence=66, last_sequence=66, events=(event,), intents=())
    compound = coalesce_v4_units((unit, suffix))
    assert compound.children['liquidity_fade_failures'][0]['batch_id'] == next_id
    assert unit.failure['batch_id'] != next_id
    with pytest.raises(ValueError, match='certified run market authority'):
        prepare_compound_v4_families(client(), compound)


def test_bare_sealed_publication_cannot_bypass_verified_predecessor():
    unit, target = native_unit(), client()
    base, families = micro(unit, target)
    with pytest.raises(ValueError, match='exact verified Backtest predecessor'):
        _publish_sealed_batch_v4(target, unit.base, base, families)


@pytest.mark.parametrize('tamper', [False, True])
def test_cold_readback_checks_hash_before_native_source_authority(tamper):
    unit = native_unit()
    _, families = micro(unit, client())
    commit, family_rows = prepare_commit_v4(run_id=unit.base.run_id, run_month=unit.base.run_month,
        attempt_id=unit.base.attempt_id, batch_id=unit.base.batch_id, prior_batch_id=unit.base.prior_batch_id,
        first_sequence=65, last_sequence=65, source_cursor=unit.base.source_cursor, status='running',
        sealed_families=families, committed_at=datetime.now(timezone.utc))
    reader = MemoryClient()
    reader.tables = {name: [dict(row) for row in rows] for name, rows in families}
    for name, rows in reader.tables.items():
        for field, kind in _CONTRACTS[name].columns:
            if kind.startswith('DateTime64('):
                precision = int(kind.split('(')[1].split(',')[0])
                for row in rows:
                    row[field] = _datetime_wire(row[field], precision)
    reader.tables.update(trading_commit_v4=[commit], trading_commit_family_v4=list(family_rows))
    if tamper:
        reader.tables[LIQUIDITY_FADE_FAILURE.name][0]['trade_count_0'] += 1
        error, message = RuntimeError, 'row hash'
    else:
        error, message = ValueError, 'certified run market authority'
    with pytest.raises(error, match=message):
        load_verified_commit_v4(reader, run_id=unit.base.run_id, batch_id=unit.base.batch_id)


@pytest.mark.parametrize('profile', ['live_v4', 'v1', 'backtest_v3'])
def test_writer_submission_rejects_foreign_profiles_without_sql(profile):
    from src.trading_runtime.arte_journal_writer import ArteJournalWriter
    journal = object.__new__(ArteJournalWriter)
    journal._journal_profile, journal._run_id = profile, 'run'
    with pytest.raises(ValueError, match='Backtest writer'):
        journal.submit_liquidity_fade_exit_v4(native_unit())


def test_real_writer_receipt_fails_on_worker_before_any_liquidity_insert(monkeypatch):
    from threading import get_ident
    from src.trading_runtime import arte_journal_writer as writer
    from src.trading_runtime.arte_journal_commit_v4 import publish_base_typed_batch_v4
    from tests.test_arte_journal_commit_v4 import attached_v4_client
    unit, seed = native_unit(), batch()
    events = tuple(typed_row('trading_event_v1', {
        **{key: value for key, value in seed.events[0].items() if key != 'content_hash'},
        'run_id': unit.base.run_id, 'batch_id': unit.base.prior_batch_id,
        'attempt_id': unit.base.attempt_id,
        'record_id': f'00000000-0000-0000-0000-{100+i:012d}', 'sequence': i,
    }) for i in range(1, 65))
    preceding = replace(seed, run_id=unit.base.run_id, attempt_id=unit.base.attempt_id,
        batch_id=unit.base.prior_batch_id, last_sequence=64, events=events)
    target = attached_v4_client()
    publish_base_typed_batch_v4(target, preceding)
    before = tuple(target.inserts)
    monkeypatch.setattr(writer, 'storage_preflight', lambda *args, **kwargs: None)
    monkeypatch.setattr(writer, 'journal_permission_preflight', lambda *args, **kwargs: None)
    monkeypatch.setattr(writer, '_verify_run_identity', lambda *args: {'mode': 'backtest', 'account_ids': ('account',)})
    journal = writer.ArteJournalWriter(target, run_id='run', journal_profile='backtest_v4', coalesce_batches=False)
    caller, threads, execute = get_ident(), [], target.execute
    def tracked(sql):
        threads.append(get_ident())
        return execute(sql)
    target.execute = tracked
    try:
        with pytest.raises(ValueError, match='certified run market authority'):
            journal.submit_liquidity_fade_exit_v4(unit).result(timeout=5)
        assert tuple(target.inserts) == before
        assert threads and all(thread != caller for thread in threads)
    finally:
        with pytest.raises(RuntimeError, match='did not drain durably'):
            journal.close()
