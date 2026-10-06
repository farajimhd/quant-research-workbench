"""Own management transport over the unchanged shared Portfolio/OMS actor.

Replay and prepared bindings are necessary input checks, not installed source
or historical financial approval. The installed gate remains closed until
normalized management publication and cold source/recovery hooks exist.
"""
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from uuid import UUID

from .declared_native_submission import DeclaredSubmissionBinding
from .declared_native_managed_execution import DeclaredNativeManagedExecutionSpec
from .declared_native_management_command import (
    DeclaredExitCommand, DeclaredProtectionCommand, DeclaredSessionCommand, DeclaredManagementContext, _equal,
)
from .declared_native_management_request import declared_management_intents
from .declared_native_fixed_capabilities import _json
from .signals import StrategyIntent


@dataclass(frozen=True, slots=True)
class DeclaredNativeManagementSubmission:
    binding: DeclaredSubmissionBinding
    spec: DeclaredNativeManagedExecutionSpec
    command: DeclaredExitCommand | DeclaredProtectionCommand | DeclaredSessionCommand
    intents: tuple[StrategyIntent, ...]

    @property
    def account_id(self):
        return self.command.context.financial.account_id

    @property
    def assignment_id(self):
        return self.command.context.financial.assignment_id

    @property
    def event_time(self):
        from src.backend.backtest_market_data import market_day_boundary
        return market_day_boundary(self.binding.session_date, self.command.context.boundary_ms)

    def matches_intents(self, intents):
        return type(intents) is tuple and _equal(intents, self.intents)

    def verify(self, *, run_id, strategy_id, strategy_revision, account_id, session_date):
        if (type(self.binding) is not DeclaredSubmissionBinding
                or type(self.spec) is not DeclaredNativeManagedExecutionSpec
                or type(self.command) not in (DeclaredExitCommand, DeclaredProtectionCommand, DeclaredSessionCommand)
                or type(self.intents) is not tuple
                or any(type(intent) is not StrategyIntent for intent in self.intents)):
            raise ValueError('Declared management requires exact complete transport types')
        if (type(run_id) is not str or str(UUID(run_id)) != run_id or not UUID(run_id).int
                or type(strategy_id) is not str or not strategy_id
                or type(strategy_revision) is not int or strategy_revision <= 0
                or type(account_id) is not str or not account_id or type(session_date) is not date):
            raise ValueError('Declared management context requires exact builtin identity types')
        self.binding.__post_init__()
        self.spec.__post_init__()
        context = self.command.context
        if type(context) is not DeclaredManagementContext:
            raise ValueError('Declared management has a foreign command context')
        context.__post_init__()
        execution = self.spec.execution
        source_candidate = self.binding.preparation.source.candidate
        if (type(source_candidate) is not type(execution.candidate)
                or _json(source_candidate.payload()) != _json(execution.candidate.payload())):
            raise ValueError('Declared management differs from complete prepared source candidate')
        capabilities = self.binding.preparation.source.parent.capabilities
        identity = capabilities.identity
        if (context.preparation is not self.binding.preparation
                or context.session_date != self.binding.session_date
                or (run_id, strategy_id, strategy_revision, account_id, session_date)
                    != (context.run_id, identity.strategy_id, identity.revision,
                        context.financial.account_id, context.session_date)
                or self.binding.preparation.account_id != account_id
                or self.binding.preparation.assignment_id != context.financial.assignment_id
                or _json(capabilities.payload()) != _json(execution.candidate.base.payload())
                or _json(context.policy.capabilities.payload()) != _json(capabilities.payload())
                or self.binding.entry_request != execution.entry_request
                or self.binding.execution_spec_token != sha256(_json(execution.payload()).encode()).hexdigest()):
            raise ValueError('Declared management differs from its full prepared binding')
        expected = declared_management_intents(self.command, self.spec.management_request)
        if not _equal(self.intents, expected):
            raise ValueError('Declared management semantic requests differ from replay')
        return self


def declared_management_submission(binding, spec, command):
    if (type(binding) is not DeclaredSubmissionBinding
            or type(spec) is not DeclaredNativeManagedExecutionSpec
            or type(command) not in (DeclaredExitCommand, DeclaredProtectionCommand, DeclaredSessionCommand)):
        raise ValueError('Declared management factory needs complete own inputs')
    submission = DeclaredNativeManagementSubmission(binding, spec, command,
        declared_management_intents(command, spec.management_request))
    identity = binding.identity
    return submission.verify(run_id=binding.preparation.run_id, strategy_id=identity.strategy_id,
        strategy_revision=identity.revision, account_id=binding.preparation.account_id,
        session_date=binding.session_date)


def require_installed_management_binding(submission):
    if type(submission) is not DeclaredNativeManagementSubmission:
        raise ValueError('Declared management lacks its exact own transport')
    identity = submission.binding.identity
    submission.verify(run_id=submission.binding.preparation.run_id,
        strategy_id=identity.strategy_id, strategy_revision=identity.revision,
        account_id=submission.account_id, session_date=submission.binding.session_date)
    raise RuntimeError('Declared own installed management source and durable V4 recovery hooks are missing')
