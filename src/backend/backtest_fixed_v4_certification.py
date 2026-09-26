"""Source-bound, fail-closed journal-family certificate for Strategy 1 V4.

This is deliberately stricter than a sample-run inventory: a quiet market day
cannot prove that an unexercised OMS or broker branch is normalized. Until each
reachable family is projected or its fixed-path exclusion is proved, launch
preflight must reject the runtime source tree.
"""
from __future__ import annotations

import ast
from hashlib import sha256
import json
from pathlib import Path

from src.backend.backtest_fixed_v3_certification import (
    _INDIRECT_SOURCES, _V3_PROJECTED,
    _FIXED_UNREACHABLE_ADAPTIVE_REPRICE,
    certify_direct_v3_projection, certify_fixed_adaptive_reprice_unreachable,
    indirect_journal_inventory,
)


_COMMON_TYPED = frozenset({
    ("lifecycle", "run"),
    ("broker", "connection_state"),
    ("risk", "risk_snapshot"),
    ("risk", "continuous_risk_state"),
    ("strategy", "strategy_intent"),
    ("strategy_decision", "intent_rejection"),
    ("strategy_decision", "intent_deferral"),
    ("execution", "fill"),
    ("execution", "commission"),
})

# These V4 families have a dedicated typed projector and cold-readback test.
# Do not add a family merely because a table with a similar name exists.
_V4_ADDITIONS = frozenset({
    ("broker", "order_acknowledgement"),
    ("order_management", "order_group_state"),
    ("order_management", "protection_reconciliation"),
    ("snapshot", "portfolio"),
    ("snapshot", "position"),
})
_SIMULATED_BROKER = Path(__file__).parents[1] / "trading_runtime" / "simulated_broker.py"
_STRATEGY_ONE_INTENT = Path(__file__).parents[1] / "trading_runtime" / "strategy_one_intent.py"


def certify_fixed_rebalance_unreachable(
    *, runtime_path: Path, portfolio_path: Path,
    intent_path: Path = _STRATEGY_ONE_INTENT,
) -> str:
    """Bind the Strategy 1 capital guard to Portfolio's rebalance condition."""
    paths = (runtime_path, portfolio_path, intent_path)
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    runtime, portfolio, intent = (ast.parse(source) for source in sources)

    def method(tree: ast.AST, owner: str, name: str, kind: type) -> ast.AST:
        classes = [node for node in getattr(tree, "body", ())
                   if isinstance(node, ast.ClassDef) and node.name == owner]
        matches = [node for node in classes[0].body
                   if isinstance(node, kind) and node.name == name] if len(classes) == 1 else []
        if len(matches) != 1:
            raise ValueError("Strategy 1 rebalance exclusion source changed")
        return matches[0]

    execute = method(runtime, "TradingRuntime", "_execute_intents", ast.AsyncFunctionDef)
    expected_guard = (
        "if self.config.mode == RunMode.BACKTEST and "
        "self.config.strategy_id == STRATEGY_ID and "
        "(self.config.strategy_revision == STRATEGY_NUMBER):\n"
        "    from .strategy_one_intent import require_no_replacement_capital\n"
        "    require_no_replacement_capital(evaluation.intents)"
    )
    if (len(execute.body) < 2
            or ast.unparse(execute.body[0]) !=
            "from .strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER"
            or ast.unparse(execute.body[1]) != expected_guard):
        raise ValueError("Strategy 1 replacement guard is not before Portfolio routing")
    helpers = [node for node in intent.body if isinstance(node, ast.FunctionDef)
               and node.name == "require_no_replacement_capital"]
    if (len(helpers) != 1 or len(helpers[0].body) != 2
            or ast.unparse(helpers[0].body[1]) !=
            "if any((intent.capital_request is not None and "
            "intent.capital_request.allow_replacement for intent in intents)):\n"
            "    raise ValueError('Strategy 1 cannot request replacement capital')"):
        raise ValueError("Strategy 1 replacement guard no longer rejects replacement")
    rebalance = method(portfolio, "PortfolioManagementEngine", "_propose_rebalance",
                       ast.FunctionDef)
    if (len(rebalance.body) < 3
            or ast.unparse(rebalance.body[0]) != "request = intent.capital_request"
            or ast.unparse(rebalance.body[2]) !=
            "if request is None or not request.allow_replacement or "
            "(not bool(mandate.get('allow_replacement', False))):\n"
            "    return None"
            or sum(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "_propose_rebalance"
                   for node in ast.walk(portfolio)) != 1):
        raise ValueError("Portfolio rebalance has another or unguarded route")
    return sha256(json.dumps({"version": 1, "sources": tuple(
        sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify_fixed_broker_stream_unreachable(
    *, controller_path: Path, runtime_path: Path, oms_path: Path,
    broker_path: Path = _SIMULATED_BROKER,
) -> str:
    """Prove simulated Backtest cannot enter the IBKR websocket emitter."""
    paths = (controller_path, runtime_path, oms_path, broker_path)
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    controller, runtime, oms, broker = tuple(ast.parse(source) for source in sources)
    simulated = [node for node in broker.body if isinstance(node, ast.ClassDef)
                 and node.name == "SimulatedBrokerAdapter"]
    if (len(simulated) != 1 or simulated[0].bases
            or any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name in {"__getattr__", "__getattribute__",
                                     "stream_broker_messages"}
                   for node in ast.walk(simulated[0]))
            or any(isinstance(node, ast.Constant)
                   and node.value == "stream_broker_messages"
                   for node in ast.walk(simulated[0]))):
        raise ValueError("Simulated broker transport absence is unproven")
    initializers = [node for node in ast.walk(controller)
                    if isinstance(node, ast.AsyncFunctionDef)
                    and node.name == "_initialize_runtime"]
    if len(initializers) != 1:
        raise ValueError("Backtest broker construction is unproven")
    broker_assignments = [node for node in ast.walk(initializers[0])
                          if isinstance(node, ast.Assign)
                          and any(isinstance(target, ast.Name)
                                  and target.id == "broker"
                                  for target in node.targets)]
    runtimes = [node for node in ast.walk(initializers[0])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "TradingRuntime"]
    if (len(broker_assignments) != 1 or len(runtimes) != 1
            or not isinstance(broker_assignments[0].value, ast.Call)
            or not isinstance(broker_assignments[0].value.func, ast.Name)
            or broker_assignments[0].value.func.id != "SimulatedBrokerAdapter"
            or len(runtimes[0].args) < 2
            or not isinstance(runtimes[0].args[1], ast.Name)
            or runtimes[0].args[1].id != "broker"):
        raise ValueError("Backtest does not forward its simulated broker unchanged")
    starts = [node for node in ast.walk(runtime)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "_consume_broker_stream"]
    guards = [node for node in ast.walk(runtime) if isinstance(node, ast.If)
              and ast.unparse(node.test) ==
              "hasattr(self.broker, 'stream_broker_messages')"]
    if (len(starts) != 1 or len(guards) != 1
            or starts[0] not in ast.walk(guards[0])
            or sum(isinstance(node, ast.Call)
                   and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "on_broker_message"
                   for node in ast.walk(runtime)) != 1):
        raise ValueError("OMS websocket consumption is not exclusively guarded")
    emitters = [node for node in ast.walk(oms)
                if isinstance(node, ast.Constant)
                and node.value == "broker_execution"]
    methods = [node for node in ast.walk(oms)
               if isinstance(node, ast.AsyncFunctionDef)
               and node.name == "on_broker_message"]
    if (len(emitters) != 1 or len(methods) != 1
            or emitters[0] not in ast.walk(methods[0])):
        raise ValueError("Broker execution has another or missing emitter")
    return sha256(json.dumps({"version": 1, "sources": tuple(
        sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify_strategy_one_v4_projection(
    *, controller_source: Path | None = None,
    indirect_sources: tuple[Path, ...] = _INDIRECT_SOURCES,
) -> str:
    """Reject any indirect emitter without a proven normalized V4 projection.

    The direct controller certificate already checks fixed-only reachability.
    This separate indirect inventory binds the runtime/OMS/portfolio sources;
    it cannot be replaced by observing one Backtest's emitted records.
    """
    direct = (certify_direct_v3_projection(source_path=controller_source)
              if controller_source is not None else certify_direct_v3_projection())
    families, dynamic = indirect_journal_inventory(indirect_sources)
    if dynamic:
        raise ValueError(f"V4 indirect journal emitter identity is dynamic: {dynamic}")
    unreachable: set[tuple[str, str]] = set()
    unreachable_proof = ""
    if _FIXED_UNREACHABLE_ADAPTIVE_REPRICE in families:
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "order_management.py" not in sources_by_name
                or "runtime.py" not in sources_by_name):
            raise ValueError("V4 adaptive skip lacks fixed-runtime source authority")
        proof_args = {
            "oms_path": sources_by_name["order_management.py"],
            "runtime_path": sources_by_name["runtime.py"],
        }
        if controller_source is not None:
            proof_args["controller_path"] = controller_source
        unreachable_proof = certify_fixed_adaptive_reprice_unreachable(
            **proof_args)
        unreachable.add(_FIXED_UNREACHABLE_ADAPTIVE_REPRICE)
    if ("execution", "broker_execution") in families:
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "order_management.py" not in sources_by_name
                or "runtime.py" not in sources_by_name):
            raise ValueError("V4 broker execution lacks fixed-runtime source authority")
        websocket_proof = certify_fixed_broker_stream_unreachable(
            controller_path=controller_source or Path(__file__).with_name(
                "replay_run_service.py"),
            runtime_path=sources_by_name["runtime.py"],
            oms_path=sources_by_name["order_management.py"])
        unreachable_proof += websocket_proof
        unreachable.add(("execution", "broker_execution"))
    if ("portfolio_management", "portfolio_rebalance") in families:
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "portfolio.py" not in sources_by_name
                or "runtime.py" not in sources_by_name):
            raise ValueError("V4 rebalance lacks fixed-runtime source authority")
        unreachable_proof += certify_fixed_rebalance_unreachable(
            runtime_path=sources_by_name["runtime.py"],
            portfolio_path=sources_by_name["portfolio.py"])
        unreachable.add(("portfolio_management", "portfolio_rebalance"))
    supported = _V3_PROJECTED | _COMMON_TYPED | _V4_ADDITIONS
    unsupported = sorted(set(families) - supported - unreachable)
    if unsupported:
        raise ValueError(f"V4 indirect emitters lack typed projection: {unsupported}")
    evidence = tuple((path.name, sha256(path.read_bytes()).hexdigest())
                     for path in indirect_sources)
    return sha256(json.dumps({
        "version": 1, "direct": direct, "families": families,
        "sources": evidence, "unreachable_proof": unreachable_proof,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
