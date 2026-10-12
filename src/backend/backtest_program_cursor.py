"""Completed programme clock through the existing normalized V4 cursor family."""
from .backtest_market_data import market_day_boundary
from .backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.arte_journal_projection import backtest_cursor_record_fields


def append_completed_program_cursor(controller):
    """Record a completed liquidity-grid clock without a disk checkpoint."""
    from .backtest_program_session import require_program_session_owner
    require_program_session_owner(controller)
    runtime, journal, at = controller._runtime, controller._journal, controller.current_time
    if (not getattr(controller, "_program_cohort_active", False) or runtime is None
            or runtime.strategy is not controller._program_strategy
            or type(journal) is not BacktestMemoryJournal or runtime.journal is not journal
            or at is None or runtime.last_event_time != at or controller._program_dispatch_failure is not None):
        raise RuntimeError("Programme cursor requires its owned completed clock")
    day = controller.definition.session_date
    elapsed = at - market_day_boundary(day, 0)
    microseconds = (elapsed.days * 86400 + elapsed.seconds) * 1000000 + elapsed.microseconds
    resolution = controller._program_liquidity_resolution_ms
    if (microseconds % 1000 or type(resolution) is not int or resolution not in (100, 1000)
            or not 0 < microseconds // 1000 <= 57600000 or (microseconds // 1000) % resolution):
        raise ValueError("Programme cursor differs from its declared liquidity grid")
    boundary = microseconds // 1000
    # The sequence names the absolute completed grid index. It is independent
    # of ticker count, sparse liquidity and an in-memory processed-event counter.
    cursor = dict(session_date=day.isoformat(), boundary_ms=boundary, sequence=boundary // resolution)
    identity, fields = backtest_cursor_record_fields(cursor, {}, completed_at=at)
    if controller._source_cursor == cursor:
        return identity
    prior = controller._source_cursor
    if prior and (str(prior.get("session_date")) != day.isoformat()
            or prior.get("boundary_ms", -1) >= boundary):
        raise ValueError("Programme cursor cannot rewind or change its session")
    journal.append(run_id=controller.run_id, category="checkpoint", entity_type="market_boundary",
        entity_id=identity, event_time=at, payload=fields)
    controller._source_cursor = cursor
    controller._frame_cursor = {}
    return identity
