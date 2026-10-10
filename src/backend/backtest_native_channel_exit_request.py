"""Pending channel evidence from a current position; not checkpoint admission."""
from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from uuid import UUID

import numpy as np
import polars as pl

from .backtest_native_channel_exit import evaluate_native_channel_exit
from src.market_engine.native_causal_channel_contract import NativeChannelSourcePlan
from src.trading_runtime.native_acquisition_clock import NativeAcquisitionClock
from src.trading_runtime.strategy_one_stateful import StrategyOneFinancialView


@dataclass(frozen=True,slots=True)
class NativeChannelExitRequest:
    policy: object
    financial: StrategyOneFinancialView
    acquisition: NativeAcquisitionClock
    source_plan: object
    decision: object
    supporting_rows: object
    reference_rows: object


def build_native_channel_exit_request(prepared, decisions, quotes, *, policy,
        financial, acquisition, source_entry_intent_id):
    """Freeze one survivor's exact completed source rows and native fill clock.

    The native owner must supply its original entry intent and broker/OMS-bound
    acquisition clock. Checkpoint confirmation must independently reprove both;
    this builder cannot manufacture that authority or submit an exit.
    """
    if (type(financial) is not StrategyOneFinancialView
            or type(acquisition) is not NativeAcquisitionClock
            or type(decisions) is not pl.DataFrame or decisions.height!=1
            or type(quotes) is not pl.DataFrame or quotes.height!=1
            or type(financial.position_quantity) not in (int,float)
            or not isfinite(financial.position_quantity) or financial.position_quantity<=0
            or type(financial.pending_exit) is not bool):
        raise ValueError('One exact current held position and acquisition required')
    acquisition.__post_init__()
    if (type(source_entry_intent_id) is not str
            or str(UUID(source_entry_intent_id))!=source_entry_intent_id
            or UUID(source_entry_intent_id).int==0
            or acquisition.source_entry_intent_id!=source_entry_intent_id
            or (acquisition.account_id,acquisition.assignment_id,acquisition.ticker)!=
                (financial.account_id,financial.assignment_id,financial.ticker)):
        raise ValueError('Channel exit acquisition differs from native financial ownership')
    from research.causal_strategy_features.v5.decisions import DECISION_IDENTITY
    if (decisions.schema.get('ticker')!=pl.String or decisions.schema.get('session_date')!=pl.String
            or decisions['ticker'].item()!=financial.ticker
            or decisions['session_date'].item()!=acquisition.session_date.isoformat()):
        raise ValueError('Channel decision differs from current acquisition session or ticker')
    if quotes.schema.get('pending_exit')!=pl.Boolean or quotes['pending_exit'].item()!=financial.pending_exit:
        raise ValueError('Channel quote pending exit differs from native financial state')
    result=evaluate_native_channel_exit(prepared,decisions,np.array([True],dtype=bool),
        np.array([acquisition.acquisition_day_us],dtype=np.int64),quotes,policy=policy)
    row=result.row(0,named=True)
    if not row['exit_eligible']:
        return None
    selected=pl.all_horizontal(pl.col(key)==row[key] for key in DECISION_IDENTITY)
    selected=selected & (pl.col('feature_attempt_id')==row['feature_attempt_id'])
    selected=selected & (pl.col('policy_digest')==row['policy_digest'])
    features=prepared.features.filter(selected)
    support=features.filter(pl.col('available_day_boundary_ms').is_between(
        row['sequence_start_day_ms'],row['sequence_end_day_ms'])).sort('available_day_boundary_ms')
    if support.height!=policy.sequence.sequence.minimum_bars:
        raise ValueError('Accepted channel decision lacks its exact supporting source rows')
    reference=features.head(0)
    if policy.sequence.reference_mode=='below-first-post-acquisition-close':
        reference=features.filter(pl.col('available_day_boundary_ms')==row['reference_completion_day_ms'])
        if reference.height!=1 or reference['close_int'].item()!=row['reference_close_int']:
            raise ValueError('Accepted channel reference differs from completed source row')
    source_plan=prepared.source_plan
    if (type(source_plan) is not NativeChannelSourcePlan
            or source_plan.request is not prepared.packet.request
            or source_plan.feature_attempt_id!=prepared.packet.feature_attempt_id
            or source_plan.token!=prepared.packet.token):
        raise ValueError('Pending channel source plan differs from prepared native packet')
    source_plan.__post_init__()
    return NativeChannelExitRequest(policy,financial,acquisition,source_plan,
        MappingProxyType(dict(row)),support.to_arrow(),reference.to_arrow())
