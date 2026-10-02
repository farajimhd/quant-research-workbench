"""Prepared original-entry/first-held binding; no commit or producer attestation."""
from src.backend.backtest_strategy_one_management import StrategyOneManagementState
from .strategy_liquidity_fade_exit import validate_liquidity_fade_witness, validate_liquidity_fade_financial
from .strategy_one_stateful import StrategyOneEntryProposal, StrategyOneFinancialView
from .strategy_one_position import ProtectionState


def validate_liquidity_fade_state(witness, state, financial) -> StrategyOneEntryProposal:
    """Require this candidate's exact native held identity and original risk.

    The caller must independently verify the snapshot/prefix and producer
    counts/quote before using this check. Reconstructed labels, fill timestamps
    or mutable JSON are not substituted for missing first-held authority.
    """
    from .strategy_half_risk_liquidity_fade import HalfRiskLiquidityFadeFailure
    # The distinct witness has one prepared policy. This initial scalar check
    # grants no numbered binding: the original entry below must separately
    # match that policy, the financial identity and the native first-held clock.
    validate_liquidity_fade_witness(witness,
        strategy_number=39 if type(witness) is HalfRiskLiquidityFadeFailure else 35)
    validate_liquidity_fade_financial(financial)
    if (type(state) is not StrategyOneManagementState
            or type(financial) is not StrategyOneFinancialView
            or state.boundary_ms != witness.boundary_ms
            or financial.position_quantity <= 0 or financial.pending_exit):
        raise ValueError("Liquidity fade source lacks exact current held state")
    key = financial.account_id, financial.assignment_id, financial.ticker
    families = {}
    for name in ("submitted", "positions", "first_held_boundaries"):
        pairs = getattr(state, name)
        if len({k for k, _ in pairs}) != len(pairs):
            raise ValueError("Liquidity fade source repeats a position identity")
        families[name] = dict(pairs)
        if key not in families[name]:
            raise ValueError("Liquidity fade source lacks its held entry identity")
    source = families["submitted"][key]
    if (type(source) is not StrategyOneEntryProposal or type(source.strategy_number) is not int
            or source.strategy_number not in (35, 36, 37, 38, 39)
            or (source.account_id, source.assignment_id, source.ticker) != key
            or source.reference_ask != witness.reference_ask
            or source.initial_stop != witness.initial_stop
            or not source.boundary_ms < witness.first_held_boundary_ms
            or type(families["first_held_boundaries"][key]) is not int
            or families["first_held_boundaries"][key] != witness.first_held_boundary_ms
            or type(families["positions"][key]) is not ProtectionState
            or families["positions"][key].boundary_ms > state.boundary_ms):
        raise ValueError("Liquidity fade source differs from original risk or first-held authority")
    validate_liquidity_fade_witness(witness, strategy_number=source.strategy_number)
    return source
