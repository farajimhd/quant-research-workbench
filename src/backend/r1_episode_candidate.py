"""Save a separate successor with VWAP-to-R1 and post-R1 episode stages."""
from src.trading_runtime.r1_ladder import VWAP_CONTRACT, STAGED_CONTRACT
from .r1_vwap_candidate import build_successor

PROFILE_ID = STAGED_CONTRACT
LABEL = 'R1 resistance ladder v6 — staged episodes with initial swing protection'
DESCRIPTION = (
    'Each bullish completed 5s MACD episode starts a fresh entry sequence. With no '
    'other resistance between execution VWAP and R1, any completed green 1s candle '
    'above VWAP is eligible after current liquidity, RVOL and quote checks pass; '
    'a fresh VWAP crossover is not required. An entry below R1 targets one tick '
    'below R1 midpoint. The first trade always uses bounded initial swing protection, '
    'including an episode that begins above R1. If resistance intervenes, wait above '
    'R1 and use the existing ATR-qualified overhead target ladder. After a full target fill, the '
    'next trade requires price above that target band upper edge in the same MACD '
    'episode, rather than above the episode high, and protects 20 bps below the '
    'crossed band lower edge. Retain that filled target even '
    'after its role changes. Episode completion resets continuation eligibility; '
    'an existing position retains its bracket protection. Original cash sizing, '
    'time, liquidity, RVOL and continuation spread policies remain unchanged.'
)


def build(source_configuration, *, published_configuration=None):
    return build_successor(source_configuration, source_contract=VWAP_CONTRACT,
        profile_id=PROFILE_ID, label=LABEL, description=DESCRIPTION,
        published_configuration=published_configuration)


def create(source_configuration):
    from .trading_configuration_service import configuration_base, create_test_candidate

    payload, canvas, plan = build(source_configuration, published_configuration=configuration_base())
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'],
        canvas_profile=canvas['profile'], configuration=payload,
        run_plan_id=plan, strategy_profile_id=PROFILE_ID)
