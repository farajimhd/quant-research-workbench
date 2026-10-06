"""Prepared own-source transport. Installed V4 admission is deliberately closed.

No token, caller flag or preparation alone grants installed submission authority.
The command-side journal is not a durable source certificate.
"""
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from src.backend.backtest_declared_native_fixed_entry import DeclaredEntryPreparation, DeclaredNativeFixedEntryProposal
from src.backend.backtest_market_data import market_day_boundary
from .signals import StrategyIntent, CapitalRequest
from .declared_native_entry_request import DeclaredEntryRequestPolicy
from src.backend.backtest_declared_base_entry_gate import DeclaredBaseEntryPolicy
from .strategy_one_stateful import StrategyOneFinancialView
from .execution_policies import (ExecutionPolicy, ExecutionPolicyName, ExecutionEnvelope,
    PartialFillPolicy, ProtectionProfile, ProtectionSlice, StopRule, StopRuleType,
    StopOrderType, TrailingRule, TrailingRuleType, AddProtectionPolicy, ProfitPocketTransition)


class _EmptyMetadata(dict):
    """Immutable empty native metadata with ordinary dataclass serialization."""
    def __init__(self, *args, **kwargs):
        if dict(*args, **kwargs):
            raise ValueError('Declared source cannot be carried in metadata')
        dict.__init__(self)

    def _reject(self, *args, **kwargs):
        raise TypeError('Declared source metadata is immutable')

    __setitem__ = __delitem__ = update = clear = pop = popitem = setdefault = __ior__ = _reject

    def __deepcopy__(self, memo):
        return {}


def _digest(value):
    return type(value) is str and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


@dataclass(frozen=True, slots=True)
class DeclaredSubmissionBinding:
    preparation: DeclaredEntryPreparation
    session_date: date
    entry_request: DeclaredEntryRequestPolicy
    configuration_hash: str
    execution_spec_token: str

    def __post_init__(self):
        if (type(self.preparation) is not DeclaredEntryPreparation or type(self.entry_request) is not DeclaredEntryRequestPolicy
                or type(self.session_date) is not date
                or self.session_date.isoformat() != self.preparation.source.parent.market.sessions[0]
                or not _digest(self.configuration_hash) or not _digest(self.execution_spec_token)):
            raise ValueError('Declared submission requires exact prepared run/source/configuration binding')
        self.preparation.__post_init__()
        self.entry_request.__post_init__()

    @property
    def identity(self):
        return self.preparation.source.parent.capabilities.identity


def require_installed_submission_binding(binding):
    """No installed own source closure or native V4 writer hook exists yet."""
    if type(binding) is not DeclaredSubmissionBinding:
        raise ValueError('Declared submission lacks exact binding')
    binding.__post_init__()
    raise RuntimeError('Declared own installed source approval and V4 submission hook are missing')


def declared_entry_intent(binding, proposal):
    """Own semantic request only; shared Portfolio computes actual cash/quantity."""
    if type(binding) is not DeclaredSubmissionBinding or type(proposal) is not DeclaredNativeFixedEntryProposal:
        raise ValueError('Declared entry intent requires its own exact proposal and binding')
    binding.__post_init__()
    capabilities = binding.preparation.source.parent.capabilities
    windows, _ = DeclaredBaseEntryPolicy(capabilities).parameters()
    if not any(start <= proposal.boundary_ms <= cutoff for start, cutoff, end in windows):
        raise ValueError('Declared entry request is outside declared extended acquisition sessions')
    policy = binding.entry_request.payload()
    at = market_day_boundary(binding.session_date, proposal.boundary_ms)
    capital = dict(policy['capital_request']); fraction = capital.pop('fraction')
    execution = dict(policy['execution_policy']); envelope = dict(execution.pop('envelope'))
    envelope.pop('maximum_buy_price_rule')
    execution['name'] = ExecutionPolicyName(execution['name'])
    execution['partial_fill_policy'] = PartialFillPolicy(execution['partial_fill_policy'])
    execution['envelope'] = ExecutionEnvelope(maximum_buy_price=proposal.reference_ask, **envelope)
    protection = dict(policy['protection_profile']); slices = []
    for payload in protection.pop('slices'):
        row = dict(payload); weight = row.pop('quantity_fraction'); stop = dict(row.pop('stop'))
        stop.pop('price_rule'); stop['rule_type'] = StopRuleType(stop['rule_type'])
        stop['order_type'] = StopOrderType(stop['order_type'])
        trailing = dict(row.pop('trailing')); trailing['rule_type'] = TrailingRuleType(trailing['rule_type'])
        row.pop('profit_target_price_rule')
        slices.append(ProtectionSlice(quantity_fraction=weight[0]/weight[1],
            stop=StopRule(price=proposal.initial_stop, **stop),
            profit_target_price=proposal.initial_target,trailing=TrailingRule(**trailing),**row))
    protection['add_policy'] = AddProtectionPolicy(protection['add_policy'])
    protection['profit_pocket_transition'] = ProfitPocketTransition(protection['profit_pocket_transition'])
    return StrategyIntent(intent_id=proposal.intent_id,ticker=proposal.ticker,event_time=at,
        action=policy['action'],quantity=policy['requested_quantity'],reference_price=proposal.reference_ask,
        capital_request=CapitalRequest(value=fraction[0]/fraction[1], **capital),
        invalidation_price=proposal.initial_stop,profit_target_price=proposal.initial_target,
        execution_policy=ExecutionPolicy(**execution),
        protection_profile=ProtectionProfile(slices=tuple(slices), **protection),
        urgency=policy['urgency'],time_in_force=policy['time_in_force'],
        outside_rth=any(start <= proposal.boundary_ms <= end for start, cutoff, end in windows),
        reason=policy['intent_reason'],metadata=_EmptyMetadata())


@dataclass(frozen=True, slots=True)
class DeclaredNativeSubmission:
    binding: DeclaredSubmissionBinding
    proposal: DeclaredNativeFixedEntryProposal
    financial: StrategyOneFinancialView
    intent: StrategyIntent
    reentry: object = None

    @property
    def assignment_id(self):
        return self.proposal.assignment_id

    @property
    def account_id(self):
        return self.proposal.account_id

    def verify(self, *, run_id, strategy_id, strategy_revision, account_id, session_date):
        if (type(self.binding) is not DeclaredSubmissionBinding or type(self.financial) is not StrategyOneFinancialView
                or type(self.proposal) is not DeclaredNativeFixedEntryProposal or type(self.intent) is not StrategyIntent):
            raise ValueError('Declared submission contains foreign typed sources')
        if (type(run_id) is not str or str(UUID(run_id)) != run_id or not UUID(run_id).int
                or type(strategy_id) is not str or not strategy_id
                or type(strategy_revision) is not int or strategy_revision <= 0
                or type(account_id) is not str or not account_id
                or type(session_date) is not date):
            raise ValueError('Declared submission context requires exact builtin identity types')
        self.binding.__post_init__()
        prep = self.binding.preparation
        identity = self.binding.identity
        if (run_id != prep.run_id or strategy_id != identity.strategy_id or strategy_revision != identity.revision
                or account_id != prep.account_id or session_date != self.binding.session_date):
            raise ValueError('Declared submission differs from own run/release/account')
        prep.verify_proposal(self.proposal,self.financial,reentry=self.reentry)
        if self.intent != declared_entry_intent(self.binding,self.proposal):
            raise ValueError('Declared semantic intent differs from exact own source')
        return self
