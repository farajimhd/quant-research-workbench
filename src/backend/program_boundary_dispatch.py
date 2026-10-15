"""Completed certified QMD boundaries through the native program runtime.

This adapter calculates no market inputs and creates no execution facts. Mode
admission belongs to the verified runtime assembly, never a caller capability
flag. Async broker reconciliation and submissions retain native backpressure.
"""
from __future__ import annotations

import asyncio


def _order_observation(runtime, actor, *, assignment_id, at):
    from src.trading_runtime.oms_assignment_observation import observe_assignment_orders
    assignment = actor._by_assignment.get(assignment_id)
    if assignment is None or runtime.last_event_time != at or runtime.strategy is not actor:
        raise ValueError("Program OMS observation requires its completed native assignment")
    return observe_assignment_orders(runtime.order_manager,
        account_id=assignment.account_id, assignment_id=assignment_id,
        ticker=assignment.ticker, at=at)


async def dispatch_program_boundary(owner, block, population, *, ordinal: int,
        assignment_id: str, execution_policy, protection_profile, outside_rth: bool,
        terminal: bool = False):
    """Serialize one native clock mutation; reject overlapping dispatch work.

    A caller must await its completed boundary. No unbounded lock queue or
    background command task is created. Other runtimes remain independent.
    """
    lock = getattr(owner, "_program_boundary_dispatch_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        owner._program_boundary_dispatch_lock = lock
    if type(lock) is not asyncio.Lock or lock.locked():
        raise RuntimeError("Program boundary dispatch is already active")
    async with lock:
        return await _dispatch_program_boundary(owner, block, population,
            ordinal=ordinal, assignment_id=assignment_id, execution_policy=execution_policy,
            protection_profile=protection_profile, outside_rth=outside_rth, terminal=terminal)


async def _dispatch_program_boundary(owner, block, population, *, ordinal: int,
        assignment_id: str, execution_policy, protection_profile, outside_rth: bool,
        terminal: bool = False):
    """Consume one committed source boundary through actual policy/Portfolio/OMS.

    No market feature is recalculated here. Eligibility comes from the
    certified scanner projection, masks from the prepared Polars block,
    and position/protection state from the completed filling-broker runtime.
    """
    from .backtest_program_inputs import PreparedProgramBlock
    from .qmd_columnar_population import CertifiedPopulationWindow
    from src.trading_runtime.signals import StrategySignal
    from src.trading_runtime.runtime import TradingRuntime
    from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
    from .signal_stream_runtime_service import SignalStreamRuntime
    runtime = owner._runtime
    if type(runtime) is not TradingRuntime:
        raise TypeError("Program dispatch requires the native trading runtime")
    TradingRuntime.require_program_dispatch_mode(runtime)
    actor = owner._program_strategy
    source = owner._program_signal_runtime
    if owner._program_dispatch_failure is not None:
        raise RuntimeError("Program dispatch requires recovery after an uncertain boundary") from owner._program_dispatch_failure
    if (type(actor) is not AssignedProgramStrategy or type(source) is not SignalStreamRuntime
            or runtime.strategy is not actor or owner.run_id != runtime.run_id
            or owner._journal is None or owner._runtime.journal is not owner._journal
            or type(block) is not PreparedProgramBlock or type(population) is not CertifiedPopulationWindow
            or block.release_hash != actor.program_release.header["content_hash"]
            or source._typed_scope is None
            or population.source_revision != source._typed_scope[1]
            or type(ordinal) is not int or not block.first_ordinal <= ordinal < block.stop_ordinal):
        raise ValueError("Program dispatch lacks its bound prepared/source contracts")
    assignment = actor._by_assignment.get(assignment_id)
    if assignment is None or assignment.account_id not in runtime.config.account_ids:
        raise ValueError("Program dispatch has an unknown native assignment")
    listing = actor._listings[assignment_id]
    row, mask = block.row_for(listing, ordinal)
    at = row["boundary_at"]
    sequence, source_hash, cutoff = source.typed_source_boundary(at)
    if (sequence < 1 or cutoff != at or owner._runtime.last_event_time != at
            or mask["boundary_at"] != at or row["evaluation_block"] != block.evaluation_block):
        raise ValueError("Program dispatch differs from its committed source/broker clock")
    boundary = block.boundary_for(actor.program_release, population, listing_id=listing,
        ticker=assignment.ticker, ordinal=ordinal)
    try:
        observation = _order_observation(runtime, actor, assignment_id=assignment_id, at=at)
        if observation.unobserved_order_ids:
            await owner._runtime.order_manager.reconcile()
            observation = _order_observation(runtime, actor, assignment_id=assignment_id, at=at)
        results = []
        basis_sequence = owner._journal.latest_sequence(owner.run_id)
        if (runtime.last_event_time != at or source.typed_source_boundary(at) != (sequence, source_hash, cutoff)
                or runtime.strategy is not actor or runtime.journal is not owner._journal
                or owner._runtime is not runtime or owner._program_strategy is not actor
                or owner._program_signal_runtime is not source):
            raise RuntimeError("Program dispatch ownership or completed clock changed during reconciliation")
        TradingRuntime.require_program_dispatch_mode(runtime)
        selection = actor.select_from_orders(boundary, assignment_id=assignment_id,
            observation=observation, terminal=terminal)
        if selection.status == "already_consumed":
            return {"status": selection.status, "results": results}
        intent_references = tuple(dict(stage=proposal.stage, strategy_intent_id=proposal.intent_id)
            for proposal in selection.proposals)
        for proposal in selection.proposals:
            results.extend(await owner._runtime.submit_program_proposal(proposal,
                account_id=assignment.account_id, assignment_id=assignment_id,
                execution_policy=execution_policy,
                protection_profile=protection_profile if proposal.action in {"enter_long", "add_long"} else None,
                outside_rth=outside_rth))
        identity = block.completion_identity(run_id=owner.run_id, assignment_id=assignment_id,
            source_hash=source_hash, listing_id=listing, ordinal=ordinal, terminal=terminal)
        reason = "program:" + selection.status
        metadata = dict(assignment_id=assignment_id, reference_price=row["reference_close"],
            status="completed", reason_code=reason, reason_detail=selection.status,
            correlation_id=identity, causation_id=source_hash)
        signal = StrategySignal(identity, "program_boundary_completed", assignment.ticker,
            at, "wait", "neutral", 0., 1., reason, working_timeframe="1s", metadata=metadata)
        completion = owner._journal.append(run_id=owner.run_id, category="strategy_decision",
            entity_type="signal", entity_id=identity, account_id=assignment.account_id,
            event_time=at, payload={**signal.payload(), "strategy_id": actor.strategy_id,
                "strategy_revision": actor.revision, "correlation_id": identity,
                "causation_id": source_hash})
        from src.trading_runtime.rule_decision_basis_projection import project_program_completion_basis
        parent, children = project_program_completion_basis(completion,
            release=actor.program_release, block=block, assignment=assignment,
            listing_id=listing, ordinal=ordinal, source_boundary=(sequence, source_hash, cutoff),
            basis_sequence=basis_sequence, intents=intent_references, terminal=terminal,
            batch_id=completion.record_id)
        # Provisional microbatch identity; the fenced publisher must rekey
        # these scalar rows together with their original completion event.
        decision_basis = owner._journal.retain_rule_decision_basis(completion, parent, children)
    except BaseException as exc:
        owner._program_dispatch_failure = exc
        raise
    return {"status": selection.status, "results": results, "decision_basis": decision_basis}

