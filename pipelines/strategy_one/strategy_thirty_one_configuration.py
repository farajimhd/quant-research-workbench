"""Producer entry point for sealed Strategy 31 configuration derivation."""
from src.trading_runtime.strategy_thirty_one_release import derive_strategy_thirty_one_configuration


def compile_strategy_thirty_one_configuration(source, *, approved_code_commit,
                                            approved_code_fingerprint, approval_reference):
    return derive_strategy_thirty_one_configuration(source,
        approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint,
        approval_reference=approval_reference)
