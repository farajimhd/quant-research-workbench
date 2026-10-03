"""Exhaustive native emitter inventory plus Strategy 43 reachability proofs."""
import ast
from hashlib import sha256
import json
from pathlib import Path

from .backtest_fixed_v3_certification import (
    _INDIRECT_SOURCES, _V3_PROJECTED, _FIXED_UNREACHABLE_ADAPTIVE_REPRICE,
    indirect_journal_inventory, certify_fixed_adaptive_reprice_unreachable,
)
from .backtest_fixed_v4_certification import (
    _COMMON_TYPED, _V4_ADDITIONS, _LEGACY_PROTECTION_FAMILIES,
    _REDUNDANT_MODIFY_SUMMARIES, certify_fixed_broker_stream_unreachable,
    certify_strategy_one_legacy_protection_unreachable,
)


def certify_projection():
    root = Path(__file__).parents[1]
    controller = Path(__file__).with_name("backtest_strategy_forty_three_controller.py")
    own = tuple(sorted((*root.glob("backend/backtest_strategy_forty_three_*.py"),
                        *root.glob("trading_runtime/strategy_forty_three_*.py"))))
    trees = {path.name: ast.parse(path.read_text(encoding="utf-8")) for path in own}
    # These legacy routes alone consume raw observations, invoke _execute_intents,
    # or withdraw deferred requests. The independent lane never calls them.
    forbidden = {"_execute_intents", "process_market_signal", "process_strategy_observation",
                 "process_account_strategy_observation", "withdraw_invalidated_requests"}
    if any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
           and node.func.attr in forbidden for tree in trees.values() for node in ast.walk(tree)):
        raise ValueError("Strategy 43 reached a legacy strategy or deferred-request emitter")
    assigned = trees["strategy_forty_three_runtime.py"]
    if any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
           and node.name in {"on_order_group_update", "on_market_signal"} for node in ast.walk(assigned)):
        raise ValueError("Strategy 43 acquired an assignment-activity callback")
    saves = [node for node in ast.walk(trees[controller.name]) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "persist_strategy_assignments"]
    if len(saves) != 1 or not any(keyword.arg == "record_events"
            and isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            for keyword in saves[0].keywords):
        raise ValueError("Strategy 43 assignment initialization emits legacy activity")
    # Every entry uses exact fixed quantities. Empty mandates also prohibit
    # Portfolio replacement, regardless of any default on CapitalRequest.
    from src.trading_runtime.strategy_forty_three_release import configuration, release_contract
    payload = configuration(approved_code_commit="0" * 40,
        approved_code_fingerprint="0" * 64, approval_reference="source-inventory")
    if payload["portfolio"]["mandates"] or payload["portfolio"]["groups"]:
        raise ValueError("Strategy 43 gained Portfolio replacement authority")
    families, dynamic = indirect_journal_inventory()
    if dynamic:
        raise ValueError(f"Strategy 43 dynamic native emitter: {dynamic}")
    runtime, portfolio, oms, _ = _INDIRECT_SOURCES
    proofs = (
        certify_fixed_adaptive_reprice_unreachable(controller_path=controller),
        certify_fixed_broker_stream_unreachable(controller_path=controller,
            runtime_path=runtime, oms_path=oms),
        certify_strategy_one_legacy_protection_unreachable(runtime_path=runtime, oms_path=oms),
    )
    excluded = set(_LEGACY_PROTECTION_FAMILIES) | set(_REDUNDANT_MODIFY_SUMMARIES) | {
        _FIXED_UNREACHABLE_ADAPTIVE_REPRICE, ("execution", "broker_execution"),
        ("portfolio_management", "portfolio_rebalance"),
        ("portfolio_management", "portfolio_request"), ("strategy", "strategy_assignment_state")}
    unsupported = set(families) - _V3_PROJECTED - _COMMON_TYPED - _V4_ADDITIONS - excluded
    if unsupported:
        raise ValueError(f"Strategy 43 native families lack projection: {sorted(unsupported)}")
    # Bind every reviewed collaborator, independent decision/source path, and
    # command/intent projector. No sample-run inventory can replace this seal.
    shared = (*_INDIRECT_SOURCES, root / "trading_runtime/simulated_broker.py",
        root / "trading_runtime/arte_journal_commit_v4.py",
        root / "trading_runtime/arte_oms_projection.py",
        Path(__file__).with_name("backtest_typed_projection.py"),
        Path(__file__).with_name("backtest_fixed_journal_bootstrap.py"))
    evidence = [(str(path.relative_to(root)), sha256(ast.unparse(ast.parse(
        path.read_text(encoding="utf-8"))).encode()).hexdigest()) for path in (*shared, *own)]
    release = release_contract()
    return sha256(json.dumps(dict(version=1, policy=release.approved_digest,
        sources=evidence, families=families, excluded=sorted(excluded), proofs=proofs),
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()
