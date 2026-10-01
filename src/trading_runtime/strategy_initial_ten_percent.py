"""Staged Strategy 26 first-setup policy; numbered admission is separate."""
from .strategy_rising_momentum_witness import RisingMomentumWitness
from .strategy_strong_ten_second_momentum import strong_ten_second_momentum_entry

POLICY_ID = 'strategy-twenty-six-premarket-first-setup-ten-second-growth-10pct-v1'


def first_setup_ten_percent_policy_payload() -> dict:
    return {
        'policy_id': POLICY_ID,
        'fraction': 0.10,
        'resolution_ms': 10_000,
        'comparison': 'current_histogram > 0 and current_histogram > prior_histogram + 0.10 * abs(prior_histogram)',
        'scope': 'first_structurally_eligible_setup_only',
        'changed_session_scope': 'premarket_only',
        'afterhours_first_setup_policy': 'unchanged_strict_10pct',
        'current_entry_policy': 'unchanged_strict_10pct',
        'clock': 'completed_adjacent_producer_observations',
        'boundary_semantics': 'first_structurally_eligible_setup_boundary_ms',
        'initiality_authority': 'certified_native_candidate_compiler',
    }


def first_setup_ten_percent_entry(witness: RisingMomentumWitness) -> bool:
    """Reuse the exact completed-observation reducer; never derive MACD."""
    return strong_ten_second_momentum_entry(witness)
