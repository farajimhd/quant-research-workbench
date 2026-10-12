"""Complete prepared-clock dispatch on the existing controller and broker."""
from datetime import datetime
from collections.abc import Mapping


async def dispatch_prepared_program_clock(controller, blocks, population, *, at,
        assignment_order, execution_policy, protection_profiles, outside_rth, terminal=False):
    """Validate full coverage before dispatching the caller's explicit order.

    The native compute funnel supplies the order; this adapter invents no
    capital priority, broker clock, market data, fills or evaluation blocks.
    """
    from .replay_run_service import ReplayRunController
    from .backtest_program_inputs import PreparedProgramBlock
    from .qmd_columnar_population import CertifiedPopulationWindow
    from .signal_stream_runtime_service import SignalStreamRuntime
    from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
    if (type(controller) is not ReplayRunController or type(population) is not CertifiedPopulationWindow
            or not isinstance(at, datetime) or at.tzinfo is None or at.utcoffset() is None
            or type(outside_rth) is not bool or type(terminal) is not bool
            or not isinstance(blocks, Mapping) or not isinstance(protection_profiles, Mapping)
            or isinstance(assignment_order, (str, bytes))):
        raise ValueError("Program clock needs its native typed scope")
    actor, runtime, source = controller._program_strategy, controller._runtime, controller._program_signal_runtime
    order = tuple(assignment_order)
    if (type(actor) is not AssignedProgramStrategy or runtime is None or runtime.strategy is not actor
            or type(source) is not SignalStreamRuntime or source._typed_scope is None
            or population.source_revision != source._typed_scope[1]
            or not order or len(order) != len(set(order))
            or set(order) != set(actor._by_assignment)
            or set(blocks) != set(order) or set(protection_profiles) != set(order)
            or runtime.last_event_time != at):
        raise ValueError("Program clock lacks complete assignment/source/broker coverage")
    boundary = source.typed_source_boundary(at)
    if boundary[0] < 1 or boundary[2] != at:
        raise ValueError("Program clock differs from its committed source")
    prepared = []
    for identity in order:
        block = blocks[identity]
        if type(block) is not PreparedProgramBlock or block.release_hash != actor.program_release.header["content_hash"]:
            raise ValueError("Program clock block differs from its immutable release")
        ordinal = block.ordinal_for(actor._listings[identity], at)
        assignment = actor._by_assignment[identity]
        block.boundary_for(actor.program_release, population, listing_id=actor._listings[identity],
            ticker=assignment.ticker, ordinal=ordinal)
        prepared.append((identity, block, ordinal, protection_profiles[identity]))
    results = []
    for identity, block, ordinal, protection in prepared:
        if (controller._program_strategy is not actor or controller._runtime is not runtime
                or controller._program_signal_runtime is not source or runtime.last_event_time != at
                or source.typed_source_boundary(at) != boundary):
            error = ValueError("Program clock ownership changed during dispatch")
            controller._program_dispatch_failure = error
            raise error
        result = await controller.dispatch_prepared_program_boundary(block, population,
            ordinal=ordinal, assignment_id=identity, execution_policy=execution_policy,
            protection_profile=protection, outside_rth=outside_rth, terminal=terminal)
        results.append((identity, result))
    if (controller._program_strategy is not actor or controller._runtime is not runtime
            or controller._program_signal_runtime is not source or runtime.last_event_time != at
            or source.typed_source_boundary(at) != boundary):
        error = ValueError("Program clock ownership changed during dispatch")
        controller._program_dispatch_failure = error
        raise error
    return tuple(results)
