"""Compile exact-parent Strategy55 with its complete installed execution proof."""
from src.trading_runtime.strategy_fifty_five_release import derive_strategy_fifty_five_configuration


def compile_strategy_fifty_five_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    result = derive_strategy_fifty_five_configuration(
        source, approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference,
    )
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    certify_numbered_fixed_v4_projection(55)
    return result
