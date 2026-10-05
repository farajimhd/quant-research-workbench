from dataclasses import replace
from uuid import uuid4

import pytest

from src.backend.backtest_squeeze_ladder_admission import admit_ladder_proposal
from src.backend.backtest_squeeze_ladder_evidence import project_ladder_evidence
from src.backend.backtest_squeeze_ladder_journal_admission import prepare_ladder_journal_families
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from tests.test_backtest_squeeze_ladder_admission import financial, DAY
from tests.test_backtest_squeeze_ladder_entry import prepared, decide
from tests.test_backtest_squeeze_ladder_setup import plans


def test_native_intent_parent_binds_exact_capital_request_and_three_evidence_lots():
    observations, setup, v7 = prepared()
    _, market, _, pivots = plans()
    admission = admit_ladder_proposal(decide(observations, setup, v7), financial(), session_date=DAY, groups=())
    intent = admission.intent
    batch = strategy_intent_batch(intent, run_id='journal-admission', run_month=DAY.replace(day=1),
        account_id='DU1', attempt_id=str(uuid4()), batch_id=str(uuid4()),
        prior_batch_id='00000000-0000-0000-0000-000000000000', sequence=1,
        source_cursor='ladder', run_status='running', recorded_at=intent.event_time)
    rows = project_ladder_evidence(admission, run_id=batch.run_id, batch_id=batch.batch_id,
        parent_record_id=batch.events[0]['record_id'], assignment_id='assignment-1', session_date=DAY)
    context = dict(observations=observations, market=market, v7=v7, pivots=pivots,
        financial=financial(), groups=(), tick_int=100, stop_buffer_ticks=1,
        break_buffer_ticks=1, target_count=3, allocation='equal')
    families = prepare_ladder_journal_families(batch, rows, **context)
    assert [len(family[1]) for family in families] == [1,3]
    # Stored evidence cannot authorize another capital request or altered slice.
    changed = dict(batch.intents[0], reference_price='11.000000000000000000')
    with pytest.raises(ValueError, match='parent intent'):
        prepare_ladder_journal_families(replace(batch, intents=(changed,)), rows, **context)
    changed_slice = dict(batch.intent_slices[0], slice_id='foreign-lot')
    with pytest.raises(ValueError, match='parent intent'):
        prepare_ladder_journal_families(replace(batch, intent_slices=(changed_slice,*batch.intent_slices[1:])), rows, **context)
