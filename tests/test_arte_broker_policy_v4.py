from datetime import datetime, UTC
from uuid import uuid4
from dataclasses import replace

import pytest

from src.trading_runtime.arte_broker_policy_v4 import broker_policy_batch_v4, POLICY_EVENT, MESSAGE
from src.trading_runtime.arte_journal_compound_v4 import coalesce_v4_units, publish_compound_v4
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
from src.trading_runtime.journal_contract import JournalRecord
from tests.test_arte_journal_commit_v4 import attached_v4_client
from tests.test_arte_journal_writer import RUN, ATTEMPT


def test_shared_policy_source_compound_receipt_and_cold_child_tamper():
    at = datetime(2026, 8, 18, 12, tzinfo=UTC)
    record = JournalRecord(str(uuid4()), RUN, 1, at, at, 'broker_policy',
        'order_warning_decision', 'actual-warning-reply-id', 'DU1',
        dict(strategy_id='generic-program', strategy_revision=62, policy_version=1,
            correlation_id=RUN, causation_id='intent', order_group_id='group',
            ticker='ABC', action='enter_long', intent_id='intent',
            message_ids=['o163'], confirmed=True, known=True))
    unit = broker_policy_batch_v4(record, run_month=at.date().replace(day=1), attempt_id=ATTEMPT,
        batch_id=str(uuid4()), prior_batch_id='00000000-0000-0000-0000-000000000000', source_cursor='actual-source')
    client = attached_v4_client()
    second = broker_policy_batch_v4(replace(record, record_id=str(uuid4()), sequence=2, entity_id='warning-2'),
        run_month=at.date().replace(day=1), attempt_id=ATTEMPT, batch_id=str(uuid4()),
        prior_batch_id=unit.base.batch_id, source_cursor='actual-source')
    compound = coalesce_v4_units((unit, second))
    assert publish_compound_v4(client, compound) == compound.base.batch_id
    assert load_verified_v4_prefix(client, RUN).last_sequence == 2
    assert client.tables[POLICY_EVENT.name][0]['strategy_id'] == 'generic-program'
    assert client.tables[MESSAGE.name][0]['message_id'] == 'o163'
    client.tables[MESSAGE.name][0]['message_id'] = 'different'
    with pytest.raises(RuntimeError):
        load_verified_v4_prefix(client, RUN)
