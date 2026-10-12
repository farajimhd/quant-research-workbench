"""Program broker image from shared normalized matching and execution facts."""
from datetime import datetime

from .qmd_columnar_history_transport import _owned_worker
from .backtest_market_data import market_day_boundary
from .backtest_v4_broker_state_restore import reconstruct_broker_match_state
from .backtest_v4_execution_restore import load_v4_broker_executions
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, load_verified_v4_prefix
from src.trading_runtime.strategy_one_broker_match_snapshot import verify_broker_match_snapshot
from src.trading_runtime.simulated_broker import SimulationConfig


async def recover_program_broker_async(reader_factory, prefix, *, broker,
        quotes, lineages, simulation_config, through_sequence=None):
    """Stage an inert broker image; caller separately proves source and OMS.

    Quote values must come from the pinned certified liquidity product. Shared
    restoration verifies their exact persisted clocks. No broker is started.
    Historical snapshots remain inside the full verified commit prefix.
    """
    if (type(prefix) is not V4CommittedPrefix or prefix.status != "running"
            or type(simulation_config) is not SimulationConfig):
        raise ValueError("Program broker recovery requires its declared typed scope")
    if through_sequence is not None and (type(through_sequence) is not int
            or not 0 <= through_sequence <= prefix.last_sequence):
        raise ValueError("Program broker historical cutoff differs from verified prefix")
    basis_sequence = prefix.last_sequence if through_sequence is None else through_sequence
    broker = verify_broker_match_snapshot(broker)
    root = broker.snapshot
    if root["run_id"] != prefix.run_id or root["checkpoint_sequence"] != basis_sequence:
        raise ValueError("Program broker snapshot differs from financial prefix")
    resolution = simulation_config.liquidity_resolution_ms
    if root["boundary_ms"] % resolution or any(
            row["last_boundary_ms"] % resolution for row in broker.tickers):
        raise ValueError("Program broker checkpoint differs from its liquidity resolution")
    lineages = tuple(lineages)
    requests, by_client, client_by_broker = {}, {}, {}
    for lineage in lineages:
        if (lineage.through_sequence != basis_sequence
                or lineage.state.group["run_id"] != prefix.run_id):
            raise ValueError("Program broker OMS lineage differs from financial prefix")
        for order in lineage.orders:
            if not order.cOID or order.cOID in by_client:
                raise ValueError("Program broker OMS client identity repeats")
            by_client[order.cOID] = order
        for binding in lineage.state.broker_bindings:
            index = binding["request_index"]
            if index is None:
                continue
            identity = str(binding["broker_order_id"])
            if (type(index) is not int or not 0 <= index < len(lineage.orders)
                    or identity in client_by_broker):
                raise ValueError("Program broker OMS binding is invalid or duplicate")
            order = lineage.orders[index]
            client_by_broker[identity] = order.cOID
            if not binding["terminal"]:
                requests[identity] = order
    open_ids = {row["broker_order_id"] for row in broker.open_orders}
    if not open_ids.issubset(requests):
        raise ValueError("Program broker lacks exact open OMS requests")
    state = reconstruct_broker_match_state(broker,
        requests_by_broker_id={key: requests[key] for key in open_ids}, quotes=quotes)
    # The shared legacy decoder labels its quote cache as 100ms. The current
    # program path retains the declared independent broker resolution instead.
    for quote in state["quotes_by_ticker"].values():
        quote["source"] = simulation_config.liquidity_source
    cutoff = market_day_boundary(root["session_date"], root["boundary_ms"])

    def read():
        client = reader_factory()
        try:
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program broker prefix moved before execution read")
            state["executions"] = load_v4_broker_executions(client, prefix,
                requests_by_coid=by_client, coid_by_broker_id=client_by_broker,
                next_execution_id=state["next_execution_id"],
                **({"through_sequence": basis_sequence} if through_sequence is not None else {}))
            if any(datetime.fromisoformat(row["trade_time"]).tzinfo is None
                   or datetime.fromisoformat(row["trade_time"]) > cutoff
                   for row in state["executions"]):
                raise ValueError("Program broker execution exceeds its completed clock")
            if load_verified_v4_prefix(client, prefix.run_id) != prefix:
                raise ValueError("Program broker prefix moved during execution read")
            return state
        finally:
            client.close()
    return await _owned_worker(read)
