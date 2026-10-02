from dataclasses import replace
from uuid import UUID, NAMESPACE_URL, uuid5

import pytest

from test_backtest_strategy_entry_activity_source import source_authority, ActivityBars
from src.backend.backtest_strategy_entry_activity_source import (
    load_entry_activity_plan, EntryActivityReadbackAuthority,
)
from src.trading_runtime.strategy_entry_activity_fade import EntryActivityCandle
from src.trading_runtime.strategy_entry_activity_witness import validate_entry_activity_witness
from src.trading_runtime.arte_entry_activity_v4 import (
    ENTRY_ACTIVITY, EntryActivityAuthority, activity_event_instant,
    project_entry_activity, restore_entry_activity, seal_entry_activity_rows,
)

PARENT = str(UUID(int=36))
BATCH = str(UUID(int=37))


def plan(mode='normal'):
    market, parent = source_authority(ten_percent=True)
    return load_entry_activity_plan(market, parent, client=ActivityBars(mode))


def graph(source):
    witness = source.witness('AAA', 41000)
    episode = source.parent.selection_witness('AAA', 41000).initial.episode_start_ms
    month = activity_event_instant(witness).date().replace(day=1).isoformat()
    entry = dict(parent_record_id=PARENT, strategy_number=36, run_id='activity-run',
                 batch_id=BATCH, event_month=month, boundary_ms=41000,
                 episode_start_ms=episode, assignment_id='assignment')
    identity = (f'strategy-36:{witness.session_date}:assignment:account:AAA:41000:{episode}')
    intent = dict(record_id=PARENT, intent_id=str(uuid5(NAMESPACE_URL, identity)),
                  run_id='activity-run', batch_id=BATCH, event_month=month,
                  action='enter_long', reason='strategy_one_entry', ticker='AAA', account_id='account')
    return witness, entry, intent


def project(witness):
    month = activity_event_instant(witness).date().replace(day=1).isoformat()
    return project_entry_activity(witness, run_id='activity-run', batch_id=BATCH,
                                  parent_record_id=PARENT, event_month=month)


def restore(row, witness):
    return restore_entry_activity((row,), expected_witness=witness, run_id='activity-run',
                                  batch_id=BATCH, parent_record_id=PARENT, event_month=row['event_month'])


def test_native_plan_supplies_witness_and_deterministic_normalized_row():
    source = plan()
    witness, entry, intent = graph(source)
    assert validate_entry_activity_witness(witness) is witness
    assert [c.trade_count for c in witness.candles] == [100] * 4
    row = project(witness)
    assert row == project(witness)
    assert row['history_active'] == 1 and row['strategy_number'] == 36
    assert restore(row, witness) is witness
    authority = EntryActivityReadbackAuthority('activity-run', source)
    receipts = authority.resolve('activity-run', (entry,), (intent,))
    assert receipts == (EntryActivityAuthority(PARENT, witness, entry['episode_start_ms']),)
    assert "storage_policy = 'live_market_ssd'" in ENTRY_ACTIVITY.ddl()
    assert all('Nullable(' in dtype for name, dtype in ENTRY_ACTIVITY.columns if name.startswith('candle_'))


@pytest.mark.parametrize('field,value', [
    ('candle_0_trade_count', 101), ('candle_0_boundary_ms', 30000),
    ('activity_source_token', 'f' * 64), ('bars_attempt_id', str(UUID(int=99))),
    ('strategy_number', 35), ('history_active', True), ('ticker', 'OTHER'),
])
def test_changed_companion_cannot_self_authenticate(field, value):
    witness = plan().witness('AAA', 41000)
    row = project(witness)
    row[field] = value
    with pytest.raises(ValueError, match='certified source'):
        restore(row, witness)


def test_warmup_absence_is_nullable_and_real_zero_is_present():
    source = plan('zero')
    zero = source.witness('AAA', 41000)
    row = project(zero)
    assert row['history_active'] == 1
    assert [row[f'candle_{i}_trade_count'] for i in range(4)] == [0] * 4
    warmup = replace(zero, boundary_ms=100, candles=(None,) * 4)
    early = project(warmup)
    assert early['history_active'] == 0
    assert all(early[f'candle_{i}_{name}'] is None for i in range(4)
               for name in ('boundary_ms', 'trade_count'))
    with pytest.raises(ValueError):
        validate_entry_activity_witness(replace(warmup, boundary_ms=20000))
    with pytest.raises(ValueError):
        validate_entry_activity_witness(replace(warmup, candles=zero.candles))


def test_utc_month_rollover_uses_source_clock_not_session_month():
    witness = plan().witness('AAA', 41000)
    boundary = 57599900
    latest = boundary // 5000 * 5000
    witness = replace(witness, session_date='2026-11-30', boundary_ms=boundary,
                      candles=tuple(EntryActivityCandle(latest - offset, 100)
                                    for offset in (15000, 10000, 5000, 0)))
    assert project(witness)['event_month'] == '2026-12-01'
    with pytest.raises(ValueError, match='UTC source clock'):
        project_entry_activity(witness, run_id='activity-run', batch_id=BATCH,
                               parent_record_id=PARENT, event_month='2026-11-01')


def test_rejected_source_key_cannot_produce_accepted_witness():
    source = plan('fade')
    with pytest.raises(ValueError, match='outside admitted'):
        source.witness('AAA', 31000)
    with pytest.raises(ValueError, match='outside admitted'):
        source.witness('AAA', 42000)


def test_readback_rejects_wrong_run_duplicate_parent_and_relabelled_intent():
    source = plan()
    _, entry, intent = graph(source)
    authority = EntryActivityReadbackAuthority('activity-run', source)
    with pytest.raises(ValueError, match='certified run'):
        authority.resolve('different-run', (entry,), (intent,))
    with pytest.raises(ValueError, match='duplicate intent'):
        authority.resolve('activity-run', (entry,), (intent, intent))
    with pytest.raises(ValueError, match='unrelated entry'):
        authority.resolve('activity-run', (entry, entry), (intent,))
    with pytest.raises(ValueError, match='numbered intent identity'):
        authority.resolve('activity-run', (entry,), (dict(intent, intent_id=str(UUID(int=88))),))
    with pytest.raises(ValueError, match='native clock or episode'):
        authority.resolve('activity-run', (dict(entry, episode_start_ms=30001),), (intent,))


def test_readback_cannot_use_legacy_first_setup_policy():
    market, parent = source_authority()
    source = load_entry_activity_plan(market, parent, client=ActivityBars())
    with pytest.raises(ValueError, match='source policy'):
        EntryActivityReadbackAuthority('activity-run', source)


def test_missing_or_extra_companion_rejects():
    witness = plan().witness('AAA', 41000)
    row = project(witness)
    for rows in ((), (row, row)):
        with pytest.raises(ValueError, match='exactly one'):
            restore_entry_activity(rows, expected_witness=witness, run_id='activity-run',
                                    batch_id=BATCH, parent_record_id=PARENT, event_month=row['event_month'])


def sealing_graph(source=None):
    source = plan() if source is None else source
    witness, entry, intent = graph(source)
    receipt = EntryActivityReadbackAuthority('activity-run', source).resolve(
        'activity-run', (entry,), (intent,))
    event = dict(record_id=PARENT, run_id=entry['run_id'], batch_id=BATCH,
                 event_month=entry['event_month'], category='strategy', entity_type='strategy_intent',
                 entity_id=intent['intent_id'], account_id=intent['account_id'],
                 event_time=activity_event_instant(witness).isoformat())
    return (project(witness),), (entry,), (intent,), (event,), receipt


def test_complete_graph_content_hash_and_native_utc_wire():
    args = sealing_graph()
    sealed = seal_entry_activity_rows(*args)
    assert len(sealed) == 1 and len(sealed[0]['content_hash']) == 64
    assert seal_entry_activity_rows(sealed, *args[1:]) == sealed
    event = dict(args[3][0], event_time=activity_event_instant(args[4][0].witness).strftime(
        '%Y-%m-%d %H:%M:%S.%f') + '000')
    assert seal_entry_activity_rows(args[0], args[1], args[2], (event,), args[4]) == sealed
    with pytest.raises(ValueError, match='changed companions'):
        seal_entry_activity_rows((dict(sealed[0], content_hash='f' * 64),), *args[1:])


def test_graph_rejects_incomplete_population_and_cross_parent_scope():
    args = sealing_graph()
    with pytest.raises(ValueError, match='source authority population'):
        seal_entry_activity_rows(*args[:4], ())
    with pytest.raises(ValueError, match='exactly one'):
        seal_entry_activity_rows((), *args[1:])
    with pytest.raises(ValueError, match='duplicate companions'):
        seal_entry_activity_rows(args[0] * 2, *args[1:])
    with pytest.raises(ValueError, match='unrelated entry'):
        seal_entry_activity_rows(args[0], args[1], (dict(args[2][0], ticker='OTHER'),), args[3], args[4])
    with pytest.raises(ValueError, match='native event clock'):
        seal_entry_activity_rows(args[0], args[1], args[2],
                                 (dict(args[3][0], event_time='2026-08-18T08:00:41.000000001+00:00'),), args[4])


def test_encoding_and_installed_release_leave_unknown_number_closed():
    from src.trading_runtime.arte_journal_writer import _CONTRACTS
    from src.trading_runtime.strategy_registry import numbered_strategy
    assert _CONTRACTS[ENTRY_ACTIVITY.name] is ENTRY_ACTIVITY
    assert numbered_strategy(36).number == 36
    with pytest.raises(ValueError):
        numbered_strategy(39)


def test_sealer_requires_encoding_registration(monkeypatch):
    from src.trading_runtime.arte_journal_writer import _CONTRACTS
    monkeypatch.delitem(_CONTRACTS, ENTRY_ACTIVITY.name)
    with pytest.raises(ValueError, match='not registered'):
        seal_entry_activity_rows((), (), (), (), ())


def test_cold_source_resolution_requires_independent_plan_and_complete_companion():
    from src.trading_runtime.arte_entry_activity_v4 import seal_certified_entry_activity_rows
    prepared = plan()
    rows, entries, intents, events, _ = sealing_graph(prepared)
    source = EntryActivityReadbackAuthority('activity-run', prepared)
    sealed = seal_certified_entry_activity_rows(rows, entries, intents, events,
        run_id='activity-run', source=source)
    assert len(sealed) == 1
    for supplied in (None, source.plan, EntryActivityReadbackAuthority('other-run', plan())):
        with pytest.raises(ValueError, match='independent certified run source'):
            seal_certified_entry_activity_rows(rows, entries, intents, events,
                run_id='activity-run', source=supplied)
    with pytest.raises(ValueError, match='exactly one'):
        seal_certified_entry_activity_rows((), entries, intents, events,
            run_id='activity-run', source=source)
    assert seal_certified_entry_activity_rows((), (), (), (), run_id='old-run') == ()
    with pytest.raises(ValueError, match='no supported numbered parent'):
        seal_certified_entry_activity_rows(rows, (), (), (), run_id='old-run')
