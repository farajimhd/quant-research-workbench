"""Producer entry point for pure, sealed Strategy 17 configuration derivation."""
from src.trading_runtime.strategy_seventeen_release import derive_strategy_seventeen_configuration


def compile_strategy_seventeen_configuration(source, *, approved_code_commit: str,
                                      approved_code_fingerprint: str,
                                      approval_reference: str) -> dict:
    return derive_strategy_seventeen_configuration(source,
        approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint,
        approval_reference=approval_reference)
