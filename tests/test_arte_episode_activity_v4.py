from uuid import NAMESPACE_URL, uuid5

import pytest

from test_arte_entry_activity_v4 import plan, graph, PARENT, BATCH
from src.backend.backtest_strategy_episode_activity_gate import compile_episode_activity_static_gate
from src.backend.backtest_strategy_episode_activity_source import EpisodeActivityReadbackAuthority
from src.backend.backtest_strategy_entry_activity_source import EntryActivityReadbackAuthority
from src.trading_runtime.arte_entry_activity_v4 import (
    activity_event_instant, project_entry_activity, seal_certified_entry_activity_rows,
)


def graph37():
    native = plan()
    authority = EpisodeActivityReadbackAuthority('activity-run', compile_episode_activity_static_gate(native))
    witness = authority.witness('AAA', 41000)
    _, entry, intent = graph(native)
    entry['strategy_number'] = 37
    identity = f'strategy-37:{witness.session_date}:assignment:account:AAA:41000:30000'
    intent['intent_id'] = str(uuid5(NAMESPACE_URL, identity))
    event = dict(record_id=PARENT,run_id=entry['run_id'],batch_id=BATCH,
        event_month=entry['event_month'],category='strategy',entity_type='strategy_intent',
        entity_id=intent['intent_id'],account_id='account',
        event_time=activity_event_instant(witness).isoformat())
    row = project_entry_activity(witness,run_id='activity-run',batch_id=BATCH,
        parent_record_id=PARENT,event_month=entry['event_month'],strategy_number=37)
    return row, entry, intent, event, authority


def seal(row, entry, intent, event, authority):
    return seal_certified_entry_activity_rows((row,),(entry,),(intent,),(event,),
        run_id='activity-run',source=authority)


def test_normalized_episode_companion_is_reconstructed_from_independent_full_prefix():
    args = graph37()
    sealed = seal(*args)
    assert sealed[0]['strategy_number'] == 37
    assert sealed[0]['activity_source_token'] == args[4].gate.token
    assert [sealed[0][f'candle_{i}_trade_count'] for i in range(4)] == [100] * 4
    assert seal(sealed[0],*args[1:]) == sealed


def test_strategy36_source_cannot_authorize_strategy37_even_with_valid_candles():
    row,entry,intent,event,source = graph37()
    legacy = EntryActivityReadbackAuthority('activity-run',source.plan)
    with pytest.raises(ValueError,match='independent certified'):
        seal(row,entry,intent,event,legacy)
    row['activity_source_token'] = source.plan.token
    with pytest.raises(ValueError,match='certified source'):
        seal(row,entry,intent,event,source)


@pytest.mark.parametrize('change', [{'strategy_number':36},{'candle_0_trade_count':101},
                                   {'activity_source_token':'f'*64}])
def test_changed_episode_companions_cannot_authenticate_themselves(change):
    row,entry,intent,event,source = graph37()
    with pytest.raises(ValueError,match='certified source'):
        seal(dict(row,**change),entry,intent,event,source)


def test_current_candle_pass_cannot_bypass_independently_vetoed_prefix():
    row,entry,intent,event,source = graph37()
    faded = EpisodeActivityReadbackAuthority('activity-run',compile_episode_activity_static_gate(plan('fade')))
    with pytest.raises(ValueError,match='admitted causal prefix'):
        seal(row,entry,intent,event,faded)
