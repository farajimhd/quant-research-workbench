"""Separate immutable V7 upper-HOD-zone research candidate."""
from .historical_hod_candidate import build as historical_build

PROFILE_ID='v7-transition-center-v5'
LABEL='V7 gray-level center breakout · 115-bps failed-retest buffer'


def build(base):
    payload,canvas,plan=historical_build(base,profile_id=PROFILE_ID,label=LABEL)
    profile=next(p for p in payload['strategy']['profiles'] if p['profile_id']==PROFILE_ID)
    p=profile['parameters']
    p['historical_hod'].update(v7_zone_enabled=1,v7_center_swing_enabled=1,v7_transition_entries_enabled=1,v7_price_only_enabled=1,entry_zone_fraction=.30,entry_breakout_offset=0.,
        target_distance_fraction=.10,early_green_stop_enabled=0,forming_macd_entry_enabled=1,rejection_break_offset_bps=115.)
    p['liquidity_admission'].update(maximum_current_spread_bps=115.,maximum_spread_bps=115.,minimum_current_trade_rate_60s=10.)
    p.setdefault('strategy_behavior',{}).update(eligible_sessions=['premarket','regular','after_hours'],
        entry_cutoff_time='19:55:00',flatten_time='19:59:00')
    profile['description']=('Causal V7 only; all origins equal. Completed non-red 1s resistance or either-direction gray transition '
        'center breakout in the upper 30% VWAP-to-HOD zone, with forming bullish 5s MACD and liquidity gates. '
        'Trade-price-only decision geometry; minimum 60s trade rate 10/sec. Initial confirmed local swing-low stop; trail below newly confirmed higher swing lows, never resistance bands. '
        'Add on higher resistance or gray-transition center closes with the same acquisition gates. '
        'Rejection exits require a close 115 bps below the frozen band floor and consecutive red lower-low confirmation. '
        '1.10 resistance targets outside regular hours, LULD during regular hours. Three cash tranches, shared protection.')
    return payload,canvas,plan


def create():
    from .trading_configuration_service import configuration_base,create_test_candidate
    payload,canvas,plan=build(configuration_base())
    return create_test_candidate(label=LABEL,canvas_revision=canvas['revision'],canvas_profile=canvas['profile'],
        configuration=payload,run_plan_id=plan,strategy_profile_id=PROFILE_ID)
