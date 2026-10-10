"""Complete immutable channel exit declaration; no release or order admission."""
from dataclasses import dataclass

from .backtest_native_channel_sequences import (
    NativeChannelSequencePolicy, parse_native_channel_sequence_policy,
)
from src.trading_runtime.native_channel_quote_exit import (
    NativeChannelQuoteExitPolicy, parse_native_channel_quote_exit_policy,
)
from src.trading_runtime.native_channel_qualification import _require_policy_json
from src.trading_runtime.journal_contract import canonical_json


@dataclass(frozen=True,slots=True)
class NativeChannelExitPolicy:
    sequence: NativeChannelSequencePolicy
    quote: NativeChannelQuoteExitPolicy

    def __post_init__(self):
        if type(self.sequence) is not NativeChannelSequencePolicy or type(self.quote) is not NativeChannelQuoteExitPolicy:
            raise ValueError('Exact declared sequence and quote policies required')
        self.sequence.__post_init__()
        self.quote.__post_init__()
        if (self.quote.threshold_mode=='below_reference_close'
                and self.sequence.reference_mode!='below-first-post-acquisition-close'):
            raise ValueError('Reference quote confirmation requires declared post-acquisition reference')
        if self.quote.max_rows!=self.sequence.max_decisions:
            raise ValueError('Sequence and quote decision bounds differ')

    def payload(self):
        self.__post_init__()
        return dict(rule='native-completed-channel-exit@1',sequence=self.sequence.payload(),
            quote=self.quote.payload(),priority='inherited_exits_first',
            ownership='actual_current_entry_OMS_group_and_broker_fills',
            persistence='normalized_typed_native_checkpoint_before_order',
            missing='no_new_exit')


def parse_native_channel_exit_policy(value):
    _require_policy_json(value)
    required={'rule','sequence','quote','priority','ownership','persistence','missing'}
    if type(value) is not dict or set(value)!=required:
        raise ValueError('Complete native channel exit declaration required')
    policy=NativeChannelExitPolicy(parse_native_channel_sequence_policy(value['sequence']),
                                  parse_native_channel_quote_exit_policy(value['quote']))
    if canonical_json(value)!=canonical_json(policy.payload()):
        raise ValueError('Native channel exit priority, ownership or persistence semantics differ')
    return policy
