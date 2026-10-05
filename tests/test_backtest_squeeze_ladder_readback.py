from dataclasses import replace
from hashlib import sha256
from uuid import uuid4

import pytest

from src.backend.backtest_squeeze_ladder_admission import admit_ladder_proposal
from src.backend.backtest_squeeze_ladder_evidence import project_ladder_evidence
from src.backend.backtest_squeeze_ladder_readback import (
    reconstruct_ladder_evidence, reconstruct_ladder_market_decision,
)
from src.trading_runtime.journal_contract import canonical_json
from tests.test_backtest_squeeze_ladder_admission import financial, DAY
from tests.test_backtest_squeeze_ladder_entry import prepared, decide
from tests.test_backtest_squeeze_ladder_setup import plans


def test_source_reconstruction_matches_intent_and_rehashed_target_is_rejected():
    observations, setup, v7 = prepared()
    _, market, _, pivots = plans()
    admission = admit_ladder_proposal(decide(observations, setup, v7), financial(), session_date=DAY, groups=())
    context = dict(run_id='source-readback', batch_id=str(uuid4()), parent_record_id=str(uuid4()))
    rows = project_ladder_evidence(admission, assignment_id='assignment-1', session_date=DAY, **context)
    sources = dict(observations=observations, market=market, v7=v7, pivots=pivots,
                   financial=financial(), groups=(), tick_int=100, stop_buffer_ticks=1,
                   break_buffer_ticks=1, target_count=3, allocation='equal', **context)
    assert reconstruct_ladder_evidence(rows, **sources) == admission.intent
    market_sources = {key: value for key, value in sources.items()
                      if key not in {'financial', 'groups', 'run_id', 'batch_id', 'parent_record_id'}}
    assert reconstruct_ladder_market_decision(rows, **market_sources) == admission.market_decision
    # A post-entry account must not invalidate historical market geometry, nor
    # may market geometry alone authorize a second purchase in that account.
    held = replace(financial(), position_quantity=100.)
    reconstructed = reconstruct_ladder_market_decision(rows, **market_sources)
    denied = admit_ladder_proposal(reconstructed, held, session_date=DAY, groups=())
    assert denied.reason == 'position_requires_management'
    assert denied.intent is None
    altered = dict(rows.targets[0], level_id='false-target')
    altered['content_hash'] = sha256(canonical_json({key:value for key,value in altered.items()
                                                    if key != 'content_hash'}).encode()).hexdigest()
    with pytest.raises(ValueError, match='reconstructed'):
        reconstruct_ladder_evidence(replace(rows, targets=(altered,*rows.targets[1:])), **sources)
