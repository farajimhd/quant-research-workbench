"""Prepared independent market-source reconstruction for ladder evidence."""
from dataclasses import replace

import numpy as np

from src.backend.backtest_squeeze_ladder_admission import (
    LadderAdmissionDecision, admit_ladder_proposal, build_ladder_proposal_intent,
)
from src.backend.backtest_squeeze_ladder_entry import propose_ladder_breakout
from src.backend.backtest_squeeze_ladder_evidence import project_ladder_evidence, verify_ladder_evidence_projection
from src.backend.backtest_squeeze_ladder_setup import bind_ladder_setups


def reconstruct_ladder_market_decision(rows, *, observations, market, v7, pivots,
                                      tick_int, stop_buffer_ticks, break_buffer_ticks,
                                      target_count, allocation):
    """Reconstruct market facts without asserting financial authorization.

    Callers supply independently certified plans and frozen release policy.
    Only saved causal clocks select source rows. The returned proposal cannot
    reserve cash, authorize orders or establish historical entry permissions.
    A cold reader must compare the complete evidence and parent intent, then
    separately verify the committed Portfolio/OMS decisions and lineage.
    """
    boundary = rows.setup.get('boundary_ms')
    qualification = rows.setup.get('qualification_boundary_ms')
    if (type(boundary) is not int or type(qualification) is not int
            or not 0 < qualification < boundary <= 57_600_000
            or boundary % 100 or qualification % 100):
        raise ValueError('Ladder readback requires exact completed causal clocks')
    gate = observations.gate
    count = int(np.searchsorted(gate.boundary_ms, boundary, side='right'))
    # Future rows must neither enter setup reconstruction nor cause an earlier
    # proof to fail through future setup geometry/availability errors.
    prefix_gate = replace(gate, boundary_ms=gate.boundary_ms[:count],
        admission_boundary_ms=gate.admission_boundary_ms[:count],
        market_rejection=gate.market_rejection[:count],
        market_indices=gate.market_indices[gate.market_indices < count],
        vwap_cross_indices=gate.vwap_cross_indices[gate.vwap_cross_indices < count],
        certified_history_through_ms=(min(boundary, gate.certified_history_through_ms)
                                     if gate.certified_history_through_ms is not None else None))
    prefix = replace(observations, completed_source=observations.completed_source.slice(0, count), gate=prefix_gate)
    setups = bind_ladder_setups(prefix, market=market, v7=v7, pivots=pivots,
                               tick_int=tick_int, stop_buffer_ticks=stop_buffer_ticks)
    matching = [setup for setup in setups if setup.qualification_boundary_ms == qualification]
    if len(matching) != 1:
        raise ValueError('Saved ladder qualification is absent from certified source')
    return propose_ladder_breakout(prefix, matching[0], v7=v7, boundary_ms=boundary,
        tick_int=tick_int, break_buffer_ticks=break_buffer_ticks, target_count=target_count, allocation=allocation)


def reconstruct_ladder_evidence(rows, *, observations, market, v7, pivots, financial,
                                groups, run_id, batch_id, parent_record_id,
                                tick_int, stop_buffer_ticks, break_buffer_ticks,
                                target_count, allocation):
    """Recheck admission using the verified historical pre-entry state only.

    This writer-side check must never receive current post-entry financial
    state as a substitute for that historical prefix. Cold market reconstruction
    is separate and grants no financial authority.
    """
    decision = reconstruct_ladder_market_decision(rows, observations=observations,
        market=market, v7=v7, pivots=pivots, tick_int=tick_int,
        stop_buffer_ticks=stop_buffer_ticks, break_buffer_ticks=break_buffer_ticks,
        target_count=target_count, allocation=allocation)
    from datetime import date
    admission = admit_ladder_proposal(decision, financial, session_date=date.fromisoformat(market.sessions[0]), groups=groups)
    expected = project_ladder_evidence(admission, run_id=run_id, batch_id=batch_id,
        parent_record_id=parent_record_id, assignment_id=financial.assignment_id,
        session_date=date.fromisoformat(market.sessions[0]))
    verify_ladder_evidence_projection(rows, expected=expected)
    return admission.intent


def verify_ladder_market_evidence(rows, *, account_id, assignment_id, run_id,
                                  batch_id, parent_record_id, **certified_market_context):
    """Verify saved geometry and recover the unapproved original proposal.

    Parent identity and release policy must come from independently verified
    run context. No current or invented historical financial view is accepted.
    This check alone cannot admit a journal batch, approve cash or submit orders.
    Committed Portfolio/OMS financial decisions require separate verification.
    """
    from datetime import date
    decision = reconstruct_ladder_market_decision(rows, **certified_market_context)
    session_date = date.fromisoformat(certified_market_context['market'].sessions[0])
    intent = build_ladder_proposal_intent(decision, session_date=session_date,
        account_id=account_id, assignment_id=assignment_id)
    # This wrapper supplies the projection's proposal shape, not evidence that
    # a financial gate has run. Only the source comparison is authorized here.
    proposal = LadderAdmissionDecision('capital_request_proposed', decision, intent)
    expected = project_ladder_evidence(proposal, run_id=run_id, batch_id=batch_id,
        parent_record_id=parent_record_id, assignment_id=assignment_id, session_date=session_date)
    verify_ladder_evidence_projection(rows, expected=expected)
    return intent
