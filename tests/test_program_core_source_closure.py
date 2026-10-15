"""Current programme core rejects changed leaves even after a warmed proof."""
from pathlib import Path
import pytest
from src.backend import backtest_fixed_v4_certification as cert

CURRENT_LEAVES = (
    'src/backend/program_runtime_assembly.py',
    'src/backend/program_runtime_decision.py',
    'src/backend/program_boundary_dispatch.py',
    'src/backend/program_execution_journal.py',
    'src/backend/program_pending_publication.py',
    'src/backend/program_broker_executions.py',
    'src/backend/program_v4_principal.py',
    'src/backend/live_strategy_runtime_service.py',
    'src/backend/live_strategy_one_v4_principal.py',
    'src/backend/program_app_credentials.py',
    'src/backend/managed_backtest_credentials.py',
    'src/backend/program_app_storage.py',
    'src/backend/program_managed_configuration.py',
    'src/trading_runtime/program_broker_gateway.py',
    'src/trading_runtime/program_portfolio_journal.py',
    'src/trading_runtime/program_portfolio_transitions.py',
    'src/trading_runtime/arte_broker_policy_v4.py',
    'src/trading_runtime/arte_portfolio_control_v4.py',
    'src/trading_runtime/arte_command_dispatcher.py',
    'src/trading_runtime/arte_journal_compound_v4.py',
    'src/trading_runtime/arte_portfolio_sync.py',
    'src/trading_runtime/keeper_ownership.py',
    'src/backend/program_signal_source.py',
    'src/backend/signal_execution_context.py',
    'src/backend/signal_context_schema.py',
    'src/backend/signal_context_transport.py',
    'src/backend/signal_typed_json_wire.py',
    'src/backend/signal_source_clickhouse_storage.py',
    'src/backend/signal_source_registered_storage.py',
    'src/backend/signal_source_insert_dispatch.py',
    'src/backend/signal_stream_session_head.py',
    'src/backend/signal_stream_typed_attestation.py',
    'src/backend/live_signal_source_cursor_proof.py',
    'src/backend/backtest_market_data.py',
    'src/backend/backtest_liquidity_price.py',
    'src/backend/qmd_volume_reader.py',
    'src/backend/qmd_opening_reference.py',
    'src/trading_runtime/arte_market_day_cold_preflight.py',
    'src/trading_runtime/arte_market_day_keeper.py',
    'src/market_engine/qmd_volume_product.py',
    'src/market_engine/typed_product_insert.py',
    'src/market_engine/causal_market_history.py',
    'src/market_engine/causal_volume_history.py',
    'pipelines/market_sip/events/program_input_publication.py',
    'pipelines/market_sip/events/qmd_feature_preparation.py',
    'pipelines/market_sip/events/qmd_feature_reference.py',
    'pipelines/market_sip/events/qmd_volume_producer.py',
    'pipelines/market_sip/events/columnar_history_producer.py',
    'pipelines/market_sip/events/opening_price_reference_producer.py',
    'pipelines/market_sip/events/regular_close_history_producer.py',
    'pipelines/market_sip/events/liquidity_resolution_producer.py',
    'pipelines/market_sip/events/liquidity_execution_price_producer.py',
    'pipelines/market_sip/events/shared_reference_listing_basis.py',
    'src/backend/optimized_audit_run.py',
    'src/backend/optimized_audit_collection.py',
    'research/vectorized_backtest/v19/audit_reference.py',
    'scripts/audit_optimized_strategy.py',
)

@pytest.mark.parametrize('relative', CURRENT_LEAVES)
def test_warmed_current_programme_core_rejects_modified_leaf(monkeypatch, relative):
    assert relative in cert._DRAWDOWN_CORE_REQUIRED_SOURCE_FILES
    control = cert.certify_drawdown_measure_core_source()
    original = Path.read_text
    expected = (Path(cert.__file__).parents[2] / relative).resolve()
    def changed(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        return value + '\n_PROGRAM_CORE_MUTATION = True\n' if path.resolve() == expected else value
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_text', changed)
        with pytest.raises(ValueError, match='Drawdown core source changed: ' + relative):
            cert.certify_drawdown_measure_core_source()
    assert cert.certify_drawdown_measure_core_source() == control
