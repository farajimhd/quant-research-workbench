from dataclasses import replace
import numpy as np
import polars as pl
import pytest

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from src.backend.backtest_native_channel_exit import evaluate_native_channel_exit
from src.backend.backtest_native_channel_exit_policy import NativeChannelExitPolicy
from src.backend.backtest_native_channel_sequences import NativeChannelSequencePolicy,prepare_installed_native_channel_sequence
from src.trading_runtime.native_channel_quote_exit import NativeChannelQuoteExitPolicy
from src.trading_runtime.native_completed_bar_sequences import CompletedBarSequencePolicy
from src.market_engine.native_causal_channel_contract import issue_source_plan


def setup(installed):
    client,authority,packet,decisions,_=installed
    sequence=NativeChannelSequencePolicy(packet.request.policy,CompletedBarSequencePolicy(1,1000),
        60000,'close_return','ge',0.0,1000,'none')
    policy=NativeChannelExitPolicy(sequence,NativeChannelQuoteExitPolicy(1000000,
        'below_original_risk_threshold',0.25,1000))
    prepared=prepare_installed_native_channel_sequence(client,packet.request,packet.feature_attempt_id,
        policy=sequence,producer_source_hash=issue_source_plan(packet).producer_source_hash,authority=authority)
    quotes=decisions.with_columns(pl.lit(False).alias('pending_exit'),
        (pl.col('decision_day_ms')*1000).alias('quote_day_us'),
        pl.lit(9400,dtype=pl.UInt64).alias('bid_int'),pl.lit(9500,dtype=pl.UInt64).alias('ask_int'),
        pl.lit(1.0).alias('original_ask'),pl.lit(0.8).alias('initial_stop'))
    return prepared,policy,decisions,quotes


def test_exit_evidence_joins_quotes_by_identity_and_preserves_order_without_reads(installed):
    prepared,policy,decisions,quotes=setup(installed)
    before=len(installed[0].sql)
    result=evaluate_native_channel_exit(prepared,decisions,np.ones(4,dtype=bool),
        np.zeros(4,dtype=np.int64),quotes.reverse(),policy=policy)
    assert result['decision_day_ms'].to_list()==decisions['decision_day_ms'].to_list()
    assert result['exit_eligible'].to_list()==[True,False,False,True]
    assert len(installed[0].sql)==before


@pytest.mark.parametrize('defect',['foreign','missing','duplicate','policy'])
def test_foreign_or_incomplete_quote_binding_fails_closed(installed,defect):
    prepared,policy,decisions,quotes=setup(installed)
    if defect=='foreign':quotes=quotes.with_columns(pl.lit('foreign').alias('ticker'))
    if defect=='missing':quotes=quotes.head(3)
    if defect=='duplicate':quotes=pl.concat([quotes.head(3),quotes.head(1)])
    if defect=='policy':policy=replace(policy,sequence=replace(policy.sequence,threshold=0.01))
    with pytest.raises(ValueError):evaluate_native_channel_exit(prepared,decisions,
        np.ones(4,dtype=bool),np.zeros(4,dtype=np.int64),quotes,policy=policy)
