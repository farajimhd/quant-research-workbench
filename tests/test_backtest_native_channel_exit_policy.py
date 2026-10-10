from dataclasses import replace
import pytest
import json
from src.trading_runtime.journal_contract import canonical_json

from tests.test_backtest_native_channel_qualification import installed, unit  # noqa: F401
from src.backend.backtest_native_channel_exit_policy import NativeChannelExitPolicy, parse_native_channel_exit_policy
from src.backend.backtest_native_channel_sequences import NativeChannelSequencePolicy
from src.trading_runtime.native_completed_bar_sequences import CompletedBarSequencePolicy
from src.trading_runtime.native_channel_quote_exit import NativeChannelQuoteExitPolicy


def policy(installed):
    return NativeChannelExitPolicy(NativeChannelSequencePolicy(installed[2].request.policy,
        CompletedBarSequencePolicy(2,1000),60000,'close_return','lt',0.0,1000,
        'below-first-post-acquisition-close'),
        NativeChannelQuoteExitPolicy(1000000,'below_reference_close',None,1000))


def test_complete_exit_rule_roundtrips_without_defaults(installed):
    original=policy(installed)
    assert parse_native_channel_exit_policy(json.loads(canonical_json(original.payload())))==original
    with pytest.raises(ValueError):replace(original,sequence=replace(original.sequence,reference_mode='none'))
    with pytest.raises(ValueError):replace(original,quote=replace(original.quote,max_rows=999))


@pytest.mark.parametrize('field',['priority','ownership','persistence','missing','rule'])
def test_execution_semantics_cannot_be_changed_by_unrecognized_payload(installed,field):
    payload=json.loads(canonical_json(policy(installed).payload()))
    payload[field]='changed'
    with pytest.raises(ValueError):parse_native_channel_exit_policy(payload)


def test_quote_clock_and_comparison_cannot_be_overridden(installed):
    for field in ('quote_clock','comparison','missing'):
        payload=json.loads(canonical_json(policy(installed).payload()))
        payload['quote'][field]='changed'
        with pytest.raises(ValueError):parse_native_channel_exit_policy(payload)
