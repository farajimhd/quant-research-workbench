"""Build the separate causal BOS / VWAP / frozen-gap momentum candidate."""
from . import early_squeeze_breakout_candidate as base
from src.trading_runtime.early_squeeze_momentum import CONTRACT

LABEL = 'Early Squeeze / session targets v25 / rapid reentry 2x'
DESCRIPTION = (
    'Early Squeeze activates the session. Initial entries require causal confirmed-high BOS, '
    'price above VWAP and bullish completed/forming 1s plus completed 100ms MACD. '
    'At +30% from the first eligible 04:00+ trade, late mode latches and requires breaking '
    'the nearest resistance midpoint below prior HOD. Up to three filled purchases per '
    'position, each requesting one third of currently unreserved cash. Adds require a green '
    '1s midpoint crossing and the first trade in the immediately following second above '
    'unchanged geometry plus open completed 1s and 100ms MACD episodes, without entry '
    'VWAP/HOD/forming-MACD gates or MACD age expiry; one filled add per resistance per MACD episode. Stop below the '
    'latest confirmed V7-supported low formed within thirty seconds, else support below VWAP '
    'within 1% of entry, else 5% below actual entry. Every three distinct accepted '
    'resistances advance the stop one resistance, below its lower band. Freeze average '
    'consecutive resistance gaps at activation through +300%. All purchases share the '
    'first entry fill-price plus session gap multiplier: 5x, 8x, 10x, 12x, then +1x per three distinct '
    'confirmed breaks, without a speed limit, preserved across positions. Reentry above '
    '+30% within 30s of the last entry fill starts at 2x with stop below the nearest '
    'resistance below price, then inherits the session multiplier on the next advancement. First target fill liquidates the whole remainder; '
    'no reentry within that same 1s candle. No CHOCH/chop/MACD/VWAP exit. Stop buying '
    'and flatten at 20:00 New York. Research candidate; no profitability acceptance.'
)


def build(configuration, baseline):
    payload, canvas, plan_id = base.build(configuration, baseline,
        profile_id=CONTRACT, label=LABEL, description=DESCRIPTION)
    profile = next(p for p in payload['strategy']['profiles'] if p['profile_id'] == CONTRACT)
    behavior = profile['parameters']['strategy_behavior']
    behavior.update(entry_cutoff_time='20:00:00', flatten_time='20:00:00')
    profile['lifecycle']['trading_behavior'] = dict(behavior)
    profile['parameters']['reentry']['after_protective_exit'] = True
    profile['parameters']['momentum_fallback_stop_percent'] = 5
    profile['parameters']['momentum_session_progression'] = True
    return payload, canvas, plan_id


def create():
    from .trading_configuration_service import configuration_base, configuration_candidate, create_test_candidate
    from .trading_runtime_service import trading_journal
    base.require_loaded_executor(CONTRACT)
    existing = next((c for c in trading_journal().trading_configuration_candidate_summaries()
        if c['label'] == LABEL), None)
    if existing:
        return configuration_candidate(existing['candidate_id'], required=True)
    payload, canvas, plan = build(configuration_base(), configuration_candidate(base.BASELINE_ID, required=True))
    return create_test_candidate(label=LABEL, canvas_revision=canvas['revision'], canvas_profile=canvas['profile'],
        configuration=payload, run_plan_id=plan, strategy_profile_id=CONTRACT)
