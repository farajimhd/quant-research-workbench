"""Prepared exact-parent Strategy 34 compiler; publication remains separate."""
from src.trading_runtime.strategy_thirty_four_release import derive_strategy_thirty_four_configuration


def compile_strategy_thirty_four_configuration(source, *, approved_code_commit,
                                              approved_code_fingerprint, approval_reference):
    return derive_strategy_thirty_four_configuration(
        source, approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint, approval_reference=approval_reference,
    )
