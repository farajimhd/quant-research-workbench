"""Explicit prepared entry-source declaration; no installed execution authority.

A future own immutable manifest must include this complete payload and input
contract before native use. A caller default or this object alone is not a
producer certificate, approved configuration, source seal or financial grant.
"""
from dataclasses import dataclass

from .declared_native_fixed_candidate import QUOTE_SOURCE_CONTRACT, QUOTE_SOURCE_PAYLOAD
from .declared_native_fixed_capabilities import _json, _keys

INPUT_CONTRACT = "declared-native-fixed-entry-source-input@1"


@dataclass(frozen=True, slots=True)
class DeclaredNativeEntrySourcePolicy:
    quote_source_contract: str

    def __post_init__(self):
        if type(self.quote_source_contract) is not str or self.quote_source_contract != QUOTE_SOURCE_CONTRACT:
            raise ValueError("Declared source requires supported exact quote contract")

    def payload(self):
        return dict(input_contract=INPUT_CONTRACT, quote_source=dict(QUOTE_SOURCE_PAYLOAD),
                    quote_product="arte.liquidity_100ms_v1",
                    bar_product="arte.bars_v1",
                    source_attempts="all_three_exact_certified_candidate_source_attempts",
                    source_population="full_independently_fenced_market_membership",
                    source_end="independently_bound_complete_candidate_source_horizon",
                    scheduler="exact_vectorized_admitted_keys_in_declared_decision_horizon",
                    freshness="unchanged_shared_scalar_financial_reducer",
                    authority="requires_complete_own_installed_manifest_and_source_closure")


def parse_declared_native_entry_source(value):
    expected = DeclaredNativeEntrySourcePolicy(QUOTE_SOURCE_CONTRACT)
    _keys(value, set(expected.payload()), "declared native entry source")
    if _json(value) != _json(expected.payload()):
        raise ValueError("Declared entry source differs from complete supported payload")
    return expected
