"""Source-bound, fail-closed journal-family certificate for Strategy 1 V4.

This is deliberately stricter than a sample-run inventory: a quiet market day
cannot prove that an unexercised OMS or broker branch is normalized. Until each
reachable family is projected or its fixed-path exclusion is proved, launch
preflight must reject the runtime source tree.
"""
from __future__ import annotations

import ast
from src.backend.source_ast_summary import canonical_symbol_ast_summary
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
    ("command", "order_cancel"),
    ("broker", "order_cancel_requested"),
    ("broker", "order_repriced"),
    ("broker", "order_reprice_error"),
    ("risk", "kill_entry_order"),
    ("risk", "emergency_flatten"),
    ("order_management", "order_group_state"),
    ("order_management", "protection_reconciliation"),
    ("snapshot", "portfolio"),
    ("snapshot", "position"),
    ("order_management", "protection_replacement_deferred"),
})
_SIMULATED_BROKER = Path(__file__).parents[1] / "trading_runtime" / "simulated_broker.py"
_STRATEGY_ONE_INTENT = Path(__file__).parents[1] / "trading_runtime" / "strategy_one_intent.py"
_STRATEGY_ONE_CONTRACT = Path(__file__).parents[1] / "trading_runtime" / "strategy_one_contract.py"
_STRATEGY_ONE_RUNTIME = Path(__file__).parents[1] / "trading_runtime" / "strategy_one_runtime.py"
_STRATEGY_ONE_EXECUTION = Path(__file__).with_name("backtest_strategy_one_execution.py")
_NUMBERED_FIXED_CONTRACT = Path(__file__).parents[1] / "trading_runtime" / "numbered_fixed_strategy.py"


def _certify_numbered_identity(path: Path = _NUMBERED_FIXED_CONTRACT) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    predicates = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                  and node.name == "is_numbered_fixed_strategy"]
    expected = 'return strategy_id == STRATEGY_ID and type(revision) is int and (revision in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61))'
    if (len(predicates) != 1 or len(predicates[0].body) != 1
            or ast.unparse(predicates[0].body[0]) != expected):
        raise ValueError("Numbered fixed identity whitelist changed")
    return sha256(source.encode()).hexdigest()


def certify_numbered_fixed_v4_projection(strategy_number: int) -> str:
    """Extend the full inventory proof with Strategy 2's explicit session lane."""
    if type(strategy_number) is int and strategy_number == 51:
        from .backtest_strategy_fifty_one_certification import certify_strategy_fifty_one_source
        from src.trading_runtime.strategy_fifty_one_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_fifty_one_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(51) != release:
            raise ValueError("Strategy51 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 51).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 49:
        from .backtest_strategy_forty_nine_certification import certify_strategy_forty_nine_source
        from src.trading_runtime.strategy_forty_nine_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_forty_nine_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(49) != release:
            raise ValueError("Strategy49 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 49).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 59:
        from .backtest_strategy_fifty_nine_certification import certify_strategy_fifty_nine_source
        from src.trading_runtime.strategy_fifty_nine_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(50)
        additional_proof = certify_strategy_fifty_nine_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(59) != release:
            raise ValueError("Strategy 59 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 59).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 60:
        from .backtest_strategy_sixty_certification import certify_strategy_sixty_source
        from src.trading_runtime.strategy_sixty_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(50)
        additional_proof = certify_strategy_sixty_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(60) != release:
            raise ValueError("Strategy 60 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 60).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 61:
        from .backtest_strategy_sixty_one_certification import certify_strategy_sixty_one_source
        from src.trading_runtime.strategy_sixty_one_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(50)
        additional_proof = certify_strategy_sixty_one_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(61) != release:
            raise ValueError("Strategy 61 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 61).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 57:
        from .backtest_strategy_fifty_seven_certification import certify_strategy_fifty_seven_source
        from src.trading_runtime.strategy_fifty_seven_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(50)
        additional_proof = certify_strategy_fifty_seven_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(57) != release:
            raise ValueError("Strategy 57 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 57).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 58:
        from .backtest_strategy_fifty_eight_certification import certify_strategy_fifty_eight_source
        from src.trading_runtime.strategy_fifty_eight_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(50)
        additional_proof = certify_strategy_fifty_eight_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(58) != release:
            raise ValueError("Strategy 58 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 58).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 55:
        from .backtest_strategy_fifty_five_certification import certify_strategy_fifty_five_source
        from src.trading_runtime.strategy_fifty_five_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(50)
        additional_proof = certify_strategy_fifty_five_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(55) != release:
            raise ValueError("Strategy 55 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 55).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 56:
        from .backtest_strategy_fifty_six_certification import certify_strategy_fifty_six_source
        from src.trading_runtime.strategy_fifty_six_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(50)
        additional_proof = certify_strategy_fifty_six_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(56) != release:
            raise ValueError("Strategy 56 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 56).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 53:
        from .backtest_strategy_fifty_three_certification import certify_strategy_fifty_three_source
        from src.trading_runtime.strategy_fifty_three_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(50)
        additional_proof = certify_strategy_fifty_three_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(53) != release:
            raise ValueError("Strategy 53 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 53).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 54:
        from .backtest_strategy_fifty_four_certification import certify_strategy_fifty_four_source
        from src.trading_runtime.strategy_fifty_four_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(50)
        additional_proof = certify_strategy_fifty_four_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(54) != release:
            raise ValueError("Strategy 54 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 54).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 52:
        from .backtest_strategy_fifty_two_certification import certify_strategy_fifty_two_source
        from src.trading_runtime.strategy_fifty_two_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(50)
        additional_proof = certify_strategy_fifty_two_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(52) != release:
            raise ValueError("Strategy 52 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 52).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 50:
        from .backtest_strategy_fifty_certification import certify_strategy_fifty_source
        from src.trading_runtime.strategy_fifty_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_fifty_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(50) != release:
            raise ValueError("Strategy 50 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 50).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 48:
        from .backtest_strategy_forty_eight_certification import certify_strategy_forty_eight_source
        from src.trading_runtime.strategy_forty_eight_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_forty_eight_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(48) != release:
            raise ValueError("Strategy 48 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 48).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 47:
        from .backtest_strategy_forty_seven_certification import certify_strategy_forty_seven_source
        from src.trading_runtime.strategy_forty_seven_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(46)
        additional_proof = certify_strategy_forty_seven_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(47) != release:
            raise ValueError("Strategy 47 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 47).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 46:
        from .backtest_strategy_forty_six_certification import certify_strategy_forty_six_source
        from src.trading_runtime.strategy_forty_six_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_forty_six_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(46) != release:
            raise ValueError("Strategy 46 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 46).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 42:
        from .backtest_strategy_forty_two_certification import certify_strategy_forty_two_source
        from src.trading_runtime.strategy_forty_two_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(41)
        additional_proof = certify_strategy_forty_two_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(42) != release:
            raise ValueError("Strategy 42 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 42).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 41:
        from .backtest_strategy_forty_one_certification import certify_strategy_forty_one_source
        from src.trading_runtime.strategy_forty_one_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(40)
        additional_proof = certify_strategy_forty_one_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(41) != release:
            raise ValueError("Strategy 41 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 41).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 40:
        from .backtest_strategy_forty_certification import certify_strategy_forty_source
        from src.trading_runtime.strategy_forty_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(39)
        additional_proof = certify_strategy_forty_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(40) != release:
            raise ValueError("Strategy 40 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 40).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 39:
        from .backtest_strategy_thirty_nine_certification import certify_strategy_thirty_nine_source
        from src.trading_runtime.strategy_thirty_nine_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(38)
        additional_proof = certify_strategy_thirty_nine_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(39) != release:
            raise ValueError("Strategy 39 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 39).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 38:
        from .backtest_strategy_thirty_eight_certification import certify_strategy_thirty_eight_source
        from src.trading_runtime.strategy_thirty_eight_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(37)
        additional_proof = certify_strategy_thirty_eight_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(38) != release:
            raise ValueError("Strategy 38 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 38).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 37:
        from .backtest_strategy_episode_activity_certification import certify_episode_activity_source
        from src.trading_runtime.strategy_thirty_seven_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(36)
        additional_proof = certify_episode_activity_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(37) != release:
            raise ValueError("Strategy 37 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 37).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 36:
        from .backtest_strategy_entry_activity_certification import certify_entry_activity_source
        from src.trading_runtime.strategy_thirty_six_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(35)
        additional_proof = certify_entry_activity_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(36) != release:
            raise ValueError("Strategy 36 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 36).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 35:
        from .backtest_strategy_liquidity_fade_certification import certify_prepared_liquidity_fade_source
        from src.trading_runtime.strategy_thirty_five_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(34)
        additional_proof = certify_prepared_liquidity_fade_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(35) != release:
            raise ValueError("Strategy 35 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 35).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 34:
        from .backtest_strategy_confirmed_ah_certification import certify_confirmed_ah_source
        from src.trading_runtime.strategy_thirty_four_release import release_contract
        parent_proof = certify_numbered_fixed_v4_projection(33)
        additional_proof = certify_confirmed_ah_source()
        release = release_contract()
        release.verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if strategy_number in (31, 32, 33):
        from .backtest_strategy_profit_certification import certify_profit_giveback_route_source
        if strategy_number == 31:
            from src.trading_runtime.strategy_thirty_one_release import release_contract
        elif strategy_number == 32:
            from src.trading_runtime.strategy_thirty_two_release import release_contract
        else:
            from src.trading_runtime.strategy_thirty_three_release import release_contract
        # Reuse all inherited execution proofs rather than bypassing their
        # numbered rule checks; bind the complete additional route separately.
        parent_proof = certify_numbered_fixed_v4_projection(strategy_number - 1)
        profit_proof = certify_profit_giveback_route_source()
        release = release_contract()
        release.verify()
        return sha256(json.dumps((parent_proof, profit_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    contract = numbered_fixed_strategy(strategy_number)
    followthrough_proof = certify_followthrough_failure_v4_source() if strategy_number in (9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) else ""
    entry_scope_proof = certify_empty_exclusion_entry_scope_source() if strategy_number in (10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) else ""
    early_failure_proof = certify_early_followthrough_failure_v4_source() if strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) else ""
    recent_bos_proof = certify_recent_bos_entry_source() if strategy_number in (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) else ""
    rising_momentum_proof = certify_rising_momentum_entry_source() if strategy_number in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30) else ""
    base = certify_strategy_one_v4_projection()
    if strategy_number == 1:
        return base
    paths = (_NUMBERED_FIXED_CONTRACT, _STRATEGY_ONE_EXECUTION,
             Path(__file__).parents[1] / "trading_runtime" / "runtime.py",
             Path(__file__).parents[1] / "trading_runtime" / "order_management.py",
             Path(__file__).parents[1] / "trading_runtime" / "numbered_session_exit.py",
             Path(__file__).with_name("backtest_strategy_one_static_gate.py"),
             Path(__file__).with_name("backtest_strategy_one_management.py"),
             Path(__file__).parents[1] / "trading_runtime" / "strategy_one_position.py",
             Path(__file__).parents[1] / "trading_runtime" / "strategy_one_intent.py")
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    trees = tuple(ast.parse(source) for source in sources)
    def named(tree, name):
        nodes = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name == name]
        if len(nodes) != 1:
            raise ValueError(f"Strategy 2 source lane missing or ambiguous: {name}")
        return nodes[0]
    def calls(node):
        return {item.func.attr if isinstance(item.func, ast.Attribute) else item.func.id
                for item in ast.walk(node) if isinstance(item, ast.Call)
                and isinstance(item.func, (ast.Name, ast.Attribute))}
    clock = named(trees[2], "advance_numbered_session_clock")
    exit_source = named(trees[2], "submit_numbered_session_exit")
    cutoff = named(trees[3], "cancel_numbered_session_acquisitions")
    before = named(trees[1], "observe_numbered_boundary")
    finish = named(trees[1], "finish_numbered_boundary")
    intent = named(trees[4], "numbered_session_exit_intent")
    if (not contract.allows_session_exit
            or "cancel_numbered_session_acquisitions" not in calls(clock)
            or "acquisition_cutoff" not in calls(clock)
            or not {"numbered_session_exit_intent", "_execute_intents", "liquidation_due"} <= calls(exit_source)
            or not {"_cancel_open_entry_roots", "reconcile"} <= calls(cutoff)
            or "is_numbered_fixed_strategy" not in calls(cutoff)
            or calls(cutoff) & {"_record", "submit_order", "on_liquidity_bar"}
            or "advance_numbered_session_clock" not in calls(before)
            or "financially_active_tickers" not in calls(finish)
            or not any(isinstance(node, ast.Raise) for node in ast.walk(finish))):
        raise ValueError("Strategy 2 session command ordering proof failed")
    if not (isinstance(finish.body[0], ast.Expr)
            and isinstance(finish.body[0].value, ast.Await)
            and ast.unparse(finish.body[0].value.value) == "finish_boundary(work)"):
        raise ValueError("Numbered terminal cursor must complete before residual failure")
    if strategy_number in (3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        gate = named(trees[5], "compile_static_entry_gate")
        if not {"fromiter", "flatnonzero"} <= calls(gate):
            raise ValueError("Strategy 3 activation gate must remain vectorized")
        masks = [node for node in ast.walk(gate) if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "same_session"
                         for target in node.targets)]
        expected_mask = "(starts <= boundaries) & ((starts > 0) & (boundaries < 19500000) | (starts > 43200000) & (boundaries < 57000000))"
        if len(masks) != 1 or ast.unparse(masks[0].value) != expected_mask:
            raise ValueError("Strategy 3 activation session boundaries changed")
        gates = [node for node in ast.walk(trees[1]) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "compile_static_entry_gate"]
        selected_gates = [node for node in gates if any(key.arg == "strategy_number"
                and ast.unparse(key.value) == "runtime.config.strategy_revision"
                for key in node.keywords)]
        preliminary_gates = [node for node in gates if any(key.arg == "strategy_number"
                and ast.unparse(key.value) == "12" for key in node.keywords)]
        momentum_routes = [node for node in ast.walk(trees[1]) if isinstance(node, ast.If)
                and ast.unparse(node.test) == 'runtime.config.strategy_revision in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)']
        if (len(gates) != 2 or len(selected_gates) != 1 or len(preliminary_gates) != 1
                or len(momentum_routes) != 1
                or preliminary_gates[0] not in tuple(ast.walk(momentum_routes[0]))):
            raise ValueError("Strategy 3 static gate is not bound to its selected contract")
    if strategy_number in (4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        management = named(trees[6], "on_management")
        guard = [node for node in management.body if isinstance(node, ast.If)
                 and "not self.contract.allows_adds" in ast.unparse(node.test)]
        confirmations = [node for node in management.body if isinstance(node, ast.Assign)
                         and any(ast.unparse(target) == "self._positions[key]" for target in node.targets)]
        add_source = named(trees[2], "submit_strategy_one_add")
        submission_guards = [node for node in add_source.body if isinstance(node, ast.If)
            and ast.unparse(node.test) == "not numbered_fixed_strategy(proposal.strategy_number).allows_adds"
            and len(node.body) == 1 and isinstance(node.body[0], ast.Raise)]
        submissions = [node for node in ast.walk(add_source) if isinstance(node, ast.Call)
                       and isinstance(node.func, (ast.Name, ast.Attribute))
                       and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
                       in {"strategy_one_add_intent", "_execute_intents"}]
        if (len(guard) != 1 or len(guard[0].body) != 1
                or not isinstance(guard[0].body[0], ast.Return)
                or not confirmations or confirmations[-1].lineno >= guard[0].lineno
                or len(submission_guards) != 1 or not submissions
                or any(node.lineno <= submission_guards[0].lineno for node in submissions)):
            raise ValueError("Strategy 4 must prohibit adds after confirmed protection")
    if strategy_number in (5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        reducer = named(trees[7], "advance_protection")
        swing = [node for node in reducer.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "swing" for target in node.targets)]
        reducer_calls = [node for node in ast.walk(named(trees[6], "on_management"))
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                         and node.func.id == "advance_protection"]
        defaults = dict(zip((arg.arg for arg in reducer.args.kwonlyargs), reducer.args.kw_defaults))
        if (len(swing) != 1 or not isinstance(swing[0].value, ast.IfExp)
                or ast.unparse(swing[0].value.test) != "allows_completed_30s_trailing"
                or ast.unparse(swing[0].value.orelse) != "None"
                or not isinstance(swing[0].value.body, ast.Call)
                or ast.unparse(swing[0].value.body.func) != "_low"
                or ast.unparse(defaults["allows_completed_30s_trailing"]) != "True"
                or len(reducer_calls) != 1 or not any(key.arg == "allows_completed_30s_trailing"
                    and ast.unparse(key.value) == "self.contract.allows_completed_30s_trailing"
                    for key in reducer_calls[0].keywords)):
            raise ValueError("Strategy 5 must disable only the subsequent 30s-low stop branch")
    if strategy_number == 7:
        trailing = named(trees[0], "allows_completed_30s_trailing")
        manager = named(trees[6], "on_management")
        bound = [node for node in ast.walk(manager) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "advance_protection"]
        if (len(trailing.body) != 1
                or ast.unparse(trailing.body[0]) != 'return self.strategy_number not in (5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)'
                or len(bound) != 1 or not any(key.arg == "allows_completed_30s_trailing"
                    and ast.unparse(key.value) == "self.contract.allows_completed_30s_trailing"
                    for key in bound[0].keywords)):
            raise ValueError("Strategy 7 must restore the existing completed-low trailing branch")
    if strategy_number in (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        reducer = named(trees[7], "advance_protection")
        targets = [node for node in reducer.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "target_amendment" for target in node.targets)]
        defaults = dict(zip((arg.arg for arg in reducer.args.kwonlyargs), reducer.args.kw_defaults))
        bindings = [node for node in ast.walk(named(trees[6], "on_management"))
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "advance_protection"]
        if (len(targets) != 1 or not isinstance(targets[0].value, ast.IfExp)
                or ast.unparse(targets[0].value.test) != "price_bearing_bar and allows_target_escalation"
                or ast.unparse(targets[0].value.orelse) != "None"
                or not isinstance(targets[0].value.body, ast.Call)
                or ast.unparse(targets[0].value.body.func) != "ordinal_target"
                or defaults.get("allows_target_escalation") is None
                or ast.unparse(defaults["allows_target_escalation"]) != "True"
                or len(bindings) != 1 or not any(key.arg == "allows_target_escalation"
                    and ast.unparse(key.value) == "self.contract.allows_target_escalation"
                    for key in bindings[0].keywords)):
            raise ValueError("Strategy 6 must freeze only subsequent target escalation")
    if strategy_number in (8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30):
        cap = named(trees[0], "caps_entry_at_reference_ask")
        entry = named(trees[8], "strategy_one_entry_intent")
        envelopes = [node for node in ast.walk(entry) if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Name) and node.func.id == "ExecutionEnvelope"]
        policies = [node for node in ast.walk(entry) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name) and node.func.id == "ExecutionPolicy"]
        envelope_keys = {key.arg: ast.unparse(key.value) for key in envelopes[0].keywords} if len(envelopes) == 1 else {}
        policy_keys = {key.arg: ast.unparse(key.value) for key in policies[0].keywords} if len(policies) == 1 else {}
        if (len(cap.body) != 1 or ast.unparse(cap.body[0]) != 'return self.strategy_number in (8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)'
                or envelope_keys.get("maximum_buy_price") != "proposal.reference_ask if numbered_fixed_strategy(proposal.strategy_number).caps_entry_at_reference_ask else None"
                or envelope_keys.get("persist_until_cancelled") != "True"
                or policy_keys.get("partial_fill_policy") != "PartialFillPolicy.COMPLETE_REMAINDER"):
            raise ValueError("Strategy 8 must cap acquisition at original reference ask without changing persistence")
    intents = [node for node in ast.walk(intent) if isinstance(node, ast.Call)
               and isinstance(node.func, ast.Name) and node.func.id == "StrategyIntent"]
    keywords = {key.arg: ast.unparse(key.value) for key in intents[0].keywords} if len(intents) == 1 else {}
    if (keywords.get("action") != "'exit'" or keywords.get("metadata") != "{}"
            or keywords.get("reason") != "numbered_session_exit_reason(strategy_number)"):
        raise ValueError("Strategy 2 liquidation source is not a normalized scalar exit")
    return sha256(json.dumps({"strategy_number": strategy_number, "inventory": base, "followthrough": followthrough_proof, "entry_scope": entry_scope_proof, "early_failure": early_failure_proof, "recent_bos": recent_bos_proof, "rising_momentum": rising_momentum_proof,
        "identity": _certify_numbered_identity(), "session_exit_reason": certify_numbered_session_exit_reason_source(), "sources": tuple(
            sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()



def certify_early_followthrough_failure_v4_source(*, source_path: Path | None = None,
                                                management_path: Path | None = None) -> str:
    """Strategy 11 binds the inclusive minute bound to first held authority."""
    runtime_root = Path(__file__).parents[1] / "trading_runtime"
    paths = (source_path or runtime_root / "strategy_early_followthrough_failure.py",
             management_path or Path(__file__).with_name("backtest_strategy_one_management.py"),
             runtime_root / "arte_followthrough_failure_v4.py",
             runtime_root / "runtime.py")
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    trees = tuple(ast.parse(source) for source in sources)
    functions = [n for n in trees[0].body if isinstance(n, ast.FunctionDef)
                 and n.name == "early_followthrough_failure"]
    constants = [n for n in trees[0].body if isinstance(n, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "EARLY_FAILURE_WINDOW_MS" for target in n.targets)]
    if (len(functions) != 1 or len(constants) != 1
            or ast.unparse(constants[0].value) != "60000"):
        raise ValueError("Strategy 11 early failure bound changed")
    function = functions[0]
    substantive = [n for n in function.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str))]
    expected = ("witness = followthrough_failure(value)",
                "if witness is None or witness.boundary_ms - witness.first_held_boundary_ms > EARLY_FAILURE_WINDOW_MS:\n    return None",
                "return witness")
    if tuple(ast.unparse(n) for n in substantive) != expected:
        raise ValueError("Strategy 11 must preserve original failure conditions and inclusive first-held age")
    assignments = [n for n in ast.walk(trees[1]) if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "failure_rule" for t in n.targets)]
    if (len(assignments) != 1 or ast.unparse(assignments[0].value)
            != 'zero_regime_risk_failure if self.contract.strategy_number in (30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) else persistent_risk_failure if self.contract.strategy_number == 29 else premarket_quarter_risk_failure if self.contract.strategy_number in (25, 26, 27, 28) else early_followthrough_failure if self.contract.strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24) else followthrough_failure'):
        raise ValueError("Strategy 11 early failure predicate is not exclusively routed")
    numbered = [n for n in trees[2].body if isinstance(n, ast.FunctionDef)
                and n.name == "validate_numbered_failure"]
    if (len(numbered) != 1
            or "strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28)" not in ast.unparse(numbered[0])
            or "witness.boundary_ms - witness.first_held_boundary_ms > EARLY_FAILURE_WINDOW_MS" not in ast.unparse(numbered[0])):
        raise ValueError("Strategy 11 normalized witness must preserve its inclusive first-held bound")
    execute = [n for n in ast.walk(trees[3]) if isinstance(n, ast.AsyncFunctionDef)
               and n.name == "_execute_intents"]
    if (len(execute) != 1 or not any(isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "validate_numbered_failure"
            for n in ast.walk(execute[0]))):
        raise ValueError("Strategy 11 runtime must validate its numbered failure witness")
    return sha256(json.dumps(tuple((path.name, sha256(source.encode()).hexdigest())
        for path, source in zip(paths, sources)), separators=(",", ":")).encode()).hexdigest()


def certify_empty_exclusion_entry_scope_source(*, source_path: Path | None = None) -> str:
    """Bind Strategy 10 to read-only full certification before scope projection."""
    path = source_path or Path(__file__).with_name("backtest_strategy_one_entry_store.py")
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    scope = functions.get("_certify_empty_candidate_exclusion_scope")
    entry = functions.get("certify_entry_evidence_plan")
    if scope is None or entry is None:
        raise ValueError("Strategy 10 scoped entry source certificate is missing")
    def calls(node):
        return {n.func.id if isinstance(n.func, ast.Name) else n.func.attr
                for n in ast.walk(node) if isinstance(n, ast.Call)
                and isinstance(n.func, (ast.Name, ast.Attribute))}
    required = {"certify_candidate_plan", "exclude_candidate_tickers",
                "load_strategy_one_activations", "certify_hod_plan",
                "certify_entry_evidence_plan", "array_equal"}
    if (not required <= calls(scope)
            or "_certify_empty_candidate_exclusion_scope" not in calls(entry)
            or calls(scope) & {"execute", "insert", "materialize", "build"}):
        raise ValueError("Strategy 10 scope must certify original products and exact projection read-only")
    text = ast.unparse(scope)
    if ("row.candidate_count != 0" not in text
            or "original_activations.rows != activations.rows" not in text
            or "original_hod.contexts != hod.contexts" not in text
            or "strategy-one-empty-exclusion-entry-scope-v1" not in text
            or "original.token" not in text or "candidates.excluded_tickers" not in text):
        raise ValueError("Strategy 10 scope source lost its empty exclusion or original token guards")
    return sha256(source.encode()).hexdigest()


def certify_followthrough_failure_v4_source() -> str:
    """Bind the new rule, its routing and dedicated normalized durable source.

    This supplements the full emitter inventory; it never admits another
    generic event family merely because a similarly named table exists.
    """
    runtime_root = Path(__file__).parents[1] / "trading_runtime"
    paths = (runtime_root / "strategy_followthrough_failure.py",
             runtime_root / "strategy_followthrough_exit.py",
             runtime_root / "arte_followthrough_failure_v4.py",
             runtime_root / "runtime.py",
             Path(__file__).with_name("backtest_strategy_one_management.py"),
             runtime_root / "arte_journal_writer.py",
             runtime_root / "arte_journal_commit_v4.py",
             Path(__file__).with_name("backtest_typed_projection.py"),
             Path(__file__).with_name("backtest_typed_publisher.py"),
             Path(__file__).with_name("backtest_journal_memory.py"))
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    trees = tuple(ast.parse(source) for source in sources)
    def named(tree, name):
        found = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        if len(found) != 1:
            raise ValueError(f"Strategy 9 normalized followthrough source proof missing: {name}")
        return found[0]
    def calls(node):
        return {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
                for n in ast.walk(node) if isinstance(n, ast.Call)
                and isinstance(n.func, (ast.Name, ast.Attribute))}
    rule = named(trees[0], "followthrough_failure")
    rule_text = ast.unparse(rule)
    expected = ("value.boundary_ms % 5000", "value.completed_five_second_boundary_ms != value.boundary_ms",
                "value.boundary_ms - 5000 < value.first_held_boundary_ms",
                "0 <= value.quote_age_us <= 1000000", "value.macd_line >= value.macd_signal",
                "value.bid > threshold", "value.completed_five_second_close_int > threshold * 10000")
    if any(value not in rule_text for value in expected):
        raise ValueError("Strategy 9 completed failure predicate source changed")
    factory = named(trees[1], "followthrough_exit_intent")
    if "validate_witness" not in calls(factory):
        raise ValueError("Strategy 9 exit factory must revalidate its scalar witness")
    intents = [n for n in ast.walk(factory) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "StrategyIntent"]
    keys = {k.arg: ast.unparse(k.value) for k in intents[0].keywords} if len(intents) == 1 else {}
    if keys.get("metadata") != "{}" or keys.get("action") != "'exit'" or keys.get("reason") != "'strategy_nine_followthrough_failure'":
        raise ValueError("Strategy 9 exit must use a metadata-free normalized intent")
    management = named(trees[4], "on_management")
    selectors = [n for n in ast.walk(management) if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "failure_rule" for t in n.targets)]
    if (len(selectors) != 1 or ast.unparse(selectors[0].value)
            != 'zero_regime_risk_failure if self.contract.strategy_number in (30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) else persistent_risk_failure if self.contract.strategy_number == 29 else premarket_quarter_risk_failure if self.contract.strategy_number in (25, 26, 27, 28) else early_followthrough_failure if self.contract.strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24) else followthrough_failure'):
        raise ValueError("Original failure rule must remain routed exclusively to Strategy 9/10")
    submit = named(trees[3], "submit_followthrough_failure")
    execute = named(trees[3], "_execute_intents")
    if (not {"failure_rule", "submit_followthrough_failure"} <= calls(management)
            or not {"followthrough_exit_intent", "_execute_intents"} <= calls(submit)
            or "append_followthrough_exit" not in calls(execute)):
        raise ValueError("Strategy 9 normalized followthrough source proof routing is not wired")
    projection = named(trees[7], "project_pending_backtest_v4_prefix")
    publisher = named(trees[8], "_drain")
    source = named(trees[9], "append_followthrough_exit")
    if (not {"followthrough_exit_for_record", "project_followthrough_failure", "V4FollowThroughFailureBatch"} <= calls(projection)
            or "submit_followthrough_exit_v4" not in calls(publisher)
            or "validate_numbered_failure" not in calls(source)):
        raise ValueError("Strategy 9 normalized followthrough source proof publisher is not wired")
    project = named(trees[2], "project_followthrough_failure")
    seal = named(trees[2], "seal_followthrough_rows")
    if "validate_numbered_failure" not in calls(project) or not {"restore_failure", "_source_entry", "typed_row"} <= calls(seal):
        raise ValueError("Strategy 9 failure source must bind its typed original entry graph")
    numbered_validation = named(trees[2], "validate_numbered_failure")
    if "validate_witness" not in calls(numbered_validation):
        raise ValueError("Normalized numbered failure must preserve its original witness validation")
    named(trees[5], "submit_followthrough_exit_v4")
    if "seal_followthrough_rows" not in calls(trees[6]):
        raise ValueError("Strategy 9 durable commit lacks the witness seal")
    return sha256(json.dumps(tuple((path.name, sha256(source.encode()).hexdigest())
        for path, source in zip(paths, sources)), separators=(",", ":")).encode()).hexdigest()

_LEGACY_PROTECTION_FAMILIES = {
    ("order_management", "partial_target_completion"): "_complete_partial_target",
    ("order_management", "profit_pocket_transition"): "apply_profit_pocket_transition",
    ("order_management", "dynamic_stop_ratcheted"): "_ratchet_dynamic_protection",
    ("broker", "protected_exit_modified"): "_modify_existing_protected_exit",
    ("broker", "protected_sliced_exit_modified"): "_modify_existing_protected_exit",
    ("order_management", "entry_acquisition_frozen_before_exit"):
        "_cancel_pending_acquisition_before_exit",
}
_REDUNDANT_MODIFY_SUMMARIES = {
    ("broker", "profit_target_replaced"): "_replace_existing_profit_targets",
    ("broker", "protective_stop_replaced"): "_replace_protective_stop",
}


def certify_strategy_one_assignment_event_unreachable(
    *, controller_path: Path, runtime_path: Path,
    strategy_path: Path = _STRATEGY_ONE_RUNTIME,
    execution_path: Path = _STRATEGY_ONE_EXECUTION,
) -> str:
    """Bind the event-free fixed assignment save to the sparse-only call path.

    This excludes the legacy activity event, not the need to cold-recover the
    initial numbered assignment and its configuration from typed storage.
    """
    paths = (controller_path, runtime_path, strategy_path, execution_path)
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    controller, runtime, strategy, execution = map(ast.parse, sources)

    def method(tree: ast.Module, cls: str, name: str) -> ast.AST:
        owners = [node for node in tree.body if isinstance(node, ast.ClassDef)
                  and node.name == cls]
        matches = [node for node in owners[0].body
                   if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and node.name == name] if len(owners) == 1 else []
        if len(matches) != 1:
            raise ValueError(f"Strategy 1 assignment event route changed: {name}")
        return matches[0]

    assigned = [node for node in strategy.body if isinstance(node, ast.ClassDef)
                and node.name == "AssignedStrategyOne"]
    if (len(assigned) != 1 or any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in {"on_order_group_update", "on_market_signal"}
            for node in assigned[0].body)):
        raise ValueError("Strategy 1 gained a legacy assignment update handler")
    fixed = method(controller, "ReplayRunController", "_run_strategy_one_fixed_days")
    initialise = method(controller, "ReplayRunController", "_initialize_runtime")
    initial_saves = [node for node in ast.walk(initialise)
                     if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                     and node.func.attr == "persist_strategy_assignments"]
    if (len(initial_saves) != 1 or not any(keyword.arg == "record_events"
            and isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            for keyword in initial_saves[0].keywords)):
        raise ValueError("Strategy 1 initial assignment save can emit activity")
    runner = [node for node in ast.walk(fixed) if isinstance(node, ast.Call)
              and isinstance(node.func, ast.Name)
              and node.func.id == "run_certified_strategy_one_session"]
    session = [node for node in execution.body
               if isinstance(node, ast.AsyncFunctionDef)
               and node.name == "run_certified_strategy_one_session"]
    if (len(runner) != 1 or len(session) != 1
            or any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr in {"process_market_signal",
                                          "process_strategy_observation",
                                          "process_account_strategy_observation",
                                          "persist_strategy_assignments"}
                   for node in ast.walk(session[0]))):
        raise ValueError("Strategy 1 sparse runner can reach assignment activity")
    emitter = method(runtime, "TradingRuntime", "persist_strategy_assignments")
    if not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
               and node.func.attr == "append_many" for node in ast.walk(emitter)):
        raise ValueError("Assignment activity emitter changed")
    # Fill/state callbacks persist only if the strategy supplies this legacy
    # handler. The numbered strategy above deliberately does not.
    for name in ("_on_order_group_fill", "_on_order_group_state"):
        callback = method(runtime, "TradingRuntime", name)
        saves = [node for node in ast.walk(callback)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr == "_persist_strategy_assignments"]
        if len(saves) != 1 or not any(isinstance(node, ast.If)
                and ast.unparse(node.test) == "handler is not None"
                and saves[0] in ast.walk(node) for node in ast.walk(callback)):
            raise ValueError("Strategy 1 fill callback can emit assignment activity")
    return sha256(json.dumps({"version": 1, "sources": tuple(
        sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify_strategy_one_portfolio_request_unreachable(
    *, runtime_path: Path, portfolio_path: Path,
    contract_path: Path = _STRATEGY_ONE_CONTRACT,
) -> str:
    """Prove the legacy revision-41 deferred-request cleanup cannot run at 1."""
    paths = (runtime_path, portfolio_path, contract_path)
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    runtime, portfolio, contract = (ast.parse(source) for source in sources)
    numbers = [node.value.value for node in contract.body
               if isinstance(node, ast.Assign) and len(node.targets) == 1
               and isinstance(node.targets[0], ast.Name)
               and node.targets[0].id == "STRATEGY_NUMBER"
               and isinstance(node.value, ast.Constant)]
    if numbers != [1]:
        raise ValueError("Strategy 1 number no longer precedes deferred-request cleanup")
    runtime_classes = [node for node in runtime.body if isinstance(node, ast.ClassDef)
                       and node.name == "TradingRuntime"]
    portfolio_classes = [node for node in portfolio.body if isinstance(node, ast.ClassDef)
                         and node.name == "PortfolioManagementEngine"]
    if len(runtime_classes) != 1 or len(portfolio_classes) != 1:
        raise ValueError("Portfolio request source owners changed")
    execute = [node for node in runtime_classes[0].body
               if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute_intents"]
    withdraw = [node for node in portfolio_classes[0].body
                if isinstance(node, ast.FunctionDef)
                and node.name == "withdraw_invalidated_requests"]
    calls = [node for node in ast.walk(runtime)
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
             and node.func.attr == "withdraw_invalidated_requests"]
    emitters = [node for node in ast.walk(portfolio)
                if isinstance(node, ast.Constant) and node.value == "portfolio_request"]
    if (len(execute) != 1 or len(withdraw) != 1 or len(calls) != 1
            or calls[0] not in ast.walk(execute[0])
            or len(emitters) != 1 or emitters[0] not in ast.walk(withdraw[0])):
        raise ValueError("Portfolio request has another or missing route")
    guards = [node for node in ast.walk(execute[0]) if isinstance(node, ast.If)
              and calls[0] in ast.walk(node)]
    if (len(guards) != 1 or ast.unparse(guards[0].test) !=
            "self.config.strategy_id != 'early-squeeze-strategy' and "
            "self.config.strategy_revision >= 41 and hasattr(self.strategy, "
            "'assignments') and self.portfolio.has_pending_entry_requests(account_id)"):
        raise ValueError("Portfolio request is not revision-41 guarded")
    return sha256(json.dumps({"version": 1, "sources": tuple(
        sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify_strategy_one_legacy_protection_unreachable(
    *, oms_path: Path, runtime_path: Path,
    contract_path: Path = _STRATEGY_ONE_CONTRACT,
) -> str:
    """Prove the numbered strategy bypasses legacy OMS protection and exits."""
    paths = (oms_path, runtime_path, contract_path)
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    oms, runtime, contract = (ast.parse(source) for source in sources)
    contract_values = {node.targets[0].id: node.value.value
                       for node in contract.body if isinstance(node, ast.Assign)
                       and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                       and isinstance(node.value, ast.Constant)
                       and node.targets[0].id in {"STRATEGY_ID", "STRATEGY_NUMBER"}}
    if contract_values != {"STRATEGY_ID": "early-squeeze-strategy", "STRATEGY_NUMBER": 1}:
        raise ValueError("Strategy 1 legacy protection identity changed")
    oms_classes = [node for node in oms.body if isinstance(node, ast.ClassDef)
                   and node.name == "OrderManagementEngine"]
    runtime_classes = [node for node in runtime.body if isinstance(node, ast.ClassDef)
                       and node.name == "TradingRuntime"]
    if len(oms_classes) != 1 or len(runtime_classes) != 1:
        raise ValueError("Strategy 1 OMS construction identity changed")
    imports = [node for node in oms.body if isinstance(node, ast.ImportFrom)
               and node.module == "src.trading_runtime.strategy_one_contract"]
    initializers = [node for node in oms_classes[0].body
                    if isinstance(node, ast.FunctionDef) and node.name == "__init__"]
    if (len(imports) != 1 or {alias.name for alias in imports[0].names}
            != {"STRATEGY_ID", "STRATEGY_NUMBER"} or len(initializers) != 1
            or any(sum(isinstance(node, ast.Assign)
                       and ast.unparse(node) == f"self.{field} = {field}"
                       for node in ast.walk(initializers[0])) != 1
                   for field in ("strategy_id", "strategy_revision"))):
        raise ValueError("Strategy 1 OMS identity binding changed")
    constructors = [node for node in ast.walk(runtime_classes[0])
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "OrderManagementEngine"]
    if len(constructors) != 1:
        raise ValueError("Strategy 1 OMS constructor changed")
    keyword_values = {keyword.arg: ast.unparse(keyword.value)
                      for keyword in constructors[0].keywords}
    if (keyword_values.get("strategy_id") != "config.strategy_id"
            or keyword_values.get("strategy_revision") != "config.strategy_revision"):
        raise ValueError("Strategy 1 identity is not forwarded to OMS")
    numbered_proof = _certify_numbered_identity()
    guard = "if is_numbered_fixed_strategy(self.strategy_id, self.strategy_revision):"
    returns = {
        "_complete_partial_target": "return False",
        "apply_profit_pocket_transition": "return []",
        "_ratchet_dynamic_protection": "return",
        "_modify_existing_protected_exit": "return None",
        "_cancel_pending_acquisition_before_exit": "return",
    }
    for family, name in _LEGACY_PROTECTION_FAMILIES.items():
        methods = [node for node in oms_classes[0].body
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == name]
        emitters = [node for node in ast.walk(oms)
                    if isinstance(node, ast.Constant) and node.value == family[1]]
        body = methods[0].body if len(methods) == 1 else []
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            body = body[1:]
        if (len(methods) != 1 or len(emitters) != 1
                or emitters[0] not in ast.walk(methods[0])
                or not body or not isinstance(body[0], ast.If)
                or ast.unparse(body[0]).split("\n", 1)[0] != guard
                or len(body[0].body) != 1
                or ast.unparse(body[0].body[0]) != returns[name]):
            raise ValueError(f"Strategy 1 legacy OMS event may be reachable: {family}")
    for family, name in _REDUNDANT_MODIFY_SUMMARIES.items():
        methods = [node for node in oms_classes[0].body
                   if isinstance(node, ast.AsyncFunctionDef) and node.name == name]
        if len(methods) != 1:
            raise ValueError("Strategy 1 modification route changed")
        method = methods[0]
        emitters = [node for node in ast.walk(oms)
                    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_record" and len(node.args) >= 2
                    and all(isinstance(arg, ast.Constant) for arg in node.args[:2])
                    and tuple(arg.value for arg in node.args[:2]) == family]
        guards = [node for node in ast.walk(method) if isinstance(node, ast.If)
                  and len(emitters) == 1 and emitters[0] in ast.walk(node)]
        acknowledgements = [node for node in ast.walk(method)
                            if isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)
                            and node.func.attr ==
                            "_record_strategy_one_modify_acknowledgement"]
        effective = [node for node in ast.walk(method)
                     if isinstance(node, ast.Call)
                     and isinstance(node.func, ast.Attribute)
                     and node.func.attr == "_record_protection"
                     and any(keyword.arg == "phase"
                             and isinstance(keyword.value, ast.Constant)
                             and keyword.value.value == "effective"
                             for keyword in node.keywords)
                     and any(keyword.arg == "amendment_intent"
                             and isinstance(keyword.value, ast.Name)
                             and keyword.value.id == "intent"
                             for keyword in node.keywords)]
        if (len(emitters) != 1 or len(guards) != 1
                or ast.unparse(guards[0].test) !=
                "not is_numbered_fixed_strategy(self.strategy_id, self.strategy_revision)"
                or len(acknowledgements) != 1 or len(effective) != 1
                or not acknowledgements[0].lineno < effective[0].lineno
                       < emitters[0].lineno):
            raise ValueError(f"Strategy 1 modification summary may be reachable: {family}")
    return sha256(json.dumps({"version": 1, "numbered_identity": numbered_proof, "sources": tuple(
        sha256(source.encode()).hexdigest() for source in sources)},
        sort_keys=True, separators=(",", ":")).encode()).hexdigest()


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
        "    from .strategy_one_intent import require_no_replacement_capital, "
        "require_strategy_one_actions\n"
        "    require_no_replacement_capital(evaluation.intents)\n"
        "    require_strategy_one_actions(evaluation.intents)"
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



_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES = ('src/backend/backtest_fixed_journal_bootstrap.py', 'src/backend/backtest_trade_proposal_v3.py', 'src/backend/backtest_typed_projection.py', 'src/backend/backtest_typed_publisher.py', 'src/backend/replay_run_service.py', 'src/trading_runtime/arte_trade_proposal_children.py', 'src/trading_runtime/arte_trade_proposal_projection.py', 'src/trading_runtime/drawdown_measure_authority.py', 'src/trading_runtime/drawdown_measure_policy.py', 'src/trading_runtime/numbered_fixed_strategy.py', 'src/trading_runtime/portfolio.py', 'src/trading_runtime/risk_supervisor.py', 'src/trading_runtime/runtime.py', 'src/trading_runtime/journal_contract.py', 'src/trading_runtime/arte_journal_projection.py', 'src/trading_runtime/arte_journal_writer.py', 'src/trading_runtime/arte_journal_reader.py', 'src/trading_runtime/arte_portfolio_snapshot.py', 'src/backend/backtest_strategy_one_configuration.py', 'src/trading_runtime/strategy_registry.py', 'src/backend/source_ast_summary.py', 'src/backend/backtest_fixed_v4_certification.py', 'src/trading_runtime/columnar_program_coordinator.py', 'src/trading_runtime/columnar_strategy_release.py', 'src/trading_runtime/columnar_strategy_schema.py', 'src/trading_runtime/columnar_rule_program.py', 'src/trading_runtime/filled_position_state.py', 'src/trading_runtime/optimized_program_specification.py', 'src/trading_runtime/optimized_strategy_reference.py', 'src/market_engine/volume_rank_rules.py', 'src/trading_runtime/columnar_program_actor.py', 'src/trading_runtime/order_management.py', 'src/trading_runtime/program_target_state.py', 'src/trading_runtime/oms_assignment_observation.py', 'src/backend/backtest_program_inputs.py', 'src/backend/qmd_columnar_history.py', 'src/backend/qmd_columnar_history_transport.py', 'src/backend/signal_dispatch_recovery.py', 'src/backend/signal_dispatch_registered_writer.py', 'src/backend/signal_dispatch_insert_dispatch.py', 'src/backend/signal_dispatch_clickhouse_storage.py', 'src/backend/signal_dispatch_typed_cursor.py', 'src/backend/signal_dispatch_registered_publication.py', 'src/backend/signal_stream_runtime_service.py', 'src/backend/signal_stream_typed_readback.py', 'src/backend/signal_stream_typed_publication.py', 'src/backend/signal_stream_typed_cursor.py', 'src/backend/signal_stream_typed_catalog.py', 'src/backend/signal_stream_typed_occurrence.py', 'src/market_engine/columnar_history_product.py', 'src/trading_runtime/stop_only_management.py', 'src/trading_runtime/program_trail_state.py', 'src/backend/qmd_columnar_population.py', 'src/trading_runtime/arte_activation_insert_dispatch.py', 'src/trading_runtime/arte_registered_activation_writer.py', 'src/backend/live_activation_dispatch_admission.py', 'src/backend/program_policy_recovery.py', 'src/trading_runtime/arte_journal_commit_v4.py', 'src/trading_runtime/arte_portfolio_recovery.py', 'src/backend/backtest_v4_keeper_lease.py', 'src/backend/program_runtime_recovery.py', 'src/backend/program_financial_recovery.py', 'src/backend/program_broker_recovery.py', 'src/backend/program_oms_recovery.py', 'src/trading_runtime/program_oms_admission.py', 'src/trading_runtime/program_oms_lineage.py', 'src/trading_runtime/rule_decision_basis_projection.py', 'src/trading_runtime/rule_decision_basis_schema.py', 'src/trading_runtime/arte_oms_projection.py', 'src/backend/backtest_v4_execution_restore.py', 'src/backend/rule_decision_reselection.py', 'src/trading_runtime/rule_decision_publication.py', 'src/trading_runtime/rule_decision_compound.py', 'src/trading_runtime/rule_decision_writer_preflight.py', 'src/trading_runtime/rule_decision_single_publication.py', 'src/backend/backtest_program_publication.py', 'src/trading_runtime/rule_decision_commit_graph.py', 'src/backend/rule_decision_recovery.py', 'src/backend/backtest_program_clock.py', 'src/backend/program_clock_financial_basis.py', 'src/trading_runtime/arte_oms_actor_restore.py', 'src/backend/backtest_liquidity_resolution.py', 'src/backend/backtest_program_cohort.py', 'src/trading_runtime/trading_price_codec.py', 'src/trading_runtime/trading_price_contracts.py', 'src/trading_runtime/trading_price_rows.py', 'src/trading_runtime/arte_oms_exact_prices.py', 'src/trading_runtime/arte_order_exact_prices.py', 'src/trading_runtime/arte_entry_acquisition_freeze.py', 'src/trading_runtime/backtest_journal_price_contract.py', 'src/trading_runtime/arte_backtest_definition.py', 'src/backend/backtest_v4_run_context.py', 'src/backend/backtest_program_journal_bootstrap.py', 'src/trading_runtime/backtest_broker_contract.py', 'src/backend/backtest_program_journal_startup.py', 'src/backend/backtest_program_session.py', 'src/backend/backtest_program_terminal.py', 'src/trading_runtime/columnar_strategy_reader.py', 'src/backend/backtest_program_launch.py', 'src/trading_runtime/arte_broker_match_snapshot.py', 'src/trading_runtime/strategy_one_broker_match_snapshot.py', 'src/trading_runtime/arte_typed_insert_dispatch.py', 'src/backend/backtest_program_cursor.py', 'src/backend/backtest_program_checkpoint.py', 'src/trading_runtime/arte_oms_observation_snapshot.py', 'src/trading_runtime/strategy_one_oms_observation_snapshot.py', 'src/backend/program_checkpoint_recovery.py', 'src/trading_runtime/arte_execution_exact_prices.py', 'src/backend/certified_program_configuration.py', 'src/backend/strategy_configuration_reader.py', 'src/backend/strategy_configuration_materialization.py', 'src/trading_runtime/strategy_configuration_selection.py', 'src/trading_runtime/strategy_configuration_schema.py', 'src/trading_runtime/arte_portfolio_policy.py', 'src/backend/backtest_program_feature_cache.py', 'src/trading_runtime/strategy_signal_configuration_schema.py', 'src/backend/strategy_signal_configuration.py', 'src/backend/backtest_program_listing_cache.py', 'src/backend/program_configuration_selection_service.py', 'src/backend/backtest_program_input_plan.py', 'src/backend/program_app_preparation.py', 'src/backend/program_recovery_evidence.py', 'src/backend/program_saved_definition.py', 'src/backend/backtest_fixed_running_anchor.py', 'src/backend/program_controller_recovery.py', 'src/backend/filled_session_measures.py', 'src/trading_runtime/rule_decision_observation.py', 'src/backend/recorded_program_comparison.py', 'src/backend/backtest_program_liquidity_scope.py', 'src/trading_runtime/ibkr_schema.py', 'src/backend/program_managed_journal.py', 'src/backend/backtest_journal_clickhouse.py', 'src/backend/program_app_handoff.py', 'src/backend/program_app_startup.py', 'src/backend/backtest_program_population_cache.py', 'src/backend/strategy_configuration_publication.py', 'src/trading_runtime/configuration_execution_binding.py', 'src/backend/configuration_execution_binding_reader.py', 'src/backend/configuration_execution_binding_publication.py', 'src/backend/strategy_configuration_transport.py', 'src/backend/configuration_execution_binding_transport.py', 'src/backend/program_app_bootstrap.py', 'src/trading_runtime/program_stop_protection.py', 'src/backend/application_registry.py', 'src/backend/qmd_feature_source_plan.py', 'src/market_engine/qmd_feature_catalog.py', 'src/market_engine/causal_context_features.py', 'src/market_engine/causal_market_context.py', 'src/market_engine/continuous_indicator_features.py', 'src/market_engine/candle_feature_projection.py', 'src/market_engine/feature_share_basis.py', 'src/market_engine/feature_source_plan.py', 'src/market_engine/qmd_feature_projection.py', 'src/backend/backtest_strategy_one_identity.py', 'src/backend/optimized_execution_audit.py', 'src/backend/optimized_execution_contract.py', 'src/backend/optimized_strategy_import.py', 'src/trading_runtime/columnar_program_input_contract.py', 'src/trading_runtime/configuration_execution_risk.py', 'src/trading_runtime/optimized_candidate.py', 'src/trading_runtime/optimized_candidate_evidence.py', 'src/backend/program_runtime_assembly.py', 'src/backend/program_runtime_decision.py', 'src/backend/program_boundary_dispatch.py', 'src/backend/program_execution_journal.py', 'src/backend/program_pending_publication.py', 'src/backend/program_broker_executions.py', 'src/backend/program_v4_principal.py', 'src/backend/live_strategy_runtime_service.py', 'src/backend/live_strategy_one_v4_principal.py', 'src/backend/program_app_credentials.py', 'src/backend/managed_backtest_credentials.py', 'src/backend/program_app_storage.py', 'src/backend/program_managed_configuration.py', 'src/trading_runtime/program_broker_gateway.py', 'src/trading_runtime/program_portfolio_journal.py', 'src/trading_runtime/program_portfolio_transitions.py', 'src/trading_runtime/arte_broker_policy_v4.py', 'src/trading_runtime/arte_portfolio_control_v4.py', 'src/trading_runtime/arte_command_dispatcher.py', 'src/trading_runtime/arte_journal_compound_v4.py', 'src/trading_runtime/arte_portfolio_sync.py', 'src/trading_runtime/keeper_ownership.py', 'src/backend/program_signal_source.py', 'src/backend/signal_execution_context.py', 'src/backend/signal_context_schema.py', 'src/backend/signal_context_transport.py', 'src/backend/signal_typed_json_wire.py', 'src/backend/signal_source_clickhouse_storage.py', 'src/backend/signal_source_registered_storage.py', 'src/backend/signal_source_insert_dispatch.py', 'src/backend/signal_stream_session_head.py', 'src/backend/signal_stream_typed_attestation.py', 'src/backend/live_signal_source_cursor_proof.py', 'src/backend/backtest_market_data.py', 'src/backend/backtest_liquidity_price.py', 'src/backend/qmd_volume_reader.py', 'src/backend/qmd_opening_reference.py', 'src/trading_runtime/arte_market_day_cold_preflight.py', 'src/trading_runtime/arte_market_day_keeper.py', 'src/market_engine/qmd_volume_product.py', 'src/market_engine/typed_product_insert.py', 'src/market_engine/causal_market_history.py', 'src/market_engine/causal_volume_history.py', 'pipelines/market_sip/events/program_input_publication.py', 'pipelines/market_sip/events/qmd_feature_preparation.py', 'pipelines/market_sip/events/qmd_feature_reference.py', 'pipelines/market_sip/events/qmd_volume_producer.py', 'pipelines/market_sip/events/columnar_history_producer.py', 'pipelines/market_sip/events/opening_price_reference_producer.py', 'pipelines/market_sip/events/regular_close_history_producer.py', 'pipelines/market_sip/events/liquidity_resolution_producer.py', 'pipelines/market_sip/events/liquidity_execution_price_producer.py', 'pipelines/market_sip/events/shared_reference_listing_basis.py', 'src/backend/optimized_audit_run.py', 'src/backend/optimized_audit_collection.py', 'research/vectorized_backtest/v19/audit_reference.py', 'scripts/audit_optimized_strategy.py', 'src/trading_runtime/strategy_orders.py', 'src/trading_runtime/arte_portfolio_reconciliation_v4.py', 'src/backend/backtest_reconciliation_v3.py', 'src/backend/completed_signal_cursor_reader.py', 'src/trading_runtime/simulated_broker.py', 'src/backend/program_terminal_financial_rollforward.py', 'src/backend/program_terminal_portfolio_rollforward.py', 'src/backend/program_terminal_checkpoint_publication.py', 'src/trading_runtime/entry_cash_budget.py')
_DRAWDOWN_CORE_REVIEWED_AST = {'src/backend/backtest_fixed_journal_bootstrap.py': '5776fb909978a01e2d6f5931b654d8fcc0136d6976aa35496e7846ce341ee826', 'src/backend/backtest_trade_proposal_v3.py': '105c0861f4f64ca8a472a6df5eab6b757672c2d372a486eea381b25a26bfb986', 'src/backend/backtest_typed_projection.py': '3fb6f7845c370fc8d2e5e0cc0fe26716d39469bd9ef5d9f19ce38a8654cb2f87', 'src/backend/backtest_typed_publisher.py': '81a587ff0e16ae870ae6fe1a789f944206e6ece6357934eb486a53aadc01b4d2', 'src/backend/replay_run_service.py': '744517e779b7842af6cc74487f990973c26eb04d1682bfa5950c438ed00a7588', 'src/trading_runtime/arte_trade_proposal_children.py': 'c306edb3cdf3ac0a1dd0935c15b305fed4e52736794cbaac353d86245ed41272', 'src/trading_runtime/arte_trade_proposal_projection.py': '3db1d832a3f703a011c97d399e7cc725143201955674fd8d2aba61b796c11d55', 'src/trading_runtime/drawdown_measure_authority.py': '5c3e9281d3ef17eeeb9e75e9847b30de5619aef598b85c2d0e687ed2befbf87d', 'src/trading_runtime/drawdown_measure_policy.py': '8db141b2fd01030439e5c6c9a4040a6a3462183ae7575abfe3fcd17c1f7d8f96', 'src/trading_runtime/numbered_fixed_strategy.py': '26510f4c0bd533119668301f06f38ccab047e8b835e58ce79efc14a68b7b9326', 'src/trading_runtime/portfolio.py': '0ebb058cbbe7cff194279286519ca36378f63b9ec13c9b825b27feef0787a6f3', 'src/trading_runtime/risk_supervisor.py': '7bceea510b1cf125f076b0b283049f51c7672045651b19b4583138e5f920a285', 'src/trading_runtime/runtime.py': '287d341d5227b3371c0f7d911aa07a43fba5d441aacb495a691bd1b5c71d7c55', 'src/trading_runtime/journal_contract.py': '130e8b70f3708cd30f0524b035a0c99cded407bf6b1fc39a610cb7e19458da28', 'src/trading_runtime/arte_journal_projection.py': '230f0c6f75912d2aefbff8421c2a7b5a2e78696a4c78190e53be68e385722828', 'src/trading_runtime/arte_journal_writer.py': '53eca44d8fab32c89a8d1fa5cb714cca8f9f07c32f0306a854b786eac80a2e5c', 'src/trading_runtime/arte_journal_reader.py': '24ec5c6b95def84d9acfc36129784147ab30db6c97ce7047f277b90d3a473ab3', 'src/trading_runtime/arte_portfolio_snapshot.py': 'd81220e345e29d2891d69c7554c90fb59e188fcaf5eca61d3991c711241fa390', 'src/backend/backtest_strategy_one_configuration.py': 'e7ac8ac056735d57086db293f47d915a7a987cf0e0e91d428e7117e494399f3f', 'src/trading_runtime/strategy_registry.py': 'b63d1facde4fc0598b421da6ae67f84f0c0f12432ee19229cf5868490e7ac064', 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995', 'src/backend/backtest_fixed_v4_certification.py': {'certify_drawdown_measure_core_source': 'a737fb7029a91aa60cee6e95cbbf9d4214f5ef7fbcf3755f328c8bac5244df45', 'certify_strategy_one_v4_projection': '5a07632380270c1795ef9331e80ddc014599aac0bdd1e80d835f862c72e05747'}, 'src/trading_runtime/columnar_program_coordinator.py': 'a8e121b2b23bec6ee756406507f326c09766657ee0c91138217162bfb9ec7a91', 'src/trading_runtime/columnar_strategy_release.py': '9d47f3f6d208de059fbde962c0157da95d54482f7bfc1601c67f2055f9522fb4', 'src/trading_runtime/columnar_strategy_schema.py': '76d68f1187a2d558cf3ad47662dc4bdc1b889af3f98ea7c361520708716b18c2', 'src/trading_runtime/columnar_rule_program.py': 'b39c6c06563dc344c7aa4d8ca738ca857bfcdc88df8e7504e5d25c7a6abfdcdc', 'src/trading_runtime/filled_position_state.py': 'c773fd312e647407c750bcd9eafe3bbec5f11ac0bf9526006cb0207381ec664e', 'src/trading_runtime/optimized_program_specification.py': '3cc479329b508b46b8199fc1e2447d189bad25accc72adff5d7a2a3800d229d3', 'src/trading_runtime/optimized_strategy_reference.py': '485aeff2ad10dd1ee3e708571f889203166ea05fb792b2507e3dca543fa0afe3', 'src/market_engine/volume_rank_rules.py': '1f9051c99800cc9896bc6a15284f379f106c8544b1dea16871d7531b6b822b35', 'src/trading_runtime/columnar_program_actor.py': 'd2e6aa0185b6d18f5e4ca81bdfef4f5ebd4ffea267257379fc74b9bde913f86d', 'src/trading_runtime/order_management.py': 'fdde27d4c4e4ca03839fa283ef57f68f11888f7f346bf82ebd871faf3c74daae', 'src/trading_runtime/program_target_state.py': '783d045ad982ef1590b52304f28944a39ae7d9184afca76a102b3bfb4267e7e9', 'src/trading_runtime/oms_assignment_observation.py': 'e85ae1020ea8acc626741f4651d6ea3e655ad5208707b5940160f6a12559d367', 'src/backend/backtest_program_inputs.py': '5c3df91b4c0a585392f5031a5eb0f12cbfa4efd479ddd9f6507af1173bd1af0a', 'src/backend/qmd_columnar_history.py': 'e35d61a19df1dce332bc7f51e58e2ef8a55612df20e4a710159d33a1305818f6', 'src/backend/qmd_columnar_history_transport.py': 'e7ed683cd5eea167d29c1ba57d58af6b23b4cb66da4560d062a740779ffc3605', 'src/backend/signal_dispatch_recovery.py': 'fca587601c61fc069fbda30a226ed8b6a0e2d1b1713ea54eb87fa04f4b08d5c4', 'src/backend/signal_dispatch_registered_writer.py': '919928771b859c7ccd40427fdf430442c5c4c0a0415b003213a347061c91df1f', 'src/backend/signal_dispatch_insert_dispatch.py': '846b9389485c4689946670dbb0ca9c318d479d0da5b54c7d4172ef07286e479a', 'src/backend/signal_dispatch_clickhouse_storage.py': 'd2f6dcad309f89706b0625c6c8cc26e85d26c64f76d2c0b53209acbb370f26f0', 'src/backend/signal_dispatch_typed_cursor.py': '3dadbe18b2de2e559c56f410b26cc8c045bd869e0d17f9f73092de280c072f01', 'src/backend/signal_dispatch_registered_publication.py': '11c39123d02c9eeee219b96ebea9411efdd46bd0502bd34ce42bcfc6d5a9bbf6', 'src/backend/signal_stream_runtime_service.py': 'a4644ecf42bf27e5a1911fcc62460636d2d676f24bd2f3fcdc3bc6a42f67a490', 'src/backend/signal_stream_typed_readback.py': '21b90b01c52e2892aceff1d0aab7a907324b58109d3a6582b1efecdd82014c4e', 'src/backend/signal_stream_typed_publication.py': '41d6a1bd17c3aad1d65b7c4dd9cb0af8d020bc52df5e2e9f89fd8df1d7662217', 'src/backend/signal_stream_typed_cursor.py': '3e4d8001ad7aa75400fedf041ecf3e3c6ff413b8b2c9207dc836a6ec74106f7f', 'src/backend/signal_stream_typed_catalog.py': '8a49c6d7b63a929ede883e7c0a441784d444f509755691827f53942e7f6e8fbe', 'src/backend/signal_stream_typed_occurrence.py': 'fcd73d1ebaf95da7570118e6d64fa0859799b067ce33cf9927e5a0cfd4cddca8', 'src/market_engine/columnar_history_product.py': '72f5d56629261f8cfa74e1bf8eb294a39b1b4688b7ba8a0b731a913ba202c669', 'src/trading_runtime/stop_only_management.py': '36268c65e9736816103fb0a40c75a4dbc2d65af7d415d5094717331d078215ce', 'src/trading_runtime/program_trail_state.py': '42556e2462cc138e9c4ce113a8cb1435514d354f58996e1d0b63086095ba821e', 'src/backend/qmd_columnar_population.py': '41fe3ab13443cd50d8596acb8a7cf599a1c9a31bdf3586420eb973764a4094af', 'src/trading_runtime/arte_activation_insert_dispatch.py': 'c510c17975af0dfb0e1a0532c943c0077b33c790709228188e5dc2c57c674040', 'src/trading_runtime/arte_registered_activation_writer.py': '45700ad4d8089ea19d86558f1e899e0a71b95f8a8cd347209b1acdf0ff4d4530', 'src/backend/live_activation_dispatch_admission.py': 'dc575154d5c67c17e62ffee711464f2139260ef9b6bc1f2e1392ff1a4092c2d6', 'src/backend/program_policy_recovery.py': '07374d130ed0fa4d4e34d7693fd32b0b4ca9cabc2f3b1fb33266c55bae53d23b', 'src/trading_runtime/arte_journal_commit_v4.py': 'd5a3a25a797090579f5a233d48e1ac60fa3eaeb888500e99cee52ae96fd53e04', 'src/trading_runtime/arte_portfolio_recovery.py': '877366e30f0d35c726d843e5d2f5b0431f0ea88fdf9ff373dc6dcbaf2d3bdc58', 'src/backend/backtest_v4_keeper_lease.py': '7f3a0f228d1df853b9b2bf58d640eccc37b80aa7efda17f4e878b5d2614e4637', 'src/backend/program_runtime_recovery.py': '85ff70bf82e3299d5ecc35c89fe8337a11fe48753c8df27da8f654cb8c644d4b', 'src/backend/program_financial_recovery.py': '494566332e3b6a12157546c55c80e462038f01d1d6e7d852f634896b60d64ba0', 'src/backend/program_broker_recovery.py': '3b10eb091c537af3715e11fb0946d75ea5f1dba914c4cbdb918599e99420403e', 'src/backend/program_oms_recovery.py': '330d06cb7c9ce57f1d1e477409df378cc8db8f7f863d9e3ca031d8c3063a4d17', 'src/trading_runtime/program_oms_admission.py': 'eee00958fa2d5ad09e01942f53424433cf405f000800d4b9d25278888d8b014f', 'src/trading_runtime/program_oms_lineage.py': '1d28e728f6a0cc387f3912f7a037c10bf14bbbb00c9bef8ec6f1d169807141c6', 'src/trading_runtime/rule_decision_basis_projection.py': 'b8c4ff8174c4e12de7c464d5a58a4b1f6f34c66b5c145e3578dfcc2443f81f32', 'src/trading_runtime/rule_decision_basis_schema.py': '474eccf826669b68af0149468125d7106386a7d01b454f37c25ccf7c2cc37026', 'src/trading_runtime/arte_oms_projection.py': 'ba7c1d8232c7a1b44d3e565cb229c29af619ffaa13fe40216e058f1b899fce57', 'src/backend/backtest_v4_execution_restore.py': '13f65aa58ba63e66b5c9e8aba703de6c865aea4640921c58a2e124700ef148fe', 'src/backend/rule_decision_reselection.py': '8fd36605a01e08d0427ffd867f31d5c5ff04fb5887ca6609959abae3b7587cc9', 'src/trading_runtime/rule_decision_publication.py': 'f079360e9d685a56dfc9fcacc19a90ab0c3e7b8e3641bd23000c2b532ea752a0', 'src/trading_runtime/rule_decision_compound.py': '4e6646004c020bb689bee7a6b5f30f4cd3c8d56008b108306aa1e530f6361114', 'src/trading_runtime/rule_decision_writer_preflight.py': 'fc3690c1c0a245c9ec316a5643134e2263aa122cf94777c37503f6549c106675', 'src/trading_runtime/rule_decision_single_publication.py': 'b493e142c17d605e13a4afb23b327d6dcd67b2dbbf3939e15ee953388fecb506', 'src/backend/backtest_program_publication.py': 'd6a1066f4d0738c484c230edb5df5b61065bea9e50010d1c45a1efc70f1fbfa5', 'src/trading_runtime/rule_decision_commit_graph.py': '02452d1a26f631a5a4a1bbc56f4ec86d20aeb9ad0d5cb25ed2715519457b759f', 'src/backend/rule_decision_recovery.py': 'db5142b97e47adc98c5d282f4fc3af5022aca7ff6275fa3afa63ec206a5f89c6', 'src/backend/backtest_program_clock.py': 'ab203b510833e57629aab2edc90003bf237cd1726071e55858390801f79877e2', 'src/backend/program_clock_financial_basis.py': '1616062fa1e6e0abb9f812731b12c379ec48935ca9880f24a17d4fdce063fa47', 'src/trading_runtime/arte_oms_actor_restore.py': '0a5b3b112c142c71ddac5c081941f2c76672358aa0e15775a8447a3d48c672a8', 'src/backend/backtest_liquidity_resolution.py': 'b7bc6392a86c1fadddadc4b46ce2020535b17171f80af2b648fb10f2b19f572f', 'src/backend/backtest_program_cohort.py': 'd63cb38694b55fceda96d9f7954b6ad08df889471b6a86e898c2956699e05ad6', 'src/trading_runtime/trading_price_codec.py': '4060f05f46c372b715bc15514085b83cb81e1c781f5a8ad10eccb562e1443d29', 'src/trading_runtime/trading_price_contracts.py': 'e19357a8d3e0e244abbbc2654199b48a5e20cd65051fb4f5ee431b849ad6c02c', 'src/trading_runtime/trading_price_rows.py': '9c124ef4634f9802d8b3cfb3c06875972152fe92d1b7349a7b3ecbf7e448b6da', 'src/trading_runtime/arte_oms_exact_prices.py': 'b8b8015d803e324d87d1c08800b05de71b87679980bb087e7a94b8883012a28f', 'src/trading_runtime/arte_order_exact_prices.py': '7e31f2b6bebf895439545edb1b93e50c0b614d469f466578bdca71b375114ea3', 'src/trading_runtime/arte_entry_acquisition_freeze.py': 'b9fc899ff208e4e1d074c9a34df3452a6953210260ac2bad2501a4ad816c6cc0', 'src/trading_runtime/backtest_journal_price_contract.py': '31d9261b76ad4a6d182cadb29de020cb5dd8ece2d2bd2e22f042d74bdd54e880', 'src/trading_runtime/arte_backtest_definition.py': '0f1b925710f45d88f7ee6eca7f5f72247967dc1e7ef645764aec46e30ea6e553', 'src/backend/backtest_v4_run_context.py': 'bf973d123f85fe03d94949a46261fb1ba654936060a9b6a40eb7f893af3c6cf9', 'src/backend/backtest_program_journal_bootstrap.py': '41f56c73cb374aed1c966008de7b3cacd59bc309769aaf498529787e2e97ec2b', 'src/trading_runtime/backtest_broker_contract.py': '98206719a9aa44624e30a2c595d83d74ab679af461269e91a0e4a183cc40eeb1', 'src/backend/backtest_program_journal_startup.py': '166d889d0e3ea31f2a6b710dbcb1cc782ace25760be42c4d2cb42d41330b147b', 'src/backend/backtest_program_session.py': 'ca32817c0bab39685ed1279c09ad6c356cd177072926568ea8dbc663e88db97b', 'src/backend/backtest_program_terminal.py': 'ca9e17e8fc4f76be4ad1823356815466686a0247129e0d07ab31afcd383abb60', 'src/trading_runtime/columnar_strategy_reader.py': 'd64be35ff3bcc14f05c5d84b8328e97436ee7567749cd8b692b4ce598ce71767', 'src/backend/backtest_program_launch.py': 'fa767dad06846f762f53e2b3e6e209ade0bc8a4dda6c3e4d4bba2558667acd6d', 'src/trading_runtime/arte_broker_match_snapshot.py': 'a4014e83bacf741bb2c5e506f00006ca1d28d67d5a45530ad5875483ceaf2cb2', 'src/trading_runtime/strategy_one_broker_match_snapshot.py': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262', 'src/trading_runtime/arte_typed_insert_dispatch.py': '442daf4b02f27738c32c55c44d10d23ea69f362177cf4f8874928865933dca6e', 'src/backend/backtest_program_cursor.py': '40041225948223d26affc8bf64f1d727dd69c3c3e6be044640feebfbf1828ba2', 'src/backend/backtest_program_checkpoint.py': '5313af601beb6fd10168981064586ba176301cb0c956c87ba794bbc9354caeee', 'src/trading_runtime/arte_oms_observation_snapshot.py': 'da49a15aed48e4a7ba15a24a9b7db47126b711a031f424ccd4877053820c718b', 'src/trading_runtime/strategy_one_oms_observation_snapshot.py': 'd8f2ad09022bb5af10c3257113a604b32c3d6c0e2a263e8eb9c0a0d45731033b', 'src/backend/program_checkpoint_recovery.py': 'd20e36aeecf7fd4d92505ab32d2957d7d81f62fe043f00267d12130090528631', 'src/trading_runtime/arte_execution_exact_prices.py': '2ff73420c958b82815b9f2cd9f81568d9d4d19f4090b3699674e3916e0551721', 'src/backend/certified_program_configuration.py': '7081fcb62386f5223dd51b72ed90cbcc40fb3038d9051b5d657d270c1198cdb2', 'src/backend/strategy_configuration_reader.py': '187cc89028e3a9d6be7a90d2650ae8d9b82a38ee3a7036a687b69a977cbb225f', 'src/backend/strategy_configuration_materialization.py': '23a4b8160860c0cff3d44f6b50d9dfe9a8ed56806bae91ae69a1cb8b0f9aa1e6', 'src/trading_runtime/strategy_configuration_selection.py': '00e740d44ffe8a47fb0e5b9920ede3ce49c13b93cb6481225ca8276f9affcf93', 'src/trading_runtime/strategy_configuration_schema.py': '1cf7b42fc0ab9c4d7b26ef6c26be1cf68d63c66731486b49693fe159d5240f69', 'src/trading_runtime/arte_portfolio_policy.py': 'f5456d288e945ed6e47dbb66e33a92e6d3259927e9811cfe4bba0b702af0ec54', 'src/backend/backtest_program_feature_cache.py': 'f25fa32781e81979ecddaf65a4bb9ccd2ed332636775255529dfbbb221be0449', 'src/trading_runtime/strategy_signal_configuration_schema.py': 'e29075af4c1ddfdbeb35638e28e082272b7264a72daa930545fb910d266b11d5', 'src/backend/strategy_signal_configuration.py': 'cd8cb9ba65e7959148c73bf5e80fd9d1347ee7f7eaaebcf4b645d844d49d1565', 'src/backend/backtest_program_listing_cache.py': '398020833e5a987a1cb30f3e9ad9b7bab4ca6fe1b109c51883988b03a9dc37f4', 'src/backend/program_configuration_selection_service.py': 'cf2a5ffd2cfd87dd8d74604f51d75a8857b716a044cd9b0326d58129c4265424', 'src/backend/backtest_program_input_plan.py': '04aef593fe9514878e26d7158062a28827f2716c91325f44b008df78ef0afd88', 'src/backend/program_app_preparation.py': '8058622b9f04f64f49e11bc4f46d2d91c185fd7369ca86d5fd16556be1a3f946', 'src/backend/program_recovery_evidence.py': 'de3a0b52b6f5330791b7ff69129107ab033b250dad59c9b86ada2413a65b9ea4', 'src/backend/program_saved_definition.py': '46ac2fa121f370c8a5a232e383a110763fd4e7df0eafb94da4dbac5b8bf4bbb8', 'src/backend/backtest_fixed_running_anchor.py': '8667939409a6819c2c14512438bd35f23c1eac901a12f0e44faa06cd721bba00', 'src/backend/program_controller_recovery.py': '6434e044d31a31ae793ed8ce5d4d267a7b540e57a9909b8609182be2044c8b79', 'src/backend/filled_session_measures.py': 'b0cd476b99e9ea494abef5ab5f03a7da968eb551e35a0480e453edf2c7bf4559', 'src/trading_runtime/rule_decision_observation.py': 'a9f051bc240a14286c080d15d9e14c3f15c6a0e92c8506ad3e8efa7d75f30479', 'src/backend/recorded_program_comparison.py': 'af16475e59ac175116c9e4aa26ab050452a66d1c5dc68f1c7b5cf49c36036717', 'src/backend/backtest_program_liquidity_scope.py': '5c389d21067e1847173220e173052bc3cc4469d581c394c29e7f6fe88d81eab5', 'src/trading_runtime/ibkr_schema.py': '925fb38783932105171da76a75c16dc092f7c7a4dcddb39d3882781a8cdf1fe3', 'src/backend/program_managed_journal.py': 'c1e67bb24de2580921b26d3a298bb6fd095f00fbaa8142d7c8423bdc277a4157', 'src/backend/backtest_journal_clickhouse.py': 'b1262d6e432e13d6f44989cbe0a811aec54f7bb42d85947876fdbad1afbe1719', 'src/backend/program_app_handoff.py': 'df6b8bf46058855348ce78c2c2d89280ecd2eef93b17fba4d2490c7616958f82', 'src/backend/program_app_startup.py': '8e2526a954a5057722bf7ca106003ba1554cfc5e5783742c19fd4a4765d54eb0', 'src/backend/backtest_program_population_cache.py': 'c93a13db2d1c8a9923be1a3fb8cbb0e0472d309d7dce93554cecf6876fd369fa', 'src/backend/strategy_configuration_publication.py': '252e0d8a40024bf8f804e3822d2021ee49719b1d1a8942c3755dfedb75bcaba3', 'src/trading_runtime/configuration_execution_binding.py': 'd493b21793c34d77cb39f8a7764364486a6e755281b4fc304cc377975d7af4e5', 'src/backend/configuration_execution_binding_reader.py': '2d89cba8d1d56575d24df9dd3af8989be9a1a9e36f8728d199399efc3d02afba', 'src/backend/configuration_execution_binding_publication.py': 'c0af83f393f4775572782641ea6870dc1dd74f13daf6045b36e7f92a58624e8a', 'src/backend/strategy_configuration_transport.py': '658d47fb3bd2d4c16c55e1458bb399e7c0b5832a5a1e066ac697f7bd31e2bbd6', 'src/backend/configuration_execution_binding_transport.py': 'dd5a83e28ba771c8d1be5823bf0f980316b1bd334e07fd2d1e7301eac776419e', 'src/backend/program_app_bootstrap.py': '008c9148f14183181155ef7408f5bc7d16e0591182343f636cbcca3a2b3bf600', 'src/trading_runtime/program_stop_protection.py': '34a6e42feb3acb5f5f9ac44f23f9f64f13f36dbd460c8c8045bb266e9984d0ca', 'src/backend/application_registry.py': 'c6f90c6c8e2f0a30de0bbfe301e9e63244c5803039393acd401cd1c934c3c529', 'src/backend/qmd_feature_source_plan.py': '9c9107744d793cf6420202cbc3686ee9a0464f34cfac043cbbf169d81c406ac1', 'src/market_engine/qmd_feature_catalog.py': 'b3fc63c60adfe7ba84e00cb19e24efedd8e1505bdffe1c2922462b669a79c826', 'src/market_engine/causal_context_features.py': '540c4819574490b31abafe9e8b953e498d1aeb63f1e88257f5267edaedb34d04', 'src/market_engine/causal_market_context.py': '58e29371898bb9a5b27800fc23fc5a75d00e77134e9d3fe582f30fe19468325b', 'src/market_engine/continuous_indicator_features.py': '3bbf2511f2d4b3ab5768041593d6adcb569aeca07d23f7870e4d51bf43a73813', 'src/market_engine/candle_feature_projection.py': 'ea9a9e66bfcd9adbcc5d082e2cc0066b7447af0afaccc130db29d8b5d2755644', 'src/market_engine/feature_share_basis.py': 'a9d920902ef88293838acdb8b88b0ec83229624113d890c3656e0b32f5ec0935', 'src/market_engine/feature_source_plan.py': '08132a183f17fe2a65a8e1f4a71e1b59e3c0bfe15e1ba663a3a8f02ed7646b5f', 'src/market_engine/qmd_feature_projection.py': '115ddaf2e6178c04aa4bc79dfafca6c9d2c2c17f9411c32d908f0435b5de67cd', 'src/backend/backtest_strategy_one_identity.py': '227c92d3ed784031cd0ad4f80890146986b358700ccd2322a08859e9c59fa663', 'src/backend/optimized_execution_audit.py': '92a0f83e7f11e8a46137800ed33ccc00ee78d7a2f360af03c828eae4a864d18e', 'src/backend/optimized_execution_contract.py': 'b9f425e33fa6ac8bc02d755383ac1ea8154160f4d23f482d1cfbb7da922512e4', 'src/backend/optimized_strategy_import.py': '3a7d0e81d80b9c011ec7e917545507a62122ef48d92b746add4811eba81d75a4', 'src/trading_runtime/columnar_program_input_contract.py': '2b2092ed3960820df57915e801364f6102774c179d09d48312fe7d410031b552', 'src/trading_runtime/configuration_execution_risk.py': '9d705e2df1fb568bf7ca5172807ef87065fcf49f886426898479a5a7434eef70', 'src/trading_runtime/optimized_candidate.py': '176f16a8846592f9368861590daeacea8060aaa302504aed678dd4be74354d6a', 'src/trading_runtime/optimized_candidate_evidence.py': '6f78657a1c329bd43347194c27d02f2898eedecd4a6e30b9b48d3671f5744d89', 'src/backend/program_runtime_assembly.py': '994c0200c6ff6947f73823286cd1dcef371c1038579aa5a591c783c572bf45ce', 'src/backend/program_runtime_decision.py': '8f3499a36293140e0257d5d6c13de26f35264b465ab02c99ecd36be78ae4e453', 'src/backend/program_boundary_dispatch.py': 'ca512b19d01198115d9554cadc1353190981539da01721f2aa04b60a4ff3ffc4', 'src/backend/program_execution_journal.py': '8e80cd93ee59b4569acfd1b01175e4d4e50114d697f5528057e5da0d32e39f73', 'src/backend/program_pending_publication.py': '5a41eeed7e6315e7ef776cbb3717af4d649acb155da64c8931404ed86e897988', 'src/backend/program_broker_executions.py': '1eb2034d1f499f97c28fc10a415dc599893496e3daf686710a16d9585828dd44', 'src/backend/program_v4_principal.py': '06ea6fb103694e1a03055895702e31c7954c2b7f20cca0ca0b6ded7949378c99', 'src/backend/live_strategy_runtime_service.py': '3e686dfbbfb15c5d27fe304d084954bcc0a356ed2133a9cce8d4548bc0625816', 'src/backend/live_strategy_one_v4_principal.py': '3ea90581c550683d0905efec3dd915d42feacdeba1078204d72a754e4e331e29', 'src/backend/program_app_credentials.py': '520958e25dca5c0f365e015e13158eae2873fb5fbbddebd84c76ade501cb7319', 'src/backend/managed_backtest_credentials.py': '5b2c28a09cbafee3b22c981a1e83724f20743b485143a2091b905fe2da5ed774', 'src/backend/program_app_storage.py': '32231e6b28fcb76393ab5e14d78d1604b677646cf5420166b9f29645ed241099', 'src/backend/program_managed_configuration.py': '11abecdc595de464e0283c82ad824e6a2a6ba9587fd8bb91f7da4ac60865edbf', 'src/trading_runtime/program_broker_gateway.py': 'e578d596031d8893a679c8698acead61611f11326d9ec6d52b77c3799e237de5', 'src/trading_runtime/program_portfolio_journal.py': '6ac4da73d5f341f6da72ddb19e176f6233a52e740ba19edfa8c641f129f09a33', 'src/trading_runtime/program_portfolio_transitions.py': 'ab1641c2b36c6960d02fda045c593cd2c3e480ab19abd0acce2a508e042616c8', 'src/trading_runtime/arte_broker_policy_v4.py': '04096e3bbe630e4b4a2414c62126452941f07b619cffec300f649f0903d0d6b1', 'src/trading_runtime/arte_portfolio_control_v4.py': '0cbdd3bf3816505952f69856e5e00df72ecef6fc73c69fc945bee619b90fcc56', 'src/trading_runtime/arte_command_dispatcher.py': '20ac703ba233f080f4be5d601a76ff31e79c38dd08a0054b8edbe28800cd7d98', 'src/trading_runtime/arte_journal_compound_v4.py': 'a8ef4e0448406ec669b84a7ac9a3e9a94c5617baaa44220643a27ee9645a1e64', 'src/trading_runtime/arte_portfolio_sync.py': '82d8e38eb2c44eedc60a17dc2d3710d30020722bb8fbdff0e57bc7b9a5f7808f', 'src/trading_runtime/keeper_ownership.py': '146f19c707a2f839227745a910ac2b938af0fe202d88a402570f4036f41d1e9e', 'src/backend/program_signal_source.py': '9c35ac26b542e776cbdb01f361a341bdf4886ccd8cf9a2fa99db6ae3ed82e8c7', 'src/backend/signal_execution_context.py': '01c008d0fb9c8d8f0a349b1915aad620918139f4d0b1c2a895f07bd79ae66c55', 'src/backend/signal_context_schema.py': '01432dc9aab201d689fbcedea6b4dc628ea6df75d22831e7bda78b79520629f5', 'src/backend/signal_context_transport.py': 'a23a5374446c798308ac7ec1b70e5a15b316b6454940b05bb6ccaf2c55b41678', 'src/backend/signal_typed_json_wire.py': 'c1f8075b3c5450a3c50f5bf592bcf013d6a19bb3d0bb9f5ac9405ba1a7a9e26f', 'src/backend/signal_source_clickhouse_storage.py': 'f7b42c3396b79a0051dbd9b1b477ff5d301217fdec1a1a7909411aee1fe76f1c', 'src/backend/signal_source_registered_storage.py': '962dd0a50d30c885aa3fa025577c6b36bc587cb326fe5ebf80902c406a819e92', 'src/backend/signal_source_insert_dispatch.py': '01dd8ff694c3ac16e830d0e446816eb0d3e83d5d8e1828aaefd42487646df474', 'src/backend/signal_stream_session_head.py': 'ece7c7889a3dda5cca50f6bd755286e2833a21dd00adc93b6d1f4b63f4756a01', 'src/backend/signal_stream_typed_attestation.py': '463da55fde0721718e40f5e04ba5e219296d056c6c91cee31070a415dcba88f3', 'src/backend/live_signal_source_cursor_proof.py': 'a86beaa98209cb47b7db3fdd0963245154da91d2e1ce723b7de5c74411bc78ea', 'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3', 'src/backend/backtest_liquidity_price.py': '7dae0a00defd94100d4312bf18e9c32d0a7ddcbed1b35f68682b6e49c1b8defa', 'src/backend/qmd_volume_reader.py': '49fa726d34f7e4372f8eac1a282b727d33cd56ec8586286d0d2c5cc8677dadb2', 'src/backend/qmd_opening_reference.py': 'cf25249e18bb679fcdd8d399b0edabdb4bfc3e61a6773302e04b47c526893fe1', 'src/trading_runtime/arte_market_day_cold_preflight.py': 'f012cd4d0f995bf6d6198f6885323b56afab56d5e85a9697abe360f9f5dbed89', 'src/trading_runtime/arte_market_day_keeper.py': '0510e7900fcf7c61a1ecae9c5ec23cb68e46eeb4d9bc962717558c5c16a69349', 'src/market_engine/qmd_volume_product.py': '77227d9c70081dc82125d9a86dd6f22d5639e50140b1b6fba3a962041cc5b86d', 'src/market_engine/typed_product_insert.py': 'd230fd8f5bbe17a2c38d389b8f4c4255ab9bcba152b9e584dc6358fbb117ab58', 'src/market_engine/causal_market_history.py': '387b80e5c3355dd7653a0f37e42f44c1d09d5e9862559098b7870a3a988e9d61', 'src/market_engine/causal_volume_history.py': 'e1a6c518b1c6d524f13abd2f5cc5abd89bb47cc838106de6e3e68413b7c152d9', 'pipelines/market_sip/events/program_input_publication.py': 'e08fd9d15431c43c30254219c2a417dac8fd281e2cf78fffbb6f5541f00e3e73', 'pipelines/market_sip/events/qmd_feature_preparation.py': 'f63ad6b8fed367d16da9a549e0f33fb4e1f8d134bf09ad26d809c06476aeff77', 'pipelines/market_sip/events/qmd_feature_reference.py': '5ab077e594b2461942030e160fef40bdefa8ae7945f3a28cf0ef210859408fb4', 'pipelines/market_sip/events/qmd_volume_producer.py': 'ef9839a98cd7500658b4889636a5c1affbf2e2856b169297245477ed64f6bec3', 'pipelines/market_sip/events/columnar_history_producer.py': '914f8e5321909dc46079ca25cc2090b4126668b8d658c1efcd798ccb7b08d09f', 'pipelines/market_sip/events/opening_price_reference_producer.py': 'c71f33c756a211d7e07aa044661220af3d827714c21c1c65526d4513909d663f', 'pipelines/market_sip/events/regular_close_history_producer.py': '2af3229089df895b565d8f3112c048275242658da9bcc8ac33e125cae0521d68', 'pipelines/market_sip/events/liquidity_resolution_producer.py': '9c1829358bb87de7a55ad84bd27398afc28d9c34acadd47e1b35a8f944b2101e', 'pipelines/market_sip/events/liquidity_execution_price_producer.py': '0941c7ea029e080c935213e50b52660d32179a8c5c744c4d284f58086714e178', 'pipelines/market_sip/events/shared_reference_listing_basis.py': 'c94ddd525ad70ca2f0c3807eb8536ccad341385e36433c52543eb9857a7e9cb5', 'src/backend/optimized_audit_run.py': 'ab8d48ad11728e5d121f143469bee7d9420c4fdf89edffca864650a83001f6db', 'src/backend/optimized_audit_collection.py': 'aac594ebb9b784ff9be5456f369ae1d847188b8b1c46c7d69e128355bcd7f149', 'research/vectorized_backtest/v19/audit_reference.py': '370fbf81e1a76253efef94599bc90b74908d34479f46ba65c0b4cae855fa3025', 'scripts/audit_optimized_strategy.py': 'e05a6f03e91db7683959c0873b55e51a86220d482f76f52637aba96d78f12c9c', 'src/trading_runtime/strategy_orders.py': '4813e9edcea5b22c2a1e8716fb88051accca99f1efb8ff3e2fdd316efa62a2fd', 'src/trading_runtime/arte_portfolio_reconciliation_v4.py': '549924b8da680f0bba926476f570fe34892cdc652f68536731de0538db71f7db', 'src/backend/backtest_reconciliation_v3.py': '3e15c9a6365b249054620bf7d8f88073026820947d550fd249623b5e2d62c605', 'src/backend/completed_signal_cursor_reader.py': '3e04ae799a4a6763ed680434f1e67d5c7b18450f92c1eb85bb2078bdf00a9a53', 'src/trading_runtime/simulated_broker.py': '99e2fcda3bab26979928f1af469a08979b70eec20d5a19fcf870861b9c73b562', 'src/backend/program_terminal_financial_rollforward.py': '92ee63759efe30f1aecd9b3ece5332c5d0439f7533b8318c38698f035eb6f6d5', 'src/backend/program_terminal_portfolio_rollforward.py': '82d01e12fc0e5452be59bd3a7e44b64e15c1b83af0cd2801338f86ebda14978f', 'src/backend/program_terminal_checkpoint_publication.py': '96d5ae624934a1288c61e264bb2e70b034a3fe563c22114fc4342dabb178e71e', 'src/trading_runtime/entry_cash_budget.py': 'a38b2b2654d345538246042b37b393f1c8feb2a699627786326c3878991b6d1e'}


def certify_drawdown_measure_core_source(*, source_overrides: dict[str, Path] | None = None) -> str:
    """Seal the shared legacy/declared numeric owner and recovery dependency closure."""
    if set(_DRAWDOWN_CORE_REVIEWED_AST) != set(_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES):
        raise ValueError("Drawdown core source keyset changed")
    metadata_anchor = "bb40b54603bb8823b4ead2b73e21ed9ed73d3e0869d4455c143818bda3278035"
    self_relative = "src/backend/backtest_fixed_v4_certification.py"
    self_symbols = ("certify_drawdown_measure_core_source", "certify_strategy_one_v4_projection")
    if (type(_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES) is not tuple
            or any(type(path) is not str for path in _DRAWDOWN_CORE_REQUIRED_SOURCE_FILES)
            or len(set(_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES)) != len(_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES)
            or type(_DRAWDOWN_CORE_REVIEWED_AST) is not dict
            or type(_DRAWDOWN_CORE_REVIEWED_AST.get(self_relative)) is not dict
            or set(_DRAWDOWN_CORE_REVIEWED_AST[self_relative]) != set(self_symbols)
            or any(type(value) is not str or len(value) != 64 for value in _DRAWDOWN_CORE_REVIEWED_AST[self_relative].values())
            or any(type(value) is not str for path, value in _DRAWDOWN_CORE_REVIEWED_AST.items() if path != self_relative)):
        raise ValueError("Drawdown core metadata shape changed")
    metadata = {
        "required": _DRAWDOWN_CORE_REQUIRED_SOURCE_FILES,
        "sources": {path: value for path, value in _DRAWDOWN_CORE_REVIEWED_AST.items() if path != self_relative},
        "self_symbols": self_symbols,
    }
    if sha256(json.dumps(metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != metadata_anchor:
        raise ValueError("Drawdown core metadata anchor changed")
    overrides = source_overrides or {}
    if set(overrides) - set(_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES):
        raise ValueError("Drawdown core source override is unknown")
    root = Path(__file__).parents[2]
    helper_relative = "src/backend/source_ast_summary.py"
    helper_source = Path(overrides.get(helper_relative, root / helper_relative)).read_text(encoding="utf-8")
    if sha256(ast.unparse(ast.parse(helper_source)).encode()).hexdigest() != _DRAWDOWN_CORE_REVIEWED_AST[helper_relative]:
        raise ValueError(f"Drawdown core source changed: {helper_relative}:direct-ast")
    observed = []
    for relative, expected in _DRAWDOWN_CORE_REVIEWED_AST.items():
        source = Path(overrides.get(relative, root / relative)).read_text(encoding="utf-8")
        selectors = tuple(expected) if isinstance(expected, dict) else ("__module__",)
        summaries = canonical_symbol_ast_summary(source, selectors)
        if (type(summaries) is not tuple
                or len(summaries) != len(selectors)
                or any(not isinstance(observation, tuple) or len(observation) != 2
                       or type(observation[0]) is not str or type(observation[1]) is not tuple
                       for observation in summaries)
                or tuple(observation[0] for observation in summaries) != selectors):
            raise ValueError(f"Drawdown core cached summary shape changed: {relative}")
        for symbol, digests in summaries:
            digest = expected[symbol] if isinstance(expected, dict) else expected
            if len(digests) != 1 or type(digests[0]) is not str or digests[0] != digest:
                raise ValueError(f"Drawdown core source changed: {relative}:{symbol}")
        if relative == self_relative:
            declarations = {}
            for node in ast.parse(source).body:
                if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES", "_DRAWDOWN_CORE_REVIEWED_AST"):
                    if node.targets[0].id in declarations:
                        raise ValueError("Drawdown core source metadata is duplicated")
                    declarations[node.targets[0].id] = ast.literal_eval(node.value)
            if set(declarations) != {"_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES", "_DRAWDOWN_CORE_REVIEWED_AST"}:
                raise ValueError("Drawdown core source metadata is missing")
            source_map = declarations["_DRAWDOWN_CORE_REVIEWED_AST"]
            if (type(source_map) is not dict
                    or type(source_map.get(self_relative)) is not dict
                    or set(source_map[self_relative]) != set(self_symbols)
                    or any(type(value) is not str or len(value) != 64 for value in source_map[self_relative].values())
                    or source_map[self_relative] != _DRAWDOWN_CORE_REVIEWED_AST[self_relative]):
                raise ValueError("Drawdown core source self pins changed")
            source_metadata = {
                "required": declarations["_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES"],
                "sources": {path: value for path, value in source_map.items() if path != self_relative},
                "self_symbols": tuple(source_map[self_relative]),
            }
            if sha256(json.dumps(source_metadata, sort_keys=True, separators=(",", ":")).encode()).hexdigest() != metadata_anchor:
                raise ValueError("Drawdown core source metadata anchor changed")
            if (type(declarations["_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES"]) is not tuple
                    or declarations["_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES"] != _DRAWDOWN_CORE_REQUIRED_SOURCE_FILES
                    or type(source_map) is not dict
                    or source_map != _DRAWDOWN_CORE_REVIEWED_AST):
                raise ValueError("Drawdown core fresh declarations disagree")
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(",", ":")).encode()).hexdigest()


def certify_strategy_one_v4_projection(
    *, controller_source: Path | None = None,
    indirect_sources: tuple[Path, ...] = _INDIRECT_SOURCES,
) -> str:
    """Reject any indirect emitter without a proven normalized V4 projection.

    The direct controller certificate already checks fixed-only reachability.
    This separate indirect inventory binds the runtime/OMS/portfolio sources;
    it cannot be replaced by observing one Backtest's emitted records.
    """
    core_proof = certify_drawdown_measure_core_source()
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
    if set(families) & (_LEGACY_PROTECTION_FAMILIES.keys()
                       | _REDUNDANT_MODIFY_SUMMARIES.keys()):
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "order_management.py" not in sources_by_name
                or "runtime.py" not in sources_by_name):
            raise ValueError("V4 legacy protection lacks fixed-runtime source authority")
        unreachable_proof += certify_strategy_one_legacy_protection_unreachable(
            oms_path=sources_by_name["order_management.py"],
            runtime_path=sources_by_name["runtime.py"])
        unreachable.update(set(families) & _LEGACY_PROTECTION_FAMILIES.keys())
        unreachable.update(set(families) & _REDUNDANT_MODIFY_SUMMARIES.keys())
    if ("portfolio_management", "portfolio_request") in families:
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "portfolio.py" not in sources_by_name
                or "runtime.py" not in sources_by_name):
            raise ValueError("V4 portfolio request lacks source authority")
        unreachable_proof += certify_strategy_one_portfolio_request_unreachable(
            runtime_path=sources_by_name["runtime.py"],
            portfolio_path=sources_by_name["portfolio.py"])
        unreachable.add(("portfolio_management", "portfolio_request"))
    if ("strategy", "strategy_assignment_state") in families:
        sources_by_name = {path.name: path for path in indirect_sources}
        if (len(sources_by_name) != len(indirect_sources)
                or "runtime.py" not in sources_by_name):
            raise ValueError("Strategy 1 assignment event lacks runtime source authority")
        unreachable_proof += certify_strategy_one_assignment_event_unreachable(
            controller_path=controller_source or Path(__file__).with_name(
                "replay_run_service.py"),
            runtime_path=sources_by_name["runtime.py"])
        unreachable.add(("strategy", "strategy_assignment_state"))
    supported = _V3_PROJECTED | _COMMON_TYPED | _V4_ADDITIONS
    unsupported = sorted(set(families) - supported - unreachable)
    if unsupported:
        raise ValueError(f"V4 indirect emitters lack typed projection: {unsupported}")
    evidence = tuple((path.name, sha256(path.read_bytes()).hexdigest())
                     for path in indirect_sources)
    return sha256(json.dumps({
        "version": 1, "direct": direct, "families": families,
        "sources": evidence, "unreachable_proof": unreachable_proof,
        "drawdown_measure_core": core_proof,
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def certify_recent_bos_entry_source(*, source_path: Path | None = None,
                                    static_path: Path | None = None,
                                    adapter_path: Path | None = None,
                                    coordinator_path: Path | None = None,
                                    intent_path: Path | None = None,
                                    commit_path: Path | None = None) -> str:
    """Bind Strategy 12's inclusive BOS clocks to vector and sequential routes."""
    runtime_root = Path(__file__).parents[1] / "trading_runtime"
    paths = (source_path or runtime_root / "strategy_recent_bos_entry.py",
             static_path or Path(__file__).with_name("backtest_strategy_one_static_gate.py"),
             adapter_path or Path(__file__).with_name("backtest_strategy_one_stateful.py"),
             coordinator_path or Path(__file__).with_name("backtest_strategy_one_coordinator.py"),
             _STRATEGY_ONE_EXECUTION, intent_path or _STRATEGY_ONE_INTENT,
             runtime_root / "arte_strategy_one_entry_journal.py",
             runtime_root / "runtime.py", commit_path or runtime_root / "arte_journal_commit_v4.py")
    sources = tuple(path.read_text(encoding="utf-8") for path in paths)
    trees = tuple(ast.parse(source) for source in sources)
    constants = [n for n in trees[0].body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "MAX_BOS_ENTRY_AGE_MS" for t in n.targets)]
    if len(constants) != 1 or ast.unparse(constants[0].value) != "30000":
        raise ValueError("Strategy 12 recent BOS age bound changed")
    expected = {'recent_bos_entry': ("if type(boundary_ms) is not int or not 0 < boundary_ms <= 57600000 or boundary_ms % 100:\n    raise ValueError('Recent BOS entry needs a completed 100ms boundary')", 'if bos_break_boundary_ms is None:\n    return False', "if type(bos_break_boundary_ms) is not int or not 0 < bos_break_boundary_ms <= boundary_ms or bos_break_boundary_ms % 1000:\n    raise ValueError('Recent BOS entry needs a causal completed 1s break')", 'return boundary_ms - bos_break_boundary_ms <= MAX_BOS_ENTRY_AGE_MS'), 'recent_bos_entry_mask': ('boundaries = np.asarray(boundaries_ms)', 'breaks = np.asarray(bos_break_boundaries_ms)', "if boundaries.ndim != 1 or breaks.shape != boundaries.shape or boundaries.dtype.kind not in 'iu' or (breaks.dtype.kind not in 'iu') or np.any(boundaries <= 0) or np.any(boundaries > 57600000) or np.any(boundaries % 100) or np.any(breaks < 0) or np.any(breaks > boundaries) or np.any(breaks % 1000):\n    raise ValueError('Recent BOS entry needs aligned causal completed clocks')", 'age = boundaries.astype(np.int64, copy=False) - breaks.astype(np.int64, copy=False)', 'return (breaks > 0) & (age <= MAX_BOS_ENTRY_AGE_MS)')}
    for name, statements in expected.items():
        functions = [n for n in trees[0].body if isinstance(n, ast.FunctionDef) and n.name == name]
        actual = tuple(ast.unparse(n) for n in functions[0].body
                       if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                               and isinstance(n.value.value, str))) if len(functions) == 1 else ()
        if actual != statements:
            raise ValueError("Strategy 12 recent BOS completed-clock or inclusive bound changed")
    static_routes = [n for n in ast.walk(trees[1]) if isinstance(n, ast.If)
                     and ast.unparse(n.test) == "strategy_number in (12, 13, 14, 15, 16, 17, 18, 19)"]
    if len(static_routes) != 1 or not all(value in ast.unparse(static_routes[0]) for value in
            ("recent_bos_entry_mask(boundaries, break_boundaries)",
             "fact.bos_break_boundary_ms or 0", "reasons |= (~recent).astype(np.uint8) * RECENT_BOS_REQUIRED")):
        raise ValueError("Strategy 12 recent BOS vector gate is not exclusively routed")
    adapter_routes = [n for n in ast.walk(trees[2]) if isinstance(n, ast.If)
                      and ast.unparse(n.test) == "(declared is not None or strategy_number in (12, 13, 14, 15, 16, 17, 18, 19)) and (not recent_bos_entry(boundary_ms=fact.boundary_ms, bos_break_boundary_ms=fact.bos_break_boundary_ms))"]
    if len(adapter_routes) != 1 or ast.unparse(adapter_routes[0].body[0]) != "return StrategyOneEntryDecision('recent_supported_bos_required')":
        raise ValueError("Strategy 12 recent BOS sequential adapter is not exclusively routed")
    coordinator_calls = [n for n in ast.walk(trees[3]) if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Name) and n.func.id == "propose_certified_strategy_one_entry"]
    if len(coordinator_calls) != 1 or not any(k.arg == "strategy_number" and ast.unparse(k.value) == "strategy_number" for k in coordinator_calls[0].keywords):
        raise ValueError("Strategy 12 coordinator must thread authoritative numbered identity")
    execution_calls = [n for n in ast.walk(trees[4]) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Name) and n.func.id == "run_strategy_one_proposals"]
    if len(execution_calls) != 1 or not any(k.arg == "strategy_number" and ast.unparse(k.value) == "config.strategy_revision" for k in execution_calls[0].keywords):
        raise ValueError("Strategy 12 execution must thread certified revision")
    intent_routes = [n for n in ast.walk(trees[5]) if isinstance(n, ast.If)
                     and ast.unparse(n.test) == "policy is not None or proposal.strategy_number in (12, 13, 14, 15, 16, 17, 18, 19)"]
    if len(intent_routes) != 1 or "if not recent_bos_entry(boundary_ms=proposal.boundary_ms, bos_break_boundary_ms=proposal.bos_break_boundary_ms):" not in ast.unparse(intent_routes[0]):
        raise ValueError("Strategy 12 intent authority must reject forged stale BOS")
    # Persistence, cold entry evidence and runtime admission reuse the guarded
    # factory; raw normalized rows reuse the same pure eligibility authority.
    for tree, names in ((trees[6], ("project_strategy_one_entry_evidence", "load_committed_strategy_one_entry_page")),
                        (trees[7], ("submit_strategy_one_proposal",))):
        for name in names:
            functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
            runtime_route = name == "submit_strategy_one_proposal"
            if len(functions) != 1 or not any(isinstance(n, ast.Call) and (
                    isinstance(n.func, ast.Attribute) and n.func.attr == "_strategy_one_entry_intent"
                    if runtime_route else isinstance(n.func, ast.Name)
                    and n.func.id == "strategy_one_entry_intent") for n in ast.walk(functions[0])):
                raise ValueError("Strategy 12 persisted entry authority factory route changed: " + name)
            if runtime_route:
                wrappers = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                            and n.name == "_strategy_one_entry_intent"]
                expected = _RISING_MOMENTUM_REVIEWED_AST["trading_runtime/runtime.py"]["_strategy_one_entry_intent"]
                if len(wrappers) != 1 or sha256(ast.unparse(wrappers[0]).encode()).hexdigest() != expected:
                    raise ValueError("Strategy 12 runtime entry wrapper differs from reviewed authority")
    raw_routes = [n for n in ast.walk(trees[8]) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "row['strategy_number'] in (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61)"]
    if len(raw_routes) != 1 or "if not recent_bos_entry(boundary_ms=row['boundary_ms'], bos_break_boundary_ms=row['bos_break_boundary_ms']):" not in ast.unparse(raw_routes[0]):
        raise ValueError("Strategy 12 raw normalized entry authority must reject forged stale BOS")
    return sha256(json.dumps(tuple((path.name, sha256(source.encode()).hexdigest())
        for path, source in zip(paths, sources)), separators=(",", ":")).encode()).hexdigest()


# Reviewed Strategy 13 source observations, filter, submission and durability
# routes. Exact canonical AST seals bind the tested implementation; a later
# behavior change gets a new numbered release. Comments/line endings do not
# affect these seals. The run separately pins its complete backend fingerprint.
_RISING_MOMENTUM_REVIEWED_AST = {'backend/backtest_journal_memory.py': {'BacktestMemoryJournal': 'b029f7b0d8f040c77d0f34181c9a98d7dc955755374e784d16ef859761259b6f', 'mark_fenced': 'fb2b5286fce3638ff6517addd1c01002625ff3e17d7aacead8c6d40b6bcea3f1', 'append_profit_giveback_exit': '985c2700f17e52db5948fd31b1ba6af9234077919a10c8507bce06fc8c9f3f4d', 'profit_giveback_exit_for_record': '961b81e4f28658ccfafcdeae95bac8c0c1aa4951f989a86cd819bab86212ed40', 'append_followthrough_exit': '0eb37dc4e964a66200a871922a6437809e3fbd1a9cba5561a62acc8e18541116', 'append_strategy_one_intent': '9a0ef434fe20d5cafeb4dfe3fed1695aaa7945ae8915b5cd8a2c91299529ae16', 'append_strategy_one_protection_intent': '87469d33983c1947a51482344c8a79cf92b0b3340c36131cf64e85f034afe578'}, 'backend/backtest_market_plan_cache.py': {'selected_product_inventory_fingerprint': '2a24f97bf8d162e0d979c01fedbaba02de6e37cdaa0da48c482700c31211f355'}, 'backend/backtest_saved_source_authority.py': {'__module__': 'bb486b3cd6337d13ab5bfa9aea9e6614ab8e5a5a3bac558b06194cc240d8a5fc'}, 'backend/backtest_strategy_certified_price_break.py': {'__module__': '8d6a7a26fc783252033f882544312eef55a5407b95e60259640452f9f42891ac'}, 'backend/backtest_strategy_first_price_source.py': {'__module__': 'a2557a2973a3ad1371fd9d97ffe740cf0687aa510372b1d6471d0284f92d2146'}, 'backend/backtest_strategy_initial_momentum.py': {'__module__': '954446cab169240a183801637b4f61b27f90d45467b065ef428760d1ed5a48df'}, 'backend/backtest_strategy_initial_momentum_growth.py': {'__module__': '33bf35371d2216a5361e735959cdd1e48a65be3a5d8f04ec3d79cd277d199485'}, 'backend/backtest_strategy_initial_price_break.py': {'__module__': 'aa5968f100749c56e97c66279ba4bd1feb583e9b3a8f8a978718f234b2d98ff6'}, 'backend/backtest_strategy_initial_ten_percent.py': {'__module__': '73b8b04654cd8ebef2a8906908bac4c82fa06616fb4d50d19f73442f7f07f420'}, 'backend/backtest_strategy_one_configuration.py': {'__module__': 'e7ac8ac056735d57086db293f47d915a7a987cf0e0e91d428e7117e494399f3f'}, 'backend/backtest_strategy_one_coordinator.py': {'run_strategy_one_proposals': '4b87990c4c6ab46db15dde2df683cafe2932bcdd2fd7cc973e1addb5511efdf4'}, 'backend/backtest_strategy_one_execution.py': {'run_certified_strategy_one_session': 'd9ce17bd7419ed3cf29d18c07ad2e133c5efe1c90671f5b7e18ee3b8b8f32b4a', 'run_strategy_one_fixed_session': 'c256cd23b41991c190d2d9cac400265da080456f7f01826e88c4ba8d9b86a3da'}, 'backend/backtest_strategy_one_management.py': {'__init__': 'd2302383942594edc2eb9a0b1b0c314ff75d5b5fc63ee835349ad81ffc27c397', 'profit_arming_requests': 'f7bbab7b86ead082838c0c069fe589cafc78329b17057c8716edc2013fb81a2e', 'accept_profit_arming_references': '3887b6c277a19ddd561fe092695fdd895d5c6fd8616e434cfc644853767eafdd', '_validate_capture': '34bbba86e7c157dbd4b361eef0027c1b7fd680baba9ec378a616623bcf68ecb3', 'on_management': 'd54623723ac130d0e2712c8b46df32da704b56adfaa865a8bf6315590c3fc190', 'restore_state': '6df788d5d58734c5bd6f9e6536581c0882a32700a79b7d63e69fce4ca7dad7a0'}, 'backend/backtest_strategy_one_stateful.py': {'propose_certified_strategy_one_entry': '835ad352d75040545d715f5ccc44b7e22cad6516d97a331950acee008ae25c76'}, 'backend/backtest_strategy_one_static_gate.py': {'compile_static_entry_gate': 'e6d2f747557dfbe005d492fa4a24ef671f47736961196cd1ca27ec2da438d0e5'}, 'backend/backtest_strategy_one_v7_interval_store.py': {'certify_v7_interval_plan': '3e5a6b0150e9225821f165971f4c2526d41c2d9934c50bdbaa9101c295c219aa'}, 'backend/backtest_strategy_rising_momentum.py': {'__module__': 'c0f4a1084b29088a1df63cdeb5c1b82b3d467fe2a657ee08ab37037bd6a6f245'}, 'backend/backtest_typed_projection.py': {'project_pending_backtest_v4_prefix': 'b075422ce1cbd2f8bc20bca16ca95b933ed1a592339ffa966da803d834535600'}, 'backend/backtest_typed_publisher.py': {'_drain': '8021317305e1cad13ae9a723cdce13cb74c7a3410f6007ed547281d0abeae70e', '_prepare_batches': 'b87339236e4503312ec5877bbc9390c4988c1ea5225b8cc0a4c9a02a2b6321f8', '_publish_terminal_v4': 'f9eabf5aca42abc249a30d7d164fc8689a56b16c284c2a8bb523d823a3932ce4', 'bind_first_price_source': '0524563fd12e8bf74d2861c9595977e2bed0e8e17c780e5a61c1c2ac2dddd5c6'}, 'backend/backtest_v4_history.py': {'__module__': '9ed3f860d27d2b6f776d4b42c0e9225fc84a75744504fa9dce60dcdd1326bd23'}, 'backend/backtest_v4_saved_review.py': {'_saved_twenty_price_source': '4cd7f7adb6d0b9637e107d13158e4c3cae73b21cf35c7fb3df77c36a9d66ccc4', '_terminal_attestation': '21f81e3095f24f4aca45821942ac4b300800abbeea48c7b7b058fc5d9b98c0e4'}, 'backend/historical_runtime_versions.py': {'__module__': '96941bdcb6b84439c3ba3f0d1fc0deb9cde238746088d1b284284aa0969857ec'}, 'backend/replay_run_service.py': {'_save_restart_checkpoint_responsive': '5f54ab88d56dbd588090b9de2e7404da167ae261a82eeb5b53f1830130ee568e', '_confirm_profit_arming_checkpoint': '8a5f2f680ece488f74197c9d204fadf678973fd017caf7127ef77d575cd7b8d4', '_run_strategy_one_fixed_days': '45a75d03d3c67ba7087a69b6258901537165a9a5b65946b37281217f5dc5a5fb', 'backtest_preflight': '50e7be10ee4df855865a67221d39480f4912d17c83ebb51f11b3fc3993e3210a'}, 'trading_runtime/arte_backtest_definition.py': {'_reconstruct_backtest_definition': '593e205522805972b2f2e2af86b076dad191be0928b74996b4bf3b2172b5f26d', 'reconstruct_backtest_definition_from_arte': 'f9f64dcd409d411db63d2057246b597e9a499df7b899f4afd46b708cd3f08f61', 'reconstruct_saved_review_definition_from_arte': '8ea00e67695146cbaee08d6825d4fd55eb9300e5ef16abcf21b98282658ddc2e'}, 'trading_runtime/arte_first_price_entry_v4.py': {'__module__': '13934841d1f1a36c7eaa3df3a5a95f130c29e433d2f5eb56b6941f60fe89526a'}, 'trading_runtime/arte_followthrough_failure_v4.py': {'_source_entry': '2e3eb76a2d8ade0055c9792254311e1af4f5ebde2933b226530c2b2c39f80b46', 'seal_followthrough_rows': '9b7dbca8a5db60362ae0fe7dbf6cb5dc0c728c2c311a11bcde786a70e4fac1c4', 'validate_numbered_failure': '6d5713de9ee93c53b45185392d1d4d12a6231bdc1179701895447626ed71533a'}, 'trading_runtime/arte_initial_momentum_entry_v4.py': {'__module__': 'cc57d1ec7e23ad0ffd8eb183abb35badaeb8b7257a943768c146fd7c10f69888'}, 'trading_runtime/arte_journal_commit_v4.py': {'verified_batch_predecessor': 'e7f52f08e28175674cf663dbde1b17abf7bf53513884f7d2289fbdd4dfe63ecf', '_load_verified_details_v4': 'b66ccb3db46a15f3ade54cf47ef56a796a291b92548ccd197f6ac08dad217e1f', '_publish_sealed_batch_v4': 'c47aa94515f05e274de6ea86f5ad9a56334fb2f959f2c76e409d48fec5bf15fa', '_publish_typed_batch_v4': '2856541318c0353ed13d306c54a9bb41da367f66678c74c9b6cde71d95e9fe2a', '_validate_strategy_one_entry_link': '83648a1fb6b54bdf1987edd3bba05d70c8bfb1065566073a5a2667ea59eb79e0', 'load_verified_commit_v4': 'bdb5d145cdb9d7d8e04f29d3ce63dbed2b557b7deb7b809ebde9489f9bf2c763', 'load_verified_v4_prefix': 'f05bce1c29f78b4ec1ce33edc20236e0a8f0a44bec452c8847b0968d46e6b315', 'load_writer_v4_snapshot_prefix': 'd4a6aaa55ed12b6a5916af7f7ca2ea80e134d8bfe76f0743a4b282c93895582a', 'publish_strategy_one_entry_batch_v4': 'b1e928db522a5aeda0887c897b42db5cb7474a98b2746f79d802971c2795550e', 'publish_terminal_typed_batch_v4': 'f352365d43e2dbc998a206a4afa54a4b2600ce0106ee410098a4a48b73228b88'}, 'trading_runtime/arte_journal_compound_v4.py': {'__module__': '53de6ae51a5470abf2d13e02da65ada39aa676cee5e8ec617d0b45d0f323c31b', '_publication_kwargs': 'cd42f559d6fbb83d5d6a912985e739096bb30477a9eb76b7f19422da81faaeec', '_unit_children': '839c3c2f99049f219df1217fc56f6f83e42004e2c982c036dfa6e331e448a329', 'prepare_compound_v4_families': '5a0d52d20d50b7b8ae26a934bd6ac10f22df540157e4e2e3b82f74f87e37fb5f'}, 'trading_runtime/arte_journal_writer.py': {'_ProfitPublicationUnit': '7cf5b6eec9ddf368a1427d43fc5cfda7ae48b1efe661d66af970f160f82f2c72', 'submit_compound_v4': '39ea1a6ebbc26f82102d7b28f4b5815632b80614fc6c4cdf2d440254aa4bfcd8', 'submit_profit_exit_v4': '0b1344d9735e100b5901ca3df69773968e838a738127d73df3510478c0e1c2fe', '_submit_profit_publication': 'c7ff4e80aacd3fcad0d45fbb2bcd3ceec055b1e0a8c6a1d2f1ec25fb621882d7', 'V4StrategyOneEntryBatch': 'caa84b3d99c6eb4aa9b5f284afc94dab06e38b9b69a6e99f1e4df68f50e21a38', '_BrokerMatchSnapshotUnit': 'b9f57982ff159fc4c38acf5e2bea17e3edec5941ca5a3a339231949824982617', '_CampaignSnapshotUnit': '7d38bc41d265b2507a147da65bfa59c298fe0b77234ca611ede1c70367714a75', '_EvidenceSnapshotUnit': 'dd22c3b1bf5a3eac08d55fa86790b60e8cb854b7678c0b4866185c9208af400e', '_ManagerSnapshotUnit': '0fc3c933a83a2c48e0feb873cf3882e4f9ef89828e9543723469bb8facb7e18f', '_OmsObservationSnapshotUnit': 'f205cbfa584c3566500f0558ef3b08a44e8f07f56778cba9000a3667d7701001', '_TerminalBacktestUnit': 'f42ccbf195ea58f40dc4a78fa7494655b47b59558d7546191a21e3f5c0fa9f62', '_run': 'da9d5de414475e8035e1a896a6248eaf65166ad9e6322943b503493821e9e700', '_validate_checkpoint_price_source': '2cdc6c63fdaf1effa48c2e0e467d2582620e796f2ee4a383e297584ed39a6b74', 'submit_broker_match_snapshot': 'ccb2ae1ec6caf555675db0b729e91636d06d35d408acde560f52e7056d7d6324', 'submit_campaign_snapshot': 'fa09a5cf1c45b87aa873185b92267c21965b042ce0a216dfa2f0ec046ffdf85b', 'submit_evidence_snapshot': '03c8628952e81825891a0f936db7a911b2984d20ef41772aeaeb9d0ff4a26674', 'submit_manager_snapshot': '9613de84dd89731121920f771f64eb68379391842ffacbe6c60bbc9601a1a647', 'submit_oms_observation_snapshot': '79e7e068135f55029751ed1fd710b63ccdcb902825c972f73ac58e4a6bca25fe', 'submit_terminal_backtest': '2cd75bbabb2057da72695abec3ca937c2d4370f6fe2391ea3612d378114c6344', 'v4_journal_write_tables': '20ee05021d0ec95fc113482abcb0be770ba5d5407ebc6e4e903efe6982e4b3db', 'v4_storage_contracts': '74673f56137f80754160338c3ca5c2b2008bcfcb05b40cdea7b7e20ab778faaf'}, 'trading_runtime/arte_profit_giveback_v4.py': {'__module__': '6b188986455e90783833f8e34f0e9c845400fa7cf29cf85b2f003872fab946ab'}, 'trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'}, 'trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'eda77e55bdbddc2bc67969107f54253379deba37aec61d104de492f58b6c882e'}, 'trading_runtime/strategy_profit_giveback_arm.py': {'__module__': 'bbb9ea954eef8212196fa6b31907f5c0e2209e8c6966cd9a8aad3161b5612d64'}, 'trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'}, 'trading_runtime/strategy_profit_giveback_source.py': {'__module__': 'b7223c7d10e9670362b8540bed75eb00950391523c6907c7b07921a8d2c3cfc2'}, 'trading_runtime/arte_rising_momentum_entry_v4.py': {'__module__': '38ba23e6f86c22d30515b74258f877aa94e78b10ffa8e9ee6890050d28be68e4'}, 'trading_runtime/arte_strategy_one_entry_journal.py': {'load_committed_strategy_one_entry_page': '50967662b4bc672fd24379a68f04c7d64ff7605f63823c466d78d0af6cc63da4', 'load_committed_strategy_one_source': '87f139b4ad6871266d5f605499d0f50c734926a7240bcc0d482f6a262396d230', 'project_strategy_one_entry_evidence': '5a303832f564b5cd7410cd460053346184b42c00c370479c44ac6770b097c927'}, 'trading_runtime/numbered_fixed_strategy.py': {'__module__': '26510f4c0bd533119668301f06f38ccab047e8b835e58ce79efc14a68b7b9326'}, 'trading_runtime/runtime.py': {'submit_followthrough_failure': 'afd29717f5efc7c1c14c283938f796f53145ac384a4b45aa9e20eeb8963457e3', 'submit_profit_giveback': 'fcd638576f375df9e6649800c40675f8a71520ce92020441bba3ca268f71df1c', '_execute_intents': 'b10f5cbfc1efdbb9677b729138eaa6869806487a372430685b4a36339a92980c', '_strategy_one_entry_intent': '97067a4b569fe8efb6c805c71673e89f22819d03dc50609b407d159a965e255b', 'bind_strategy_one_price_source': 'a56c1c0f462ff5afbae26c3ed83e573cd74c0a91203cfe2f5e49d93d915c8283', 'submit_strategy_one_proposal': 'b2e56337a591ebad402232c677e04ae34e4e2e79c5ca0d2fa382c99e0913865c'}, 'trading_runtime/strategy_followthrough_exit.py': {'__module__': '6faac9cae66c139869dad4c3be31d4b9a40efa3e3984bfb8e0bdc088d277d450'}, 'trading_runtime/strategy_initial_momentum_growth.py': {'__module__': '68d66854b639e67a5d3734aaaf1a61ce015963d6841746a59c4ad85390761606'}, 'trading_runtime/strategy_initial_price_break.py': {'__module__': '5279377acee015b28239ecd1949657fb1ce66731835a6190b686bf54a38ce3ce'}, 'trading_runtime/strategy_initial_strong_momentum.py': {'__module__': '65c6021a7a7c287682a502989fe03b638ee6aaa6ae1488e9a323a0e4c75f4c06'}, 'trading_runtime/strategy_initial_ten_percent.py': {'__module__': '086a330212aa01c2ddf70bdb8ddb2db70654f4b851662977ea86ec307be18be2'}, 'trading_runtime/strategy_one_broker_match_snapshot.py': {'__module__': 'c104a2420df90e936d594b95b47125195523130f69388043be66f3c0527f1262'}, 'trading_runtime/strategy_one_campaign_snapshot.py': {'__module__': 'c0dc6663aeb6c5d4f658056f24830a22823daeb8c3f03a0426c9a2f4bb6b56c2'}, 'trading_runtime/strategy_one_evidence_snapshot.py': {'__module__': 'b231921b43f4b332e4e0d5141aa8f4920d9cbcc2b55ef8ce8d8f12f003415a75'}, 'trading_runtime/strategy_one_intent.py': {'strategy_one_entry_intent': '668fd1ad7b61ffb42401df4821900372ce0a2cc705903bfc1c0a847c88b44380'}, 'trading_runtime/strategy_one_management_snapshot.py': {'_project_manager_snapshot_scalar': 'e767c4cf4770e1010325d9633f89439b4ec42e347feb3c79c5fb38acdf751fb1', 'attach_committed_momentum_sources': '6300b9d91455b4a0d73dce569de7096b40134bfef64ad1804542375a98e29e1c', 'load_attested_manager_snapshot': 'b7334c9c67638563a9cacdeb2376cabb1a376a372eac47389f24f5bf19602a3e', 'project_manager_snapshot': 'eef770c23f2fb2679677f24efa41dc4f22a6bca2d61e729e3f6f48d30f5c5008', 'publish_manager_snapshot': '04aaa35af64ca161463ab2107ee92d40753b17b2f71b75d91a9ce95539410364', 'restore_manager_snapshot': 'f6f786343523ee899d73e9c9f561e9b0815f35208625db374325e523d59e6f17'}, 'trading_runtime/strategy_one_oms_observation_snapshot.py': {'__module__': 'd8f2ad09022bb5af10c3257113a604b32c3d6c0e2a263e8eb9c0a0d45731033b'}, 'trading_runtime/strategy_persistent_risk_failure.py': {'__module__': '62bbdbcc400315b7c63a3b3260dbaae0247cc128cadd26969075deacd61a3e52'}, 'trading_runtime/strategy_premarket_quarter_risk_failure.py': {'__module__': '87a7e1941a30e09f0e3b463187d9914f8389186a528d9f706ec76b5e6ea529b9'}, 'trading_runtime/strategy_registry.py': {'initialize_numbered_fixed_strategies': '068193df13a92c03a78858dd616dfd8a1eb29d27f3f1783d9a058afadc4b47b9', 'installed_numbered_fixed_strategy_numbers': '43b186df8d4ff46a5fbe9258e0949853a1554225dca0e5e10a049e65ed21bc63', 'numbered_strategy_parent': '910d21113065e5233063a2f17c3a943e927f4af978e0da259dc0ef170bcbf3b5'}, 'trading_runtime/strategy_rising_momentum_entry.py': {'__module__': '26f5e82b33a5e7e4126fd703d9748ca3ee14b3696df4f6b9eddb79db96e05ea7'}, 'trading_runtime/strategy_rising_momentum_witness.py': {'__module__': '6d5bd7f4a11ae8f2e05e4138a29d7cde0c53efb8945b38cfe117f82d1d05c4ee'}, 'trading_runtime/strategy_strong_ten_second_momentum.py': {'__module__': '70071f8696a3675e4a7344328d65327a84b539cb7e03f09c4fae49542a74dba5'}, 'trading_runtime/strategy_thirty_release.py': {'__module__': '049384140302bd83801e7b16447263c1847aa9ff7d2a4853bea5eb10aad84ea2'}, 'trading_runtime/strategy_twenty_eight_release.py': {'__module__': '0cccc9b6115abd744a09d760482f35a317f0d1f795b70fd580a7c9c1730fe6a8'}, 'trading_runtime/strategy_twenty_five_release.py': {'__module__': '1bbba29e4542af0fbe42ef7d07ebe852926039910c02d0680d1ad429def83f3a'}, 'trading_runtime/strategy_twenty_four_release.py': {'__module__': '7913d809cbe160b2d2018c63f4749ee2dd085bc4a1f6fa8412dde9c5168f5859'}, 'trading_runtime/strategy_twenty_nine_release.py': {'__module__': '58f134b9c6f17f5448d536a00779cddf32a4a43d172edeed0137fe02e348e0e7'}, 'trading_runtime/strategy_twenty_one_release.py': {'__module__': '0255b201419a032c712acf7de0fb5c45f1fe78d502d47b64c4c247031ab35679'}, 'trading_runtime/strategy_twenty_release.py': {'__module__': 'ec84c03c9e2b5d314be5b77204fa27904285123db8c54563459fa19a9724d281'}, 'trading_runtime/strategy_twenty_seven_release.py': {'__module__': '25b56cd9b46abc53eda393573a8a0ceca991e9037eb6869e0ab17b51192d2348'}, 'trading_runtime/strategy_twenty_six_release.py': {'__module__': '7344cb7fcdcb6cd3f6e10aea1400fe4b8e9e721e11294eab402d63576e901440'}, 'trading_runtime/strategy_twenty_three_release.py': {'__module__': 'e43951d61064e390078f9ac25d78eca0c000e0d33b28c54e663b71f644511c4f'}, 'trading_runtime/strategy_twenty_two_release.py': {'__module__': 'dfd5e4ccca3a285d278649a3374412e24638e29e62f57cf62f3db64c902d830f'}, 'trading_runtime/strategy_zero_regime_risk_failure.py': {'__module__': '2cca428561093253b73728c7c52a41b9e91dcf622f28fdd7ca93bb66660e8cd1'}, 'trading_runtime/entry_momentum_growth.py': {'__module__': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c'}, 'backend/backtest_declared_initial_momentum.py': {'__module__': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81'}, 'backend/source_ast_summary.py': {'__module__': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'}}


def certify_rising_momentum_entry_source(*, source_overrides: dict[str, Path] | None = None) -> str:
    """Fail closed when any reviewed Strategy 13 source or authority route changes.

    Covers exact producer attempt/key/Float64 reads, necessary-condition I/O
    pruning, scalar/native agreement, guarded Portfolio admission, exact two
    normalized companions, direct/compound commit seals and cold manager joins.
    Test-only path overrides allow mutations without changing installed files.
    """
    overrides = source_overrides or {}
    if set(overrides) - set(_RISING_MOMENTUM_REVIEWED_AST):
        raise ValueError("Strategy 13 source override is outside reviewed authority")
    root = Path(__file__).parents[1]
    observations = []
    for relative, expected in _RISING_MOMENTUM_REVIEWED_AST.items():
        path = overrides.get(relative, root / relative)
        source = path.read_text(encoding="utf-8")
        try:
            summaries = canonical_symbol_ast_summary(source, tuple(expected))
        except SyntaxError as exc:
            raise ValueError("Strategy 13 source cannot be parsed: " + relative) from exc
        if len(summaries) != len(expected):
            raise ValueError("Strategy 13 source summary shape changed: " + relative)
        for (name, digest), summary in zip(expected.items(), summaries):
            if (summary.name != name or len(summary.digests) != 1
                    or summary.digests[0] != digest):
                raise ValueError("Strategy 13 reviewed source authority changed: " + relative + ":" + name)
        observations.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observations, separators=(",", ":")).encode()).hexdigest()


_SESSION_EXIT_REVIEWED_AST = {'backend/backtest_journal_memory.py': {'append_numbered_session_exit_intent': '446960d28eda324257f9d51eed6d38dd00c1843d7976198be308ad918ac7bbe5'}, 'trading_runtime/numbered_fixed_strategy.py': {'_SESSION_EXIT_REASONS': '178f0fbbfd4a4e572fe0217a4c0437a63ce9d082c7903a165f6a866d671fa3c6', 'numbered_session_exit_reason': '210f5211d9f9a3ca40a881298b00de8be3465a21941376ee44885efe952c7565'}, 'trading_runtime/numbered_session_exit.py': {'numbered_session_exit_intent': 'ef9bf3c91d18c5059f3d58ea844397a0d764b7cad9188a865a4d7260e85d0550'}, 'trading_runtime/runtime.py': {'_execute_intents': 'b10f5cbfc1efdbb9677b729138eaa6869806487a372430685b4a36339a92980c'}}


def certify_numbered_session_exit_reason_source(*, source_overrides=None) -> str:
    """Bind the shared reason map and all three real proposal/admission paths.

    Full reviewed functions retain surrounding scalar source, mode and
    provenance guards. Mutation tests must not bypass a caller or the map.
    """
    overrides = source_overrides or {}
    if set(overrides) - set(_SESSION_EXIT_REVIEWED_AST):
        raise ValueError("Session-exit source override is outside reviewed authority")
    root = Path(__file__).parents[1]
    observed = []
    for relative, expected in _SESSION_EXIT_REVIEWED_AST.items():
        source = overrides.get(relative, root / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for name, digest in expected.items():
            if name == "_SESSION_EXIT_REASONS":
                nodes = [node for node in tree.body if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name) and target.id == name
                                 for target in node.targets)]
            else:
                nodes = [node for node in ast.walk(tree)
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and node.name == name]
            if len(nodes) != 1 or sha256(ast.unparse(nodes[0]).encode()).hexdigest() != digest:
                raise ValueError("Numbered session-exit reviewed authority changed: " + relative + ":" + name)
        observed.append((relative, sha256(source.encode()).hexdigest()))
    return sha256(json.dumps(observed, separators=(",", ":")).encode()).hexdigest()
