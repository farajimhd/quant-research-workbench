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
    expected = "return strategy_id == STRATEGY_ID and type(revision) is int and (revision in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 49, 50, 51, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(revision, 'strategy-fourteen-numbered-admission-v1') or declared_automatic_ladder_release(revision))"
    if (len(predicates) != 1 or len(predicates[0].body) != 1
            or ast.unparse(predicates[0].body[0]) != expected):
        raise ValueError("Numbered fixed identity whitelist changed")
    return sha256(source.encode()).hexdigest()


def certify_numbered_fixed_v4_projection(strategy_number: int) -> str:
    """Extend the full inventory proof with Strategy 2's explicit session lane."""
    if type(strategy_number) is int and strategy_number == 76:
        from .backtest_strategy_seventy_six_certification import certify_strategy_seventy_six_source
        from src.trading_runtime.strategy_seventy_six_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        from .backtest_frozen_parent_source import certify_frozen_performance_parent_source
        parent_proof = certify_frozen_performance_parent_source().native_proof
        additional_proof = certify_strategy_seventy_six_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(76) != release:
            raise ValueError("Strategy76 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 76).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 75:
        from .backtest_strategy_seventy_five_certification import certify_strategy_seventy_five_source
        from src.trading_runtime.strategy_seventy_five_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_seventy_five_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(75) != release:
            raise ValueError("Strategy75 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 75).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 78:
        from .backtest_strategy_seventy_eight_certification import certify_strategy_seventy_eight_source
        from src.trading_runtime.strategy_seventy_eight_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_seventy_eight_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(78) != release:
            raise ValueError("Strategy78 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 78).verify()
        from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy, DeclaredFixedStrategyContract
        from src.trading_runtime.strategy_seventy_eight_contract import strategy_seventy_eight_contract
        registered_contract = numbered_fixed_strategy(78)
        if type(registered_contract) is not DeclaredFixedStrategyContract or registered_contract != strategy_seventy_eight_contract():
            raise ValueError("Strategy78 registered canonical contract differs")
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 73:
        from .backtest_strategy_seventy_three_certification import certify_strategy_seventy_three_source
        from src.trading_runtime.strategy_seventy_three_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_seventy_three_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(73) != release:
            raise ValueError("Strategy73 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 73).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 74:
        from .backtest_strategy_seventy_four_certification import certify_strategy_seventy_four_source
        from src.trading_runtime.strategy_seventy_four_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_seventy_four_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(74) != release:
            raise ValueError("Strategy74 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 74).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 72:
        from .backtest_strategy_seventy_two_certification import certify_strategy_seventy_two_source
        from src.trading_runtime.strategy_seventy_two_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_seventy_two_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(72) != release:
            raise ValueError("Strategy72 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 72).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 71:
        from .backtest_strategy_seventy_one_certification import certify_strategy_seventy_one_source
        from src.trading_runtime.strategy_seventy_one_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_seventy_one_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(71) != release:
            raise ValueError("Strategy71 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 71).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 70:
        from .backtest_strategy_seventy_certification import certify_strategy_seventy_source
        from src.trading_runtime.strategy_seventy_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_seventy_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(70) != release:
            raise ValueError("Strategy70 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 70).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 69:
        from .backtest_strategy_sixty_nine_certification import certify_strategy_sixty_nine_source
        from src.trading_runtime.strategy_sixty_nine_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_sixty_nine_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(69) != release:
            raise ValueError("Strategy69 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 69).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 68:
        from .backtest_strategy_sixty_eight_certification import certify_strategy_sixty_eight_source
        from src.trading_runtime.strategy_sixty_eight_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_sixty_eight_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(68) != release:
            raise ValueError("Strategy68 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 68).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 66:
        from .backtest_strategy_sixty_six_certification import certify_strategy_sixty_six_source
        from src.trading_runtime.strategy_sixty_six_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_sixty_six_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(66) != release:
            raise ValueError("Strategy66 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 66).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
    if type(strategy_number) is int and strategy_number == 64:
        from .backtest_strategy_sixty_four_certification import certify_strategy_sixty_four_source
        from src.trading_runtime.strategy_sixty_four_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_sixty_four_source()
        release = release_contract()
        release.verify()
        if numbered_strategy(64) != release:
            raise ValueError("Strategy64 installed release differs from source approval")
        fixed_strategy_executor(release.executor_strategy_id, 64).verify()
        return sha256(json.dumps((parent_proof, additional_proof, release.approved_digest),
                                 separators=(',', ':')).encode()).hexdigest()
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
    if type(strategy_number) is int and strategy_number == 65:
        from .backtest_strategy_sixty_five_certification import certify_strategy_sixty_five_source
        from src.trading_runtime.strategy_sixty_five_release import release_contract
        from src.trading_runtime.strategy_registry import numbered_strategy, fixed_strategy_executor
        parent_proof = certify_numbered_fixed_v4_projection(42)
        additional_proof = certify_strategy_sixty_five_source()
        release=release_contract();release.verify()
        if numbered_strategy(65) != release:
            raise ValueError('Strategy65 installed release differs from complete source approval')
        fixed_strategy_executor(release.executor_strategy_id,65).verify()
        return sha256(json.dumps((parent_proof,additional_proof,release.approved_digest),separators=(',', ':')).encode()).hexdigest()
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
                and ast.unparse(node.test) == "runtime.config.strategy_revision in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(runtime.config.strategy_revision, 'strategy-thirteen-rising-completed-momentum-v1')"]
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
            != "zero_regime_risk_failure if self.contract.strategy_number in (30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(self.contract.strategy_number, 'strategy-thirty-zero-regime-original-risk-failure-v1') else persistent_risk_failure if self.contract.strategy_number == 29 else premarket_quarter_risk_failure if self.contract.strategy_number in (25, 26, 27, 28) else early_followthrough_failure if self.contract.strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24) else followthrough_failure"):
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
            != "zero_regime_risk_failure if self.contract.strategy_number in (30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(self.contract.strategy_number, 'strategy-thirty-zero-regime-original-risk-failure-v1') else persistent_risk_failure if self.contract.strategy_number == 29 else premarket_quarter_risk_failure if self.contract.strategy_number in (25, 26, 27, 28) else early_followthrough_failure if self.contract.strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24) else followthrough_failure"):
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



_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES = ('src/backend/backtest_fixed_journal_bootstrap.py',
 'src/backend/backtest_trade_proposal_v3.py',
 'src/backend/backtest_typed_projection.py',
 'src/backend/backtest_typed_publisher.py',
 'src/backend/replay_run_service.py',
 'src/trading_runtime/arte_trade_proposal_children.py',
 'src/trading_runtime/arte_trade_proposal_projection.py',
 'src/trading_runtime/drawdown_measure_authority.py',
 'src/trading_runtime/drawdown_measure_policy.py',
 'src/trading_runtime/numbered_fixed_strategy.py',
 'src/trading_runtime/portfolio.py',
 'src/trading_runtime/risk_supervisor.py',
 'src/trading_runtime/runtime.py',
 'src/trading_runtime/journal_contract.py',
 'src/trading_runtime/arte_journal_projection.py',
 'src/trading_runtime/arte_journal_writer.py',
 'src/trading_runtime/arte_journal_reader.py',
 'src/trading_runtime/arte_portfolio_snapshot.py',
 'src/backend/backtest_strategy_one_configuration.py',
 'src/trading_runtime/strategy_registry.py',
 'src/backend/source_ast_summary.py',
 'src/backend/backtest_fixed_v4_certification.py',
 'src/trading_runtime/squeeze_ladder_geometry.py',
 'src/backend/backtest_declared_ladder_plan.py',
 'src/backend/backtest_ladder_source_authority.py',
 'src/trading_runtime/squeeze_ladder_automatic.py',
 'src/trading_runtime/confirmed_original_risk_failure.py',
 'src/trading_runtime/arte_original_risk_diagnostic_v4.py',
 'src/trading_runtime/original_risk_diagnostic_profile.py',
 'src/backend/backtest_confirmed_original_risk_source.py',
 'src/backend/backtest_market_data.py',
 'src/trading_runtime/original_risk_checkpoint.py',
 'src/trading_runtime/original_risk_pending_snapshot.py',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py',
 'src/trading_runtime/premarket_confirmed_original_risk.py',
 'src/trading_runtime/_checkpoint_prefix_read.py')
_DRAWDOWN_CORE_REVIEWED_AST = {'src/backend/backtest_fixed_journal_bootstrap.py': 'b1e67803f6ddc4b3c28e4030febd61cc8800d0f6d3e21f151b88fd24f4ce689d',
 'src/backend/backtest_trade_proposal_v3.py': '105c0861f4f64ca8a472a6df5eab6b757672c2d372a486eea381b25a26bfb986',
 'src/backend/backtest_typed_projection.py': '30e7ad90b8d41270aa80ced61ed1e9b3fdd539265ff7395b9285308237db3a9e',
 'src/backend/backtest_typed_publisher.py': '89ebb43227635813d78c07f3751cca2f93cd095c26cc1f9e5b1f809e3ca2da61',
 'src/backend/replay_run_service.py': '341053ab5a8a8c0d51dfafa62893237801379896aa690b2185900d6f66dec8c1',
 'src/trading_runtime/arte_trade_proposal_children.py': 'c306edb3cdf3ac0a1dd0935c15b305fed4e52736794cbaac353d86245ed41272',
 'src/trading_runtime/arte_trade_proposal_projection.py': '3db1d832a3f703a011c97d399e7cc725143201955674fd8d2aba61b796c11d55',
 'src/trading_runtime/drawdown_measure_authority.py': '5c3e9281d3ef17eeeb9e75e9847b30de5619aef598b85c2d0e687ed2befbf87d',
 'src/trading_runtime/drawdown_measure_policy.py': '8db141b2fd01030439e5c6c9a4040a6a3462183ae7575abfe3fcd17c1f7d8f96',
 'src/trading_runtime/numbered_fixed_strategy.py': 'b37f53e1468912b2b70846bc93f24a86d9733b909562d6b5e9686545574254f8',
 'src/trading_runtime/portfolio.py': 'd14c31fd8d4c392f2f98c748b401a0b50042524a4d0b56a586b059e19056bf8f',
 'src/trading_runtime/risk_supervisor.py': '412c0d2406acd76d49771b562fea96bec2a1331b8a1cc4e6cd483d99c4fbd975',
 'src/trading_runtime/runtime.py': '6e8c043db1d62b04560fc581e46f3f7d0382c49285975263dc6cff4b40dd8dc8',
 'src/trading_runtime/journal_contract.py': '130e8b70f3708cd30f0524b035a0c99cded407bf6b1fc39a610cb7e19458da28',
 'src/trading_runtime/arte_journal_projection.py': '9c78a31a6bad5fb1026053f0b5552c9ed37bf717c75143da25f14d9df3e08efc',
 'src/trading_runtime/arte_journal_writer.py': 'faafb31844ea3c0dfd7c7d5f205552d01c3e6652179612ef57298a918f93d37c',
 'src/trading_runtime/arte_journal_reader.py': '71e11a8f9a853e9d2efa9153cdcb36a931726aec7412da5e9c241fd1713d2973',
 'src/trading_runtime/arte_portfolio_snapshot.py': '6a66afd31ac6665364929c40e293f6a11e8559165d9a46fbc852f69b0b55ea8e',
 'src/backend/backtest_strategy_one_configuration.py': 'cc290861a5d58ad089ac1e41352d9ad77842cfdfe48503953343c28c9accfeab',
 'src/trading_runtime/strategy_registry.py': '65231a384e85b66cd8282d61493c6b88a02e02147719be0e5d92799fa7d6e508',
 'src/backend/source_ast_summary.py': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995',
 'src/backend/backtest_fixed_v4_certification.py': {'certify_drawdown_measure_core_source': '4c438d83cc00174cd4089de3c1fc9fa738e481baf793c7cf5af1734cdf8a3741',
                                                    'certify_strategy_one_v4_projection': '5a07632380270c1795ef9331e80ddc014599aac0bdd1e80d835f862c72e05747'},
 'src/trading_runtime/squeeze_ladder_geometry.py': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d',
 'src/backend/backtest_declared_ladder_plan.py': {'automatic_policy': 'f5383587a2b55fcd05c6c6f0872d7e641b1059d6ee229094f5a10ef15b5c2669'},
 'src/backend/backtest_ladder_source_authority.py': {'declared_ladder_policy': '56c0bef2dbea0bccd752d45293520c1ce8ebec0b3e2829c48c5a2a7eccbe360c'},
 'src/trading_runtime/squeeze_ladder_automatic.py': {'AutomaticLadderPolicy': '8e6b530d31208cc0c139f51eee83da902e67a16f532118b6f7eacc90a30f936f'},
 'src/trading_runtime/confirmed_original_risk_failure.py': '738493dce849c08fd7e1d4f5c24a7c3188bc772a183d9083a86713177aba6f39',
 'src/trading_runtime/arte_original_risk_diagnostic_v4.py': '747bd14c7cd27adc1151770c7b946d6c14074a2e813f167dcf7e4241604176d2',
 'src/trading_runtime/original_risk_diagnostic_profile.py': '595f15675892f4b9f59cb1ee1b19536e59ab47ca563f55971b39811614a4298c',
 'src/backend/backtest_confirmed_original_risk_source.py': 'f51caf0f2197a606d02de3972aaeb41b9291dd487a2549fe2fd9987162bfa859',
 'src/backend/backtest_market_data.py': 'ad4135f16a0979b5af821b0c00c294511635b3b84b37b90f7bdff79e503cf9d3',
 'src/trading_runtime/original_risk_checkpoint.py': '7e39fb2e7cb189ab080b7bb46e53563c73fbafb8c349f23ff1ca5be1fda3af07',
 'src/trading_runtime/original_risk_pending_snapshot.py': '0c18cf677683921ff30f41c6b5dde78a43e5ad190e34bdde572baeabb14d9cac',
 'src/trading_runtime/strategy_liquidity_fade_financial_checkpoint.py': '1ff8cda4665f3fefd70fef5a56ccfdc7cf77a55caf819639f17e38aeffe36739',
 'src/trading_runtime/strategy_one_broker_match_snapshot.py': '1c4571237c9231fbe1660349be684adaf52a2b34d19b4c49c9489af9392a1a7c',
 'src/trading_runtime/premarket_confirmed_original_risk.py': '907931a13a9e71e9b7daf96ae15be0e51f356b9c246fc153a7a234bb1475a7fb',
 'src/trading_runtime/_checkpoint_prefix_read.py': '62c510588e1a0d7b53f07703bab2f320b84cef11972c99fa83ec5e8179fe33c8'}


def certify_drawdown_measure_core_source(*, source_overrides: dict[str, Path] | None = None) -> str:
    """Seal the shared legacy/declared numeric owner and recovery dependency closure."""
    if set(_DRAWDOWN_CORE_REVIEWED_AST) != set(_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES):
        raise ValueError("Drawdown core source keyset changed")
    metadata_anchor = '7d347e53f0524fd4674d4f6a39b151049b0def7ae01cee5821633d5cc2e43dfb'
    self_relative = "src/backend/backtest_fixed_v4_certification.py"
    self_symbols = ("certify_drawdown_measure_core_source", "certify_strategy_one_v4_projection")
    classifier_symbols = {
        "src/backend/backtest_declared_ladder_plan.py": ("automatic_policy",),
        "src/backend/backtest_ladder_source_authority.py": ("declared_ladder_policy",),
        "src/trading_runtime/squeeze_ladder_automatic.py": ("AutomaticLadderPolicy",),
    }
    if (type(_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES) is not tuple
            or any(type(path) is not str for path in _DRAWDOWN_CORE_REQUIRED_SOURCE_FILES)
            or len(set(_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES)) != len(_DRAWDOWN_CORE_REQUIRED_SOURCE_FILES)
            or type(_DRAWDOWN_CORE_REVIEWED_AST) is not dict
            or type(_DRAWDOWN_CORE_REVIEWED_AST.get(self_relative)) is not dict
            or set(_DRAWDOWN_CORE_REVIEWED_AST[self_relative]) != set(self_symbols)
            or any(type(value) is not str or len(value) != 64 for value in _DRAWDOWN_CORE_REVIEWED_AST[self_relative].values())
            or any(type(_DRAWDOWN_CORE_REVIEWED_AST.get(path)) is not dict
                   or tuple(_DRAWDOWN_CORE_REVIEWED_AST[path]) != selectors
                   or any(type(value) is not str or len(value) != 64
                          for value in _DRAWDOWN_CORE_REVIEWED_AST[path].values())
                   for path, selectors in classifier_symbols.items())
            or any(type(value) is not str for path, value in _DRAWDOWN_CORE_REVIEWED_AST.items()
                   if path != self_relative and path not in classifier_symbols)):
        raise ValueError("Drawdown core metadata shape changed")
    metadata = {
        "required": _DRAWDOWN_CORE_REQUIRED_SOURCE_FILES,
        "sources": {path: value for path, value in _DRAWDOWN_CORE_REVIEWED_AST.items() if path != self_relative},
        "self_symbols": self_symbols,
        "classifier_symbols": classifier_symbols,
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
                "classifier_symbols": classifier_symbols,
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
                  and ast.unparse(n.test) == "row['strategy_number'] in (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(row['strategy_number'], 'strategy-twelve-recent-bos-entry-v1')"]
    if len(raw_routes) != 1 or "if not recent_bos_entry(boundary_ms=row['boundary_ms'], bos_break_boundary_ms=row['bos_break_boundary_ms']):" not in ast.unparse(raw_routes[0]):
        raise ValueError("Strategy 12 raw normalized entry authority must reject forged stale BOS")
    return sha256(json.dumps(tuple((path.name, sha256(source.encode()).hexdigest())
        for path, source in zip(paths, sources)), separators=(",", ":")).encode()).hexdigest()


# Reviewed Strategy 13 source observations, filter, submission and durability
# routes. Exact canonical AST seals bind the tested implementation; a later
# behavior change gets a new numbered release. Comments/line endings do not
# affect these seals. The run separately pins its complete backend fingerprint.
_RISING_MOMENTUM_REVIEWED_AST = {'backend/backtest_journal_memory.py': {'BacktestMemoryJournal': 'd8a28946e7e701134c97fd7e230dd27ff4047b0228b3b0fbd94a30f12ac61f5a',
                                        'mark_fenced': '2e02db5801e914b3833499b7750afecce8df6bf26c6ba8e8675cab5d9ed58ae4',
                                        'append_profit_giveback_exit': '3fd57b6f79cd5aaa71bb3b6411000e8d6bcd0d9f749d07c550e4748769065e35',
                                        'profit_giveback_exit_for_record': '961b81e4f28658ccfafcdeae95bac8c0c1aa4951f989a86cd819bab86212ed40',
                                        'append_followthrough_exit': '8ab6f33258500d58971d3060b8977a077ebb68a9284762d8a58f07cc2fda194b',
                                        'append_strategy_one_intent': '67902b54c827a8ebefe3f7b27ea51546711f3ada44d28e82d442cac9bbb6395e',
                                        'append_strategy_one_protection_intent': 'a71dad66fdecd9b1ef51ba63a539153b5dcf96a861cb4d1b40ebdd9705a9f80b'},
 'backend/backtest_market_plan_cache.py': {'selected_product_inventory_fingerprint': '2a24f97bf8d162e0d979c01fedbaba02de6e37cdaa0da48c482700c31211f355'},
 'backend/backtest_saved_source_authority.py': {'__module__': 'b0f17ce10cdfdbaf09315e64899dff9c6ef87b0fd520bfa334649401bcafd6bf'},
 'backend/backtest_strategy_certified_price_break.py': {'__module__': 'e1fa545fbbd95b4784e4cf2e0ef320276a4ff525683f680ddea6fc0ccd4c1a38'},
 'backend/backtest_strategy_first_price_source.py': {'__module__': 'a2557a2973a3ad1371fd9d97ffe740cf0687aa510372b1d6471d0284f92d2146'},
 'backend/backtest_strategy_initial_momentum.py': {'__module__': '954446cab169240a183801637b4f61b27f90d45467b065ef428760d1ed5a48df'},
 'backend/backtest_strategy_initial_momentum_growth.py': {'__module__': '33bf35371d2216a5361e735959cdd1e48a65be3a5d8f04ec3d79cd277d199485'},
 'backend/backtest_strategy_initial_price_break.py': {'__module__': 'aa5968f100749c56e97c66279ba4bd1feb583e9b3a8f8a978718f234b2d98ff6'},
 'backend/backtest_strategy_initial_ten_percent.py': {'__module__': '73b8b04654cd8ebef2a8906908bac4c82fa06616fb4d50d19f73442f7f07f420'},
 'backend/backtest_strategy_one_configuration.py': {'__module__': 'cc290861a5d58ad089ac1e41352d9ad77842cfdfe48503953343c28c9accfeab'},
 'backend/backtest_strategy_one_coordinator.py': {'run_strategy_one_proposals': 'a500ecf82713e24ba1d21d53c12528db1096321fbe429a45c212cf468d87d54d'},
 'backend/backtest_strategy_one_execution.py': {'run_certified_strategy_one_session': '90486c747410a0da727e4ba0075f26a714a37c861e0a2d830c2dbedcc2a3dff4',
                                                'run_strategy_one_fixed_session': '48ddc31d1118ef82d893071e4b5d26dead294fc929c565123a6675d22e70a492'},
 'backend/backtest_strategy_one_management.py': {'__init__': '70f984e9bd8e309bbc69b0b7e6d97864a943a9852cee1b2111fb4dc30e6bab2b',
                                                 'profit_arming_requests': '4f49d7c80950ee39e014a51b4edc4f9db2cdd2f92c1efcff2b2dc253c1e953b7',
                                                 'accept_profit_arming_references': '3887b6c277a19ddd561fe092695fdd895d5c6fd8616e434cfc644853767eafdd',
                                                 '_validate_capture': '665a02e826fab39be41328ca35ceb1d4d8f87614a096d6ec4c018e1536613e53',
                                                 'on_management': '2eabc20004143756aac6d0797877381f447125589865ba66116f3cf3aa53d5ed',
                                                 'restore_state': 'ce06572c0dec8ded353b84f0b993c27bf1e76829028653ce39de3a16e0640225'},
 'backend/backtest_strategy_one_stateful.py': {'propose_certified_strategy_one_entry': '317695e0ecff96c835c6e897225d9414d4940d86aadfdf6a10b9bea741817eb3'},
 'backend/backtest_strategy_one_static_gate.py': {'compile_static_entry_gate': 'e6d2f747557dfbe005d492fa4a24ef671f47736961196cd1ca27ec2da438d0e5'},
 'backend/backtest_strategy_one_v7_interval_store.py': {'certify_v7_interval_plan': '3e5a6b0150e9225821f165971f4c2526d41c2d9934c50bdbaa9101c295c219aa'},
 'backend/backtest_strategy_rising_momentum.py': {'__module__': 'c0f4a1084b29088a1df63cdeb5c1b82b3d467fe2a657ee08ab37037bd6a6f245'},
 'backend/backtest_typed_projection.py': {'project_pending_backtest_v4_prefix': 'bebcb188fa512e94b6a332c17b67bfbb1223b765a15167ead14a6abfff448a78'},
 'backend/backtest_typed_publisher.py': {'_drain': '8fefc301a509f4dc40c031a15767343027df0783792c77cb23800a932969cd22',
                                         '_prepare_batches': 'e618d19814c8c700e5573c6217f0baf66e9d39ca70a2ed3fd15cda17bb5d19d8',
                                         '_publish_terminal_v4': 'f9eabf5aca42abc249a30d7d164fc8689a56b16c284c2a8bb523d823a3932ce4',
                                         'bind_first_price_source': 'b2a38201fa4b8b489ae2c8ac1e5ee6259d8f8977f711e0f62d48ee9f24a2442e'},
 'backend/backtest_v4_history.py': {'__module__': '9ed3f860d27d2b6f776d4b42c0e9225fc84a75744504fa9dce60dcdd1326bd23'},
 'backend/backtest_v4_saved_review.py': {'_saved_twenty_price_source': 'b5c9f1deff7963330e6aeb7963284fb2f3719f14dada69224208458e1ec5670b',
                                         '_terminal_attestation': '9701ae6892dfee469545337694f2b8377975e221b028a084290f70f3eba9f104'},
 'backend/historical_runtime_versions.py': {'__module__': '96941bdcb6b84439c3ba3f0d1fc0deb9cde238746088d1b284284aa0969857ec'},
 'backend/replay_run_service.py': {'_save_restart_checkpoint_responsive': '5f54ab88d56dbd588090b9de2e7404da167ae261a82eeb5b53f1830130ee568e',
                                   '_confirm_profit_arming_checkpoint': '2f2d7e832932067bc4be0e25b4a0013f9f43cd82e3a8d48d8c61de595cf46b4e',
                                   '_run_strategy_one_fixed_days': 'f27dc9c24a6f6ad86ce50525b697d571d13e4103502ca82a20e9f8015be9b416',
                                   'backtest_preflight': 'ca11be31c88e6ff6fbecd1c5d3f33d3a4304dd3951a1191e44acdadf067b98b1'},
 'trading_runtime/arte_backtest_definition.py': {'_reconstruct_backtest_definition': '5848ac310bcb92ba03e71eae9dc962f757191951fb77389c46eaa946779e86d0',
                                                 'reconstruct_backtest_definition_from_arte': 'f9f64dcd409d411db63d2057246b597e9a499df7b899f4afd46b708cd3f08f61',
                                                 'reconstruct_saved_review_definition_from_arte': '8ea00e67695146cbaee08d6825d4fd55eb9300e5ef16abcf21b98282658ddc2e'},
 'trading_runtime/arte_first_price_entry_v4.py': {'__module__': 'c968e50cb42d4326f9499b20134ec6500990058a6415aed581f373fb2d2efec6'},
 'trading_runtime/arte_followthrough_failure_v4.py': {'_source_entry': '2e3eb76a2d8ade0055c9792254311e1af4f5ebde2933b226530c2b2c39f80b46',
                                                      'seal_followthrough_rows': '90733ea1ef5323c5eeb7f76895b96a7298b6fd6a514380bb475c44788020a422',
                                                      'validate_numbered_failure': 'bb8bbe7487017dcfe6a867386615ecb3a0d12162232b6115f1600d15a5b07948'},
 'trading_runtime/arte_initial_momentum_entry_v4.py': {'__module__': '037069b752fbc7af9de87ba44f53d3af79833cdb379872ce2554013456af21ea'},
 'trading_runtime/arte_journal_commit_v4.py': {'verified_batch_predecessor': 'e7f52f08e28175674cf663dbde1b17abf7bf53513884f7d2289fbdd4dfe63ecf',
                                               '_load_verified_details_v4': 'e167b8f519653d15465055ef2f1fcea26f6a1fcc548b859397919c68c420071f',
                                               '_publish_sealed_batch_v4': 'de0d4434d7eb6853167b7b67e4ee247df34624e6c6f9acab24c5e27e9fa853f9',
                                               '_publish_typed_batch_v4': '2143bf79a6e056c20e9b17cc7dc457c876a062fd9c00681781109d9711d7a3a2',
                                               '_validate_strategy_one_entry_link': '23afcdd6fc9bd51c2d6d7bc3a708cf283b167dc97feed48cb6f4a6b3bb51f22e',
                                               'load_verified_commit_v4': 'cc7f0f8891168efa70dad7a833e0dc1e73fd2c571e4480323db1ad8f5b758e4c',
                                               'load_verified_v4_prefix': 'f690eaef0891df060ba50c3a5128f0e944019823f717f1d2c3c27bf2c0790182',
                                               'load_writer_v4_snapshot_prefix': 'd4a6aaa55ed12b6a5916af7f7ca2ea80e134d8bfe76f0743a4b282c93895582a',
                                               'publish_strategy_one_entry_batch_v4': 'b1e928db522a5aeda0887c897b42db5cb7474a98b2746f79d802971c2795550e',
                                               'publish_terminal_typed_batch_v4': 'f352365d43e2dbc998a206a4afa54a4b2600ce0106ee410098a4a48b73228b88'},
 'trading_runtime/arte_journal_compound_v4.py': {'__module__': '05b28c131df86e9b743117c1c801eec7384995b0bbe8c8b80c9afb53e183eb4c',
                                                 '_publication_kwargs': '0d3756ee815814fcbcb3600607872650825fcd99556c323f897291bce8943089',
                                                 '_unit_children': '2834a73090637128f51c1ac6ebeb73f22c424d2b7a2035a53c7a7f6862a53f85',
                                                 'prepare_compound_v4_families': 'c2f24102d75ba4322a2e3489af7fbcfdee6dd034c0d12c6f5ad3c8c8184f1610'},
 'trading_runtime/arte_journal_writer.py': {'_ProfitPublicationUnit': '7cf5b6eec9ddf368a1427d43fc5cfda7ae48b1efe661d66af970f160f82f2c72',
                                            'submit_compound_v4': '13761130508aa3187fd8964a4edab151a240b802a7b427d72c4b15f00a08ede9',
                                            'submit_profit_exit_v4': '0b1344d9735e100b5901ca3df69773968e838a738127d73df3510478c0e1c2fe',
                                            '_submit_profit_publication': '4b77a7990b79b6c2e3d83c21c32c0359f7c66606de6fc30faf95a9fcfd77aff4',
                                            'V4StrategyOneEntryBatch': '280acd4c131f26d5714db9036aed705f06db518ffba332269f520d35076c19c3',
                                            '_BrokerMatchSnapshotUnit': 'b9f57982ff159fc4c38acf5e2bea17e3edec5941ca5a3a339231949824982617',
                                            '_CampaignSnapshotUnit': '7d38bc41d265b2507a147da65bfa59c298fe0b77234ca611ede1c70367714a75',
                                            '_EvidenceSnapshotUnit': 'dd22c3b1bf5a3eac08d55fa86790b60e8cb854b7678c0b4866185c9208af400e',
                                            '_ManagerSnapshotUnit': '0fc3c933a83a2c48e0feb873cf3882e4f9ef89828e9543723469bb8facb7e18f',
                                            '_OmsObservationSnapshotUnit': 'f205cbfa584c3566500f0558ef3b08a44e8f07f56778cba9000a3667d7701001',
                                            '_TerminalBacktestUnit': 'f42ccbf195ea58f40dc4a78fa7494655b47b59558d7546191a21e3f5c0fa9f62',
                                            '_run': '0dd8ff9f80f955a67f413b383f3d2c3f81186707d7524458f135bfeee3b9d4c5',
                                            '_validate_checkpoint_price_source': '2cdc6c63fdaf1effa48c2e0e467d2582620e796f2ee4a383e297584ed39a6b74',
                                            'submit_broker_match_snapshot': 'ccb2ae1ec6caf555675db0b729e91636d06d35d408acde560f52e7056d7d6324',
                                            'submit_campaign_snapshot': 'fa09a5cf1c45b87aa873185b92267c21965b042ce0a216dfa2f0ec046ffdf85b',
                                            'submit_evidence_snapshot': '03c8628952e81825891a0f936db7a911b2984d20ef41772aeaeb9d0ff4a26674',
                                            'submit_manager_snapshot': '98455c7414c19d675e22163dbd94b8c2af4d2f96d24fe647d7b7c756d7e47bbe',
                                            'submit_oms_observation_snapshot': '79e7e068135f55029751ed1fd710b63ccdcb902825c972f73ac58e4a6bca25fe',
                                            'submit_terminal_backtest': '2cd75bbabb2057da72695abec3ca937c2d4370f6fe2391ea3612d378114c6344',
                                            'v4_journal_write_tables': '20ee05021d0ec95fc113482abcb0be770ba5d5407ebc6e4e903efe6982e4b3db',
                                            'v4_storage_contracts': '74673f56137f80754160338c3ca5c2b2008bcfcb05b40cdea7b7e20ab778faaf'},
 'trading_runtime/arte_profit_giveback_v4.py': {'__module__': '3e22f8f0c57d85a471cd15a63e18295517c617f9b8dc88eae5d1cc7908a70293'},
 'trading_runtime/strategy_profit_giveback.py': {'__module__': 'cc23fe27ca6db49c4139a481f364bfc26088b0dea01d1f542ea2f98f79072d10'},
 'trading_runtime/strategy_profit_giveback_exit.py': {'__module__': 'c8466273babe33b6c54ed898d446a6b1b68297fd2e37e69b27270a4bc804b49e'},
 'trading_runtime/strategy_profit_giveback_arm.py': {'__module__': 'ef5b2892793ecf7ef2d6957a0851c1c2010e1d4da5c21c7e0dbc5de46fc39f41'},
 'trading_runtime/strategy_profit_giveback_arm_reference.py': {'__module__': '87352243161430f4a0f88321acd26b93d3cf69d69a99594d1b01dabe6ee36cf6'},
 'trading_runtime/strategy_profit_giveback_source.py': {'__module__': 'b3e58e1bf6cbb1f77a79ddfd41a8ce7115c634dc80753877549f37947db75189'},
 'trading_runtime/arte_rising_momentum_entry_v4.py': {'__module__': '2f9b2689448c0592d99134286955618d0c312c29d8712d37f79310bffbb55d1d'},
 'trading_runtime/arte_strategy_one_entry_journal.py': {'load_committed_strategy_one_entry_page': 'c621bcba93248f5c92c1cc5c744954538923912fced2a4c4c5232ec7a2cac263',
                                                        'load_committed_strategy_one_source': '46abf12c734f64e6a90f283736145b6db5581dd75f763f06b3f033a59ce223db',
                                                        'project_strategy_one_entry_evidence': '62d8692c70c506d9baeedf6e2307e8ce231d9e25ca9829ca562722fc12caf060'},
 'trading_runtime/numbered_fixed_strategy.py': {'__module__': 'b37f53e1468912b2b70846bc93f24a86d9733b909562d6b5e9686545574254f8'},
 'trading_runtime/runtime.py': {'submit_followthrough_failure': 'c9e77b7822ae2102985b735fa9b6715f02336975cbde474016c061b8f15cfa53',
                                'submit_profit_giveback': 'fcd638576f375df9e6649800c40675f8a71520ce92020441bba3ca268f71df1c',
                                '_execute_intents': 'a707272b9d051588cc7f217e1993064c8fc4e0e0590d324b9a907297cf330f90',
                                '_strategy_one_entry_intent': 'c193305101040e954be086e2f46ccf560cd7c373fe3819157da9d35c045e5c63',
                                'bind_strategy_one_price_source': 'aa5289128f33c2be31c6f21113cf613414033d4de0890d43aa57e714d1a7d2e1',
                                'submit_strategy_one_proposal': 'd4c545ca3419db23d782f307eda0464d8f214f21a6d98a7ddcf7fbd97de72d23'},
 'trading_runtime/strategy_followthrough_exit.py': {'__module__': 'e5ed9aa87f606bed29e550756d11e7fa1445d9ffb47f69d4f1402bfdb17b9984'},
 'trading_runtime/strategy_initial_momentum_growth.py': {'__module__': '68d66854b639e67a5d3734aaaf1a61ce015963d6841746a59c4ad85390761606'},
 'trading_runtime/strategy_initial_price_break.py': {'__module__': '5279377acee015b28239ecd1949657fb1ce66731835a6190b686bf54a38ce3ce'},
 'trading_runtime/strategy_initial_strong_momentum.py': {'__module__': '65c6021a7a7c287682a502989fe03b638ee6aaa6ae1488e9a323a0e4c75f4c06'},
 'trading_runtime/strategy_initial_ten_percent.py': {'__module__': '086a330212aa01c2ddf70bdb8ddb2db70654f4b851662977ea86ec307be18be2'},
 'trading_runtime/strategy_one_broker_match_snapshot.py': {'__module__': '1c4571237c9231fbe1660349be684adaf52a2b34d19b4c49c9489af9392a1a7c'},
 'trading_runtime/strategy_one_campaign_snapshot.py': {'__module__': 'c0dc6663aeb6c5d4f658056f24830a22823daeb8c3f03a0426c9a2f4bb6b56c2'},
 'trading_runtime/strategy_one_evidence_snapshot.py': {'__module__': 'b231921b43f4b332e4e0d5141aa8f4920d9cbcc2b55ef8ce8d8f12f003415a75'},
 'trading_runtime/strategy_one_intent.py': {'strategy_one_entry_intent': '668fd1ad7b61ffb42401df4821900372ce0a2cc705903bfc1c0a847c88b44380'},
 'trading_runtime/strategy_one_management_snapshot.py': {'_project_manager_snapshot_scalar': 'a3c2ad89cf8615514e81d30867ba527c94c8dabf992151cfe567b6df6a990278',
                                                         'attach_committed_momentum_sources': '72adf83b4fae5986928f403afd7edde5040eaa7d14a7eeb3bf1db7987b31c56e',
                                                         'load_attested_manager_snapshot': '3c7626cc5be6991bdff5a436615022e8f58ec8a3f20c8f44ba85e8acdcb9cd16',
                                                         'project_manager_snapshot': '7fff6795650ef979434574a4dcc3782c2b6b7aa4fc596d7d88e3e9f0cc9cde00',
                                                         'publish_manager_snapshot': '3c633c9f8f7d567bb83ae0322e6a1dab13746df79664ffcb9110fc1821e432e1',
                                                         'restore_manager_snapshot': '053ee23cc386bcafd6f3fa2d0bc401a42b0b745614d1c7c3820fb0c06c299d76'},
 'trading_runtime/strategy_one_oms_observation_snapshot.py': {'__module__': 'd8f2ad09022bb5af10c3257113a604b32c3d6c0e2a263e8eb9c0a0d45731033b'},
 'trading_runtime/strategy_persistent_risk_failure.py': {'__module__': '62bbdbcc400315b7c63a3b3260dbaae0247cc128cadd26969075deacd61a3e52'},
 'trading_runtime/strategy_premarket_quarter_risk_failure.py': {'__module__': '87a7e1941a30e09f0e3b463187d9914f8389186a528d9f706ec76b5e6ea529b9'},
 'trading_runtime/strategy_registry.py': {'initialize_numbered_fixed_strategies': '8b16306d1287ddb4cea7bfb81fd77f6b47547c0788d45e75e6328a878980d5d8',
                                          'installed_numbered_fixed_strategy_numbers': '43b186df8d4ff46a5fbe9258e0949853a1554225dca0e5e10a049e65ed21bc63',
                                          'numbered_strategy_parent': '2ba61f5c41b8490094bfa7d54e2bcfebf784112efa11c8233e1603574d7ffe43'},
 'trading_runtime/strategy_rising_momentum_entry.py': {'__module__': '26f5e82b33a5e7e4126fd703d9748ca3ee14b3696df4f6b9eddb79db96e05ea7'},
 'trading_runtime/strategy_rising_momentum_witness.py': {'__module__': '8433d7be44361854221822708c23dbbc7899b106a16aa5d9f62d2e1825d6bf30'},
 'trading_runtime/strategy_strong_ten_second_momentum.py': {'__module__': '70071f8696a3675e4a7344328d65327a84b539cb7e03f09c4fae49542a74dba5'},
 'trading_runtime/strategy_thirty_release.py': {'__module__': '049384140302bd83801e7b16447263c1847aa9ff7d2a4853bea5eb10aad84ea2'},
 'trading_runtime/strategy_twenty_eight_release.py': {'__module__': '0cccc9b6115abd744a09d760482f35a317f0d1f795b70fd580a7c9c1730fe6a8'},
 'trading_runtime/strategy_twenty_five_release.py': {'__module__': '1bbba29e4542af0fbe42ef7d07ebe852926039910c02d0680d1ad429def83f3a'},
 'trading_runtime/strategy_twenty_four_release.py': {'__module__': '7913d809cbe160b2d2018c63f4749ee2dd085bc4a1f6fa8412dde9c5168f5859'},
 'trading_runtime/strategy_twenty_nine_release.py': {'__module__': '58f134b9c6f17f5448d536a00779cddf32a4a43d172edeed0137fe02e348e0e7'},
 'trading_runtime/strategy_twenty_one_release.py': {'__module__': '0255b201419a032c712acf7de0fb5c45f1fe78d502d47b64c4c247031ab35679'},
 'trading_runtime/strategy_twenty_release.py': {'__module__': 'ec84c03c9e2b5d314be5b77204fa27904285123db8c54563459fa19a9724d281'},
 'trading_runtime/strategy_twenty_seven_release.py': {'__module__': '25b56cd9b46abc53eda393573a8a0ceca991e9037eb6869e0ab17b51192d2348'},
 'trading_runtime/strategy_twenty_six_release.py': {'__module__': '7344cb7fcdcb6cd3f6e10aea1400fe4b8e9e721e11294eab402d63576e901440'},
 'trading_runtime/strategy_twenty_three_release.py': {'__module__': 'e43951d61064e390078f9ac25d78eca0c000e0d33b28c54e663b71f644511c4f'},
 'trading_runtime/strategy_twenty_two_release.py': {'__module__': 'dfd5e4ccca3a285d278649a3374412e24638e29e62f57cf62f3db64c902d830f'},
 'trading_runtime/strategy_zero_regime_risk_failure.py': {'__module__': '2cca428561093253b73728c7c52a41b9e91dcf622f28fdd7ca93bb66660e8cd1'},
 'trading_runtime/entry_momentum_growth.py': {'__module__': '8acec288e9cf7d1c62bf8cb2fa386f82d5db69a63fc9306b90168f8cee3c0b5c'},
 'backend/backtest_declared_initial_momentum.py': {'__module__': '786a12c62a157c092652c8cd7d695fe4f5e8ebfb315c9b8dbab56580fb0a4b81'},
 'backend/source_ast_summary.py': {'__module__': '8316c975ab6d554ce9508b10fe3fe9ad87e059e0c48db91640357241ecdfe995'},
 'trading_runtime/squeeze_ladder_geometry.py': {'__module__': '34658cac7dba475b19ca3963bbcad1397a88f5f86e4133664947c9e656671e0d'},
 'backend/backtest_saved_writer_source.py': {'__module__': 'c92075375d589842d9087d04249c024b9b45c3972b0610b42d114445ddcb4cc7'},
 'backend/backtest_saved_reader_certification.py': {'certify_saved_reader_source': '6bcc3dcfeaf9b1039c6e005a727427a6111e2b98a2a585da3212e199f97c0f60'}}


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


_SESSION_EXIT_REVIEWED_AST = {'backend/backtest_journal_memory.py': {'append_numbered_session_exit_intent': '446960d28eda324257f9d51eed6d38dd00c1843d7976198be308ad918ac7bbe5'}, 'trading_runtime/numbered_fixed_strategy.py': {'_SESSION_EXIT_REASONS': '837954e429ce236e1a63eb6d85b743f79929e693d1e2f94b481c528c1932e3d9', 'numbered_session_exit_reason': 'd1a8701a48f8d0fa5e744d676bb53894b66b5fa2dff28e8f32cf0ca053ce4cec'}, 'trading_runtime/numbered_session_exit.py': {'numbered_session_exit_intent': 'ef9bf3c91d18c5059f3d58ea844397a0d764b7cad9188a865a4d7260e85d0550'}, 'trading_runtime/runtime.py': {'_execute_intents': 'a707272b9d051588cc7f217e1993064c8fc4e0e0590d324b9a907297cf330f90'}}


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
