"""Compile the declared fixed-lot comparison only under complete source proof."""
from src.trading_runtime.strategy_seventy_seven_release import (
    derive_strategy_seventy_seven_configuration,
    verify_prepared_strategy_seventy_seven_configuration,
)


def compile_strategy_seventy_seven_configuration(
    source, *, approved_code_commit, approved_code_fingerprint, approval_reference,
):
    result = derive_strategy_seventy_seven_configuration(
        source, approved_code_commit=approved_code_commit,
        approved_code_fingerprint=approved_code_fingerprint,
        approval_reference=approval_reference,
    )
    verify_prepared_strategy_seventy_seven_configuration(source, result['payload'])
    from src.backend.backtest_fixed_v4_certification import certify_numbered_fixed_v4_projection
    certify_numbered_fixed_v4_projection(result['payload']['strategy']['strategy_number'])
    # The publisher recomputes normalized nodes from the complete payload;
    # preparation-only nodes are not part of its closed transport envelope.
    return {key: result[key] for key in (
        'source_candidate_id', 'source_candidate_hash', 'payload_hash',
        'node_hash', 'node_count', 'payload',
    )}
