"""Prepare exact-parent Strategy 35; native installation/publication is separate."""
from src.trading_runtime.strategy_thirty_five_release import derive_strategy_thirty_five_configuration


def compile_strategy_thirty_five_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    return derive_strategy_thirty_five_configuration(
        source, approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference,
    )
