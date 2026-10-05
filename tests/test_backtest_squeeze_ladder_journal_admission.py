from dataclasses import replace
from uuid import uuid4

import pytest

from src.backend.backtest_squeeze_ladder_admission import admit_ladder_proposal
from src.backend.backtest_squeeze_ladder_evidence import project_ladder_evidence
from src.backend.backtest_squeeze_ladder_journal_admission import prepare_ladder_journal_families
from src.trading_runtime.arte_intent_projection import strategy_intent_batch
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix
from tests.test_backtest_squeeze_ladder_admission import financial, DAY
from tests.test_backtest_squeeze_ladder_entry import prepared, decide
from tests.test_backtest_squeeze_ladder_setup import plans


def test_native_intent_parent_binds_exact_capital_request_and_three_evidence_lots():
    observations, setup, v7 = prepared()
    _, market, _, pivots = plans()
    admission = admit_ladder_proposal(decide(observations, setup, v7), financial(), session_date=DAY, groups=())
    intent = admission.intent
    prior = str(uuid4())
    batch = strategy_intent_batch(intent, run_id='journal-admission', run_month=DAY.replace(day=1),
        account_id='DU1', attempt_id=str(uuid4()), batch_id=str(uuid4()),
        prior_batch_id=prior, sequence=2,
        source_cursor='ladder', run_status='running', recorded_at=intent.event_time)
    rows = project_ladder_evidence(admission, run_id=batch.run_id, batch_id=batch.batch_id,
        parent_record_id=batch.events[0]['record_id'], assignment_id='assignment-1', session_date=DAY)
    context = dict(observations=observations, market=market, v7=v7, pivots=pivots,
        financial=financial(), groups=(), tick_int=100, stop_buffer_ticks=1,
        break_buffer_ticks=1, target_count=3, allocation='equal')
    context['verified_prior_prefix'] = V4CommittedPrefix(batch.run_id, 1, prior, 'before-entry', 'running', (prior,))
    families = prepare_ladder_journal_families(batch, rows, **context)
    assert [len(family[1]) for family in families] == [1,3]
    # Every other native family is outside this proposal's authority. Reject
    # it before source reconstruction, even when the proposal itself is valid.
    from src.trading_runtime.arte_journal_writer import _FAMILIES
    for _, attribute, _, _ in _FAMILIES:
        if attribute in {'events', 'intents', 'intent_slices'}:
            continue
        with pytest.raises(ValueError, match='unrelated mutation families'):
            prepare_ladder_journal_families(
                replace(batch, **{attribute: ({'record_id': str(uuid4())},)}), rows, **context)
    with pytest.raises(ValueError, match='unrelated mutation families'):
        prepare_ladder_journal_families(
            replace(batch, v4_command_lineages=({'record_id': str(uuid4())},)), rows, **context)
    # Stored evidence cannot authorize another capital request or altered slice.
    changed = dict(batch.intents[0], reference_price='11.000000000000000000')
    with pytest.raises(ValueError, match='parent intent'):
        prepare_ladder_journal_families(replace(batch, intents=(changed,)), rows, **context)
    changed_slice = dict(batch.intent_slices[0], slice_id='foreign-lot')
    with pytest.raises(ValueError, match='parent intent'):
        prepare_ladder_journal_families(replace(batch, intent_slices=(changed_slice,*batch.intent_slices[1:])), rows, **context)
    for prefix in (None, replace(context['verified_prior_prefix'], run_id='another-run'),
                   replace(context['verified_prior_prefix'], status='completed'),
                   replace(context['verified_prior_prefix'], last_sequence=0),
                   replace(context['verified_prior_prefix'], last_batch_id=str(uuid4()))):
        with pytest.raises(ValueError, match='exact verified prior'):
            prepare_ladder_journal_families(batch, rows, **{**context, 'verified_prior_prefix':prefix})
