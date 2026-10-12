"""Construction of the shared app runtime from staged program financial facts."""
from copy import deepcopy
from datetime import datetime

from .program_financial_recovery import build_program_portfolio_engine
from .backtest_journal_memory import BacktestMemoryJournal
from src.trading_runtime.arte_portfolio_recovery import PortfolioRecovery
from src.trading_runtime.arte_oms_actor_restore import TypedOmsActorImage
from src.trading_runtime.columnar_program_actor import AssignedProgramStrategy
from src.trading_runtime.runtime import TradingRuntime, RunConfig, RunMode
from src.trading_runtime.domain import TradingMode
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.strategy_orders import RuntimeIbkrStrategyOrderPlanner


def build_recovered_program_runtime(config, strategy, journal, *, portfolio_recovery,
        oms_image, broker_image, simulation_config, completed_at, intent_planner,
        strategy_configuration, groups=(), drawdown_measure_policy=None):
    """Construct inert actors; caller still owns fences, initialization and admission.

    Uses the same TradingRuntime, Portfolio, OMS and simulator as app runs.
    This does not start producers, publish anything or reconstruct policy state.
    """
    if (type(config) is not RunConfig or config.mode is not RunMode.BACKTEST
            or not config.run_id or config.write_progress_checkpoints
            or type(strategy) is not AssignedProgramStrategy
            or type(journal) is not BacktestMemoryJournal
            or type(portfolio_recovery) is not PortfolioRecovery
            or type(oms_image) is not TypedOmsActorImage
            or type(simulation_config) is not SimulationConfig
            or not isinstance(completed_at, datetime) or completed_at.tzinfo is None
            or type(intent_planner) is not RuntimeIbkrStrategyOrderPlanner
            or (intent_planner.run_id, intent_planner.strategy_id, intent_planner.strategy_revision)
               != (config.run_id, config.strategy_id, config.strategy_revision)
            or not config.account_ids or len(set(config.account_ids)) != len(config.account_ids)
            or any(assignment.account_id not in config.account_ids for assignment in strategy.assignments())
            or any(group.account_id not in config.account_ids for group in oms_image.groups.values())
            or (config.strategy_id, config.strategy_revision) != (strategy.strategy_id, strategy.revision)
            or (oms_image.run_id, oms_image.strategy_id, oms_image.strategy_revision)
               != (config.run_id, config.strategy_id, config.strategy_revision)
            or portfolio_recovery.run_id != config.run_id
            or set(portfolio_recovery.states) != set(config.account_ids)
            or set(broker_image["account_ids"]) != set(config.account_ids)
            or len(broker_image["account_ids"]) != len(config.account_ids)
            or journal.latest_sequence(config.run_id) not in set(portfolio_recovery.revisions.values())
            or journal.pending_record_count):
        raise ValueError("Program cold runtime differs from its exact typed financial scope")
    portfolio = build_program_portfolio_engine(portfolio_recovery, journal=journal,
        strategy_id=config.strategy_id, strategy_revision=config.strategy_revision,
        groups=groups, event_clock=lambda: completed_at,
        drawdown_measure_policy=drawdown_measure_policy)
    broker = SimulatedBrokerAdapter(list(config.account_ids), config=simulation_config,
        mode=TradingMode.BACKTEST, initial_time=completed_at, fixed_bar_mode=True)
    broker.restore_checkpoint_state(deepcopy(broker_image))
    # A financial candidate starts with an empty policy actor. Only the
    # independently replayed and qualified policy packet may install fills,
    # targets, trail water and consumed decision clocks afterward.
    staged_strategy = AssignedProgramStrategy(strategy.program_release,
        deepcopy(strategy.assignments()), dict(strategy._listings))
    staged_planner = RuntimeIbkrStrategyOrderPlanner(deepcopy(intent_planner.instruments),
        strategy_id=config.strategy_id, strategy_revision=config.strategy_revision,
        run_id=config.run_id, limit_offset_bps=intent_planner.limit_offset_bps)
    runtime = TradingRuntime(deepcopy(config), broker, staged_strategy, journal,
        intent_planner=staged_planner, portfolio=portfolio,
        strategy_configuration=deepcopy(strategy_configuration), typed_oms_image=oms_image)
    runtime.last_event_time = completed_at
    return runtime
