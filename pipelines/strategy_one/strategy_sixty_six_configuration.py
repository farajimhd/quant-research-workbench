"""Compile exact-parent Strategy66 only after its full native source proof."""
from src.trading_runtime.strategy_sixty_six_release import derive_strategy_sixty_six_configuration


def compile_strategy_sixty_six_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    result = derive_strategy_sixty_six_configuration(
        source, approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint,
        approval_reference=approval_reference)
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    certify_numbered_fixed_v4_projection(result['payload']['strategy']['strategy_number'])
    return result
