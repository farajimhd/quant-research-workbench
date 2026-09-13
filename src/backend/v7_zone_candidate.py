"""Separate immutable V7 upper-HOD-zone research candidate."""
from .historical_hod_candidate import build as historical_build

PROFILE_ID='v7-setup-v7'
LABEL='V7 early setup - swing protection until range breakout'


def build(base):
    payload,canvas,plan=historical_build(base,profile_id=PROFILE_ID,label=LABEL)
    profile=next(p for p in payload['strategy']['profiles'] if p['profile_id']==PROFILE_ID)
    p=profile['parameters']
    p['historical_hod'].update(v7_zone_enabled=1,v7_center_swing_enabled=1,v7_transition_entries_enabled=1,v7_price_only_enabled=1,entry_zone_fraction=.30,entry_breakout_offset=0.,
        target_distance_fraction=.10,early_green_stop_enabled=0,forming_macd_entry_enabled=1,rejection_break_offset_bps=115.,
        v7_setup_enabled=1,setup_range_seconds=30,setup_minimum_bars=5,
        v7_encounters_enabled=1,breakout_buffer_bps=10.,breakout_buffer_ticks=1.,topping_tail_fraction=.5)
    p['liquidity_admission'].update(maximum_current_spread_bps=115.,maximum_spread_bps=115.,minimum_current_trade_rate_60s=10.)
    p.setdefault('strategy_behavior',{}).update(eligible_sessions=['premarket','regular','after_hours'],
        entry_cutoff_time='19:55:00',flatten_time='19:59:00')
    profile['description']=('Early non-red rising 1s close below the preceding 30-second range high, minimum five bars; '
        'bullish forming 5s MACD, upper 30% VWAP/HOD zone, 115bps spread and existing liquidity gates. '
        'Initial and trailing confirmed swing-low stops. Hold through consolidation, MACD reversals and resistance '
        'rejections until a non-red close clears the frozen entry range high by 10bps or one tick. '
        'Only subsequent encounters can trigger post-breakout rejection exits. Session, manual, LULD and stop exits remain active. '
        'V7 resistance/gray adds, three cash tranches and existing target rules are retained.')
    return payload,canvas,plan


def create():
    from .trading_configuration_service import configuration_base,create_test_candidate
    payload,canvas,plan=build(configuration_base())
    return create_test_candidate(label=LABEL,canvas_revision=canvas['revision'],canvas_profile=canvas['profile'],
        configuration=payload,run_plan_id=plan,strategy_profile_id=PROFILE_ID)
