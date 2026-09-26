"""A V4 launch cannot infer journal coverage from a quiet sample run."""
from __future__ import annotations

import pytest

from src.backend.backtest_fixed_v4_certification import (
    certify_fixed_broker_stream_unreachable,
    certify_fixed_rebalance_unreachable,
    certify_strategy_one_legacy_protection_unreachable,
    certify_strategy_one_portfolio_request_unreachable,
    certify_strategy_one_v4_projection,
)


def test_v4_certificate_binds_direct_and_indirect_sources(tmp_path):
    controller = tmp_path / "controller.py"
    controller.write_text(
        'self._journal.append(category="checkpoint", entity_type="market_boundary")',
        encoding="utf-8")
    oms = tmp_path / "order_management.py"
    oms.write_text(
        'self._record("broker", "order_acknowledgement", "1", "A", at, {})\n'
        'self._record("order_management", "protection_reconciliation", '
        '"1", "A", at, {})\n', encoding="utf-8")
    kwargs = {"controller_source": controller, "indirect_sources": (oms,)}
    first = certify_strategy_one_v4_projection(**kwargs)
    assert len(first) == 64
    assert certify_strategy_one_v4_projection(**kwargs) == first
    oms.write_text(oms.read_text(encoding="utf-8") + "# source revision\n",
                   encoding="utf-8")
    assert certify_strategy_one_v4_projection(**kwargs) != first


def test_v4_certificate_rejects_unprojected_indirect_family(tmp_path):
    controller = tmp_path / "controller.py"
    controller.write_text(
        'self._journal.append(category="checkpoint", entity_type="market_boundary")',
        encoding="utf-8")
    oms = tmp_path / "order_management.py"
    oms.write_text('self._record("broker", "unknown", "1", "A", at, {})',
                   encoding="utf-8")
    with pytest.raises(ValueError, match="V4 indirect emitters lack typed projection"):
        certify_strategy_one_v4_projection(
            controller_source=controller, indirect_sources=(oms,))


def test_current_runtime_is_not_yet_v4_certified():
    with pytest.raises(ValueError, match="order_cancel_requested") as failure:
        certify_strategy_one_v4_projection()
    # The initial Backtest save is event-free, but that alone does not prove
    # every future assignment update unreachable. Keep this family blocked
    # until it has a normalized projection or a complete call-graph proof.
    assert "strategy_assignment_state" in str(failure.value)


def test_simulated_broker_websocket_exclusion_fails_on_source_change(tmp_path):
    from src.backend import backtest_fixed_v4_certification as cert

    sources = {
        "controller_path": cert.Path(__file__).parents[1] / "src/backend/replay_run_service.py",
        "runtime_path": cert.Path(__file__).parents[1] / "src/trading_runtime/runtime.py",
        "oms_path": cert.Path(__file__).parents[1] / "src/trading_runtime/order_management.py",
        "broker_path": cert._SIMULATED_BROKER,
    }
    copies = {}
    for name, source in sources.items():
        target = tmp_path / source.name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        copies[name] = target
    assert len(certify_fixed_broker_stream_unreachable(**copies)) == 64
    broker = copies["broker_path"]
    broker.write_text(broker.read_text(encoding="utf-8").replace(
        "    requires_fresh_execution_state = False",
        "    requires_fresh_execution_state = False\n"
        "    def stream_broker_messages(self): pass", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="transport absence"):
        certify_fixed_broker_stream_unreachable(**copies)


def test_rebalance_exclusion_requires_both_runtime_and_portfolio_guards(tmp_path):
    from pathlib import Path
    from src.backend import backtest_fixed_v4_certification as cert

    sources = {
        "runtime_path": Path(__file__).parents[1] / "src/trading_runtime/runtime.py",
        "portfolio_path": Path(__file__).parents[1] / "src/trading_runtime/portfolio.py",
        "intent_path": cert._STRATEGY_ONE_INTENT,
    }
    copies = {}
    for key, source in sources.items():
        target = tmp_path / source.name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        copies[key] = target
    assert len(certify_fixed_rebalance_unreachable(**copies)) == 64
    runtime = copies["runtime_path"]
    runtime.write_text(runtime.read_text(encoding="utf-8").replace(
        "require_no_replacement_capital(evaluation.intents)",
        "pass", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="replacement guard"):
        certify_fixed_rebalance_unreachable(**copies)
    runtime.write_text(sources["runtime_path"].read_text(encoding="utf-8"),
                       encoding="utf-8")
    portfolio = copies["portfolio_path"]
    portfolio.write_text(portfolio.read_text(encoding="utf-8").replace(
        "or not request.allow_replacement", "", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="rebalance has another or unguarded"):
        certify_fixed_rebalance_unreachable(**copies)
    portfolio.write_text(sources["portfolio_path"].read_text(encoding="utf-8"),
                         encoding="utf-8")
    intent = copies["intent_path"]
    intent.write_text(intent.read_text(encoding="utf-8").replace(
        "and intent.capital_request.allow_replacement for intent in intents",
        "and False for intent in intents", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="no longer rejects replacement"):
        certify_fixed_rebalance_unreachable(**copies)


def test_strategy_one_rejects_replacement_capital():
    from types import SimpleNamespace
    from src.trading_runtime.signals import CapitalRequest
    from src.trading_runtime.strategy_one_intent import require_no_replacement_capital

    request = SimpleNamespace(capital_request=CapitalRequest(
        mode="mandate_fraction", value=1 / 3))
    require_no_replacement_capital((request,))
    replacement = SimpleNamespace(capital_request=CapitalRequest(
        mode="mandate_fraction", value=1 / 3, allow_replacement=True))
    with pytest.raises(ValueError, match="replacement capital"):
        require_no_replacement_capital((replacement,))


def test_numbered_protection_bypasses_legacy_oms_managers(tmp_path):
    import asyncio
    from datetime import datetime, timezone
    from pathlib import Path
    from src.backend import backtest_fixed_v4_certification as cert
    from src.trading_runtime.order_management import OrderManagementEngine
    from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER

    sources = {
        "oms_path": Path(__file__).parents[1] / "src/trading_runtime/order_management.py",
        "runtime_path": Path(__file__).parents[1] / "src/trading_runtime/runtime.py",
        "contract_path": cert._STRATEGY_ONE_CONTRACT,
    }
    copies = {}
    for key, source in sources.items():
        target = tmp_path / source.name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        copies[key] = target
    assert len(certify_strategy_one_legacy_protection_unreachable(**copies)) == 64
    oms = copies["oms_path"]
    oms.write_text(oms.read_text(encoding="utf-8").replace(
        "return False  # Strategy 1 owns its full-target amendment at completed boundaries.",
        "pass", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="legacy OMS event may be reachable"):
        certify_strategy_one_legacy_protection_unreachable(**copies)

    manager = object.__new__(OrderManagementEngine)
    manager.strategy_id = STRATEGY_ID
    manager.strategy_revision = STRATEGY_NUMBER
    async def exercise():
        assert await manager._complete_partial_target(None, datetime.now(timezone.utc)) is False
        assert await manager.apply_profit_pocket_transition(None) == []
        assert await manager._ratchet_dynamic_protection(None, None) is None
    asyncio.run(exercise())


def test_strategy_one_cannot_enter_legacy_deferred_request_cleanup(tmp_path):
    from pathlib import Path
    from src.backend import backtest_fixed_v4_certification as cert

    sources = {
        "runtime_path": Path(__file__).parents[1] / "src/trading_runtime/runtime.py",
        "portfolio_path": Path(__file__).parents[1] / "src/trading_runtime/portfolio.py",
        "contract_path": cert._STRATEGY_ONE_CONTRACT,
    }
    copies = {}
    for key, source in sources.items():
        target = tmp_path / source.name
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        copies[key] = target
    assert len(certify_strategy_one_portfolio_request_unreachable(**copies)) == 64
    runtime = copies["runtime_path"]
    runtime.write_text(runtime.read_text(encoding="utf-8").replace(
        "self.config.strategy_revision >= 41", "self.config.strategy_revision >= 1", 1),
        encoding="utf-8")
    with pytest.raises(ValueError, match="revision-41 guarded"):
        certify_strategy_one_portfolio_request_unreachable(**copies)
