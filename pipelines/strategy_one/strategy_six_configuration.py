"""Producer entry point for pure, sealed Strategy 6 configuration derivation."""
from src.trading_runtime.strategy_six_release import derive_strategy_six_configuration


def compile_strategy_six_configuration(source, *, approved_code_commit: str,
                                      approved_code_fingerprint: str,
                                      approval_reference: str) -> dict:
    return derive_strategy_six_configuration(source,
        approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint,
        approval_reference=approval_reference)
