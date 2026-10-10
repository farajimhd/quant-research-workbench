"""Declared channel writer identity; no implicit grants or legacy fallback."""
from .native_channel_management_policy import NativeChannelManagementPolicy
from .original_risk_diagnostic_profile import validate_original_risk_profile


def validate_channel_writer_profile(policy, *, automatic_ladder=False,
        entry_spread_risk=False, risk_policy=None, ladder_geometry_policy=None,
        fixed_structural_lot_profile=None):
    if policy is None:
        return
    if type(policy) is not NativeChannelManagementPolicy or risk_policy is None:
        raise ValueError('Channel writer requires exact declared channel and original-risk policies')
    policy.__post_init__()
    validate_original_risk_profile(automatic_ladder, entry_spread_risk, risk_policy)
    if ladder_geometry_policy is not None or fixed_structural_lot_profile is not None:
        raise ValueError('Channel writer cannot mix unrelated manager authorities')


def channel_runner_identity(policy, risk_policy):
    from .original_risk_diagnostic_profile import require_original_risk_diagnostic_policy
    require_original_risk_diagnostic_policy(risk_policy)
    validate_channel_writer_profile(policy, risk_policy=risk_policy,
        entry_spread_risk=getattr(risk_policy, 'entry_cost_capability', False))
    if policy is None:
        raise ValueError('Channel writer identity requires a selected declaration')
    return ('BACKTEST_V4_NATIVE_CHANNEL_RUNNER', 'backtest_v4_native_channel_runner')
