"""Prepared causal exit evidence; native persistence and OMS remain separate."""
import polars as pl

from .backtest_native_channel_exit_policy import NativeChannelExitPolicy
from .backtest_native_channel_sequences import PreparedNativeChannelSequence
from research.causal_strategy_features.v5.decisions import DECISION_IDENTITY
from src.trading_runtime.native_channel_quote_exit import qualify_native_channel_quote_exits


def evaluate_native_channel_exit(prepared, decisions, mandatory_held, acquisition_day_us,
        quotes, *, policy):
    """Combine certified sequence evidence and exact keyed current quotes.

    Callers must obtain ownership/fill clocks from the native financial owner,
    not retrospective trade labels. The returned frame is pending evidence;
    it never submits an order or grants checkpoint/release admission.
    """
    if type(prepared) is not PreparedNativeChannelSequence or type(policy) is not NativeChannelExitPolicy:
        raise ValueError('Exact prepared sequence and immutable exit policy required')
    policy.__post_init__()
    if prepared.policy != policy.sequence:
        raise ValueError('Prepared sequence differs from declared exit policy')
    keys=[*DECISION_IDENTITY,'decision_day_ms']
    schema=dict.fromkeys(DECISION_IDENTITY,pl.String)
    schema.update(decision_day_ms=pl.Int64,pending_exit=pl.Boolean,
                  quote_day_us=pl.Int64,bid_int=pl.UInt64,ask_int=pl.UInt64)
    if policy.quote.threshold_mode=='below_original_risk_threshold':
        schema.update(original_ask=pl.Float64,initial_stop=pl.Float64)
    if (type(quotes) is not pl.DataFrame or dict(quotes.schema)!=schema
            or quotes.height>policy.quote.max_rows or quotes.select(keys).n_unique()!=quotes.height
            or any(quotes[k].null_count() for k in keys)):
        raise ValueError('Exact uniquely keyed bounded quote evidence required')
    evidence=prepared.decision_evidence(decisions,mandatory_held,acquisition_day_us)
    if (quotes.height!=evidence.height
            or quotes.join(evidence.select(keys),on=keys,how='anti').height):
        raise ValueError('Quote evidence differs from exact decision identities')
    joined=(evidence.with_row_index('_decision_row').join(quotes,on=keys,how='left',validate='1:1')
            .sort('_decision_row').drop('_decision_row'))
    mask=qualify_native_channel_quote_exits(joined,policy=policy.quote)
    return joined.with_columns(pl.Series('exit_eligible',mask))
