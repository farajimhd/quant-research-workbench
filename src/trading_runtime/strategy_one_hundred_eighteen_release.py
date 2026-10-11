"""Immutable declaration for the first causal completed-channel financial pilot."""
from copy import deepcopy
from dataclasses import replace

from . import strategy_one_hundred_seventeen_release as parent
from .native_channel_management_policy import RULE, SOURCE_CONTRACT, POLICY_KEY, NativeChannelManagementPolicy
from .native_completed_bar_sequences import CompletedBarSequencePolicy
from .native_channel_quote_exit import NativeChannelQuoteExitPolicy
from src.market_engine.native_causal_channel_contract import NativeChannelPolicy
from src.backend.backtest_native_channel_sequences import NativeChannelSequencePolicy
from src.backend.backtest_native_channel_exit_policy import NativeChannelExitPolicy

CHANNEL_POLICY = NativeChannelManagementPolicy(NativeChannelExitPolicy(
    NativeChannelSequencePolicy(
        NativeChannelPolicy((30000,60000,300000),100,
            ((30000,29999),(60000,59999),(300000,299999)),5,5),
        CompletedBarSequencePolicy(2,2000000),60000,'close_return','lt',0.0,
        2000000,'below-first-post-acquisition-close'),
    NativeChannelQuoteExitPolicy(1000000,'below_reference_close',None,2000000)),1024,4096)

BEHAVIOR = (
    'Retain Strategy117 activation, entries, reentry, additions, sizing, cash, '
    'aggregate exposure, costs, protection and inherited exit priority. After '
    'actual first acquisition, exit on two wholly post-acquisition completed '
    'one-minute negative close returns, latest completed close below the first '
    'post-acquisition close, and a current fresh bid below that reference. '
    'Use certified 30-second, one-minute and five-minute native channels with '
    'completed 100ms decision boundaries. Preserve sequential Portfolio/OMS '
    'admission, fills, cash release and protective OCA ordering. PM/AH only; '
    'AH retains prior-day V7 and full regular-session warmup. Backtest-only; '
    'financial benefit, full-session performance and validation unproven.')


def release_contract():
    prior=parent.release_contract()
    draft=replace(prior,number=118,executor_revision=118,
        input_contracts=prior.input_contracts+(SOURCE_CONTRACT,),
        rule_set_contracts=prior.rule_set_contracts+(RULE,),
        behavior_specification=BEHAVIOR,approved_digest='')
    release=replace(draft,approved_digest=draft.digest())
    release.verify()
    return release


def declared_policies():
    policies=deepcopy(parent.declared_policies())
    policies[POLICY_KEY]=CHANNEL_POLICY.payload()
    return policies
