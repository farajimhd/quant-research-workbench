"""Native position/quote adapter for pending channel decisions, never orders."""
from datetime import datetime,timezone
from math import isfinite
from zoneinfo import ZoneInfo

import polars as pl

from .backtest_market_data import market_day_boundary
from .backtest_native_channel_exit_request import build_native_channel_exit_request
from .backtest_native_channel_exit_policy import NativeChannelExitPolicy
from .backtest_native_channel_sequences import PreparedNativeChannelSequence
from .backtest_native_channel_position_index import NativeChannelPositionIndex
from src.trading_runtime.native_acquisition_clock import NativeAcquisitionClock
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView,StrategyOneEntryProposal


def build_native_channel_management_request(prepared,quote,*,policy,financial,
        acquisition,original_entry,boundary_ms,position_index=None):
    """Use the exact native clock, current quote and original entry risk.

    The caller must read financial/fill evidence after broker reconciliation.
    The resulting request remains pending until complete checkpoint confirmation.
    An optional source-bound feature index only skips unsuccessful predicates.
    """
    if (type(prepared) is not PreparedNativeChannelSequence or type(policy) is not NativeChannelExitPolicy
            or prepared.policy!=policy.sequence or type(financial) is not StrategyOneFinancialView
            or type(acquisition) is not NativeAcquisitionClock
            or type(original_entry) is not StrategyOneEntryProposal or type(quote) is not dict
            or type(boundary_ms) is not int or boundary_ms<0 or boundary_ms%100
            or type(financial.position_quantity) not in (int,float)
            or not isfinite(financial.position_quantity) or financial.position_quantity<0
            or type(financial.pending_exit) is not bool):
        raise ValueError('Native channel management requires exact position, source and boundary inputs')
    acquisition.__post_init__()
    owner=(financial.account_id,financial.assignment_id,financial.ticker)
    if owner!=(original_entry.account_id,original_entry.assignment_id,original_entry.ticker) or owner!=(
            acquisition.account_id,acquisition.assignment_id,acquisition.ticker):
        raise ValueError('Native channel management cannot borrow another position or original risk')
    at=market_day_boundary(acquisition.session_date,boundary_ms).astimezone(timezone.utc)
    local=at.astimezone(ZoneInfo('America/New_York'))
    day_us=((local.hour*3600+local.minute*60+local.second)*1_000_000+local.microsecond)
    if local.date()!=acquisition.session_date or day_us%1000 or day_us<acquisition.acquisition_day_us:
        raise ValueError('Native management decision precedes acquisition or crosses its source session')
    day_ms=day_us//1000
    if financial.position_quantity==0 or financial.pending_exit:
        return None
    if position_index is not None:
        if type(position_index) is not NativeChannelPositionIndex:
            raise ValueError('Native management feature screening requires its exact source-bound index')
        if not position_index.candidate_at(day_ms,acquisition=acquisition,prepared=prepared):
            return None
    if quote.get('quote_valid')!=1:
        return None
    if (quote.get('ticker')!=financial.ticker
            or quote.get('session_date')!=acquisition.session_date.isoformat()
            or quote.get('boundary_ms')!=boundary_ms):
        raise ValueError('Native quote crossed its exact position, source session or boundary')
    stamp,bid,ask=(quote.get(key) for key in ('quote_timestamp_us','bid_int','ask_int'))
    if any(type(value) is not int for value in (stamp,bid,ask)) or not 0<bid<=ask:
        return None
    elapsed=at-datetime(1970,1,1,tzinfo=timezone.utc)
    at_us=(elapsed.days*86400+elapsed.seconds)*1_000_000+elapsed.microseconds
    if stamp>at_us or at_us-stamp>policy.quote.quote_max_age_us:
        return None
    scope=prepared.scope.filter((pl.col('ticker')==financial.ticker)
        & (pl.col('session_date')==acquisition.session_date.isoformat()))
    if scope.height!=1:
        raise ValueError('Native management lacks one exact certified source identity')
    decisions=scope.with_columns(pl.lit(day_ms,dtype=pl.Int64).alias('decision_day_ms'))
    quotes=decisions.with_columns(pl.lit(False).alias('pending_exit'),
        pl.lit(day_us-(at_us-stamp),dtype=pl.Int64).alias('quote_day_us'),
        pl.lit(bid,dtype=pl.UInt64).alias('bid_int'),pl.lit(ask,dtype=pl.UInt64).alias('ask_int'))
    if policy.quote.threshold_mode=='below_original_risk_threshold':
        quotes=quotes.with_columns(pl.lit(original_entry.reference_ask,dtype=pl.Float64).alias('original_ask'),
            pl.lit(original_entry.initial_stop,dtype=pl.Float64).alias('initial_stop'))
    return build_native_channel_exit_request(prepared,decisions,quotes,policy=policy,
        financial=financial,acquisition=acquisition,source_entry_intent_id=acquisition.source_entry_intent_id)
