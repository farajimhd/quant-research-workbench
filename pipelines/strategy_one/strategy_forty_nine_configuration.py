"""Compile exact-parent Strategy49 with its complete installed execution proof."""
from src.trading_runtime.strategy_forty_nine_release import derive_strategy_forty_nine_configuration


def compile_strategy_forty_nine_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    result = derive_strategy_forty_nine_configuration(
        source, approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference,
    )
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    certify_numbered_fixed_v4_projection(49)
    return result
