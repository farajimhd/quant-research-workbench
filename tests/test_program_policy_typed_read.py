"""Policy's consumed exact-price contract across real mixed V4 typed pages."""
import asyncio

import pytest

from src.backend import program_policy_recovery as policy
from tests.test_arte_journal_reader import mixed_v4_price_client


@pytest.mark.parametrize('legacy_consumed', [None, 'trading_execution_v1', 'trading_strategy_signal_v2'])
@pytest.mark.parametrize('exact', [False, True])
def test_owned_policy_read_requires_exact_consumed_families_only(monkeypatch, legacy_consumed, exact):
    client, prefix = mixed_v4_price_client(legacy_consumed=legacy_consumed)
    execute = client.execute
    client.execute = lambda sql: '1' if sql == "SELECT getSetting('readonly')" else execute(sql)
    closed, prefixes, calls = [], [], []
    client.close = lambda: closed.append(True)
    def head(*args):
        prefixes.append(True)
        return prefix
    monkeypatch.setattr(policy, 'load_verified_v4_prefix', head)
    load_page = policy.load_typed_event_page
    def page(*args, **kwargs):
        calls.append(kwargs)
        return load_page(*args, **kwargs)
    monkeypatch.setattr(policy, 'load_typed_event_page', page)
    # Exercise the actual owned client, typed reader and UTC restoration. The
    # replay state machine has separate full financial recovery tests.
    def replay(template, events, *, run_id, last_sequence):
        assert run_id == prefix.run_id and last_sequence == prefix.last_sequence
        return tuple(events)
    monkeypatch.setattr(policy, 'rebuild_program_policy_events', replay)
    operation = policy.recover_program_policy_async(lambda: client, prefix, object(), exact_prices=exact)
    if exact and legacy_consumed:
        with pytest.raises(RuntimeError, match='committed mode'):
            asyncio.run(operation)
        assert len(prefixes) == 1
    else:
        events = asyncio.run(operation)
        assert [event.event['sequence'] for event in events] == [1, 2, 3, 4]
        assert events[0].detail['source_event_time'].tzinfo is not None
        assert all(event.event['event_time'].tzinfo is not None for event in events)
        assert len(prefixes) == 2
    assert closed == [True] and len(calls) == 1
    if exact:
        assert calls[0]['exact_prices'] is True
        assert calls[0]['exact_price_families'] == ('trading_execution_v1', 'trading_strategy_signal_v2')
    else:
        assert 'exact_prices' not in calls[0] and 'exact_price_families' not in calls[0]
