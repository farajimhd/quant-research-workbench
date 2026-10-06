"""Full managed declaration readback over the common certified source reader.

The child configuration is never rewritten to a candidate-only envelope. Every
read independently recompiles its complete managed declaration and checks the
actual child hash against the fenced run. Source equivalence alone cannot
authorize an entry, attest historical finances, or restore manager state.
"""
from .arte_declared_native_fixed_sources import PreparedDeclaredSourceResolver
from .declared_native_managed_execution import (
    DeclaredNativeManagedExecutionSpec,
    parse_declared_native_managed_execution,
    verify_declared_managed_configuration,
)
from .declared_native_fixed_capabilities import _json
from .declared_native_fixed_candidate import NativeFixedCandidateSpec
from .declared_native_entry_source import DeclaredNativeEntrySourcePolicy


class PreparedDeclaredManagedSourceResolver(PreparedDeclaredSourceResolver):
    """Explicit full-spec reader sharing the existing producer/run fences."""

    def __init__(self, client, *, run_id, spec, envelope, approval, market):
        if type(spec) is not DeclaredNativeManagedExecutionSpec:
            raise ValueError("Managed source needs its exact complete declaration")
        self._managed_spec = parse_declared_native_managed_execution(spec.payload())
        execution = self._managed_spec.execution
        super().__init__(client, run_id=run_id, candidate=execution.candidate,
            envelope=envelope, approval=approval, market=market,
            source_policy=execution.entry_source)

    def _verify_configuration(self, envelope, approval):
        if type(self._managed_spec) is not DeclaredNativeManagedExecutionSpec:
            raise ValueError("Managed source lost its complete declaration")
        self._managed_spec.__post_init__()
        execution = self._managed_spec.execution
        if (type(self.candidate) is not NativeFixedCandidateSpec
                or type(self.source_policy) is not DeclaredNativeEntrySourcePolicy
                or _json(self.candidate.payload()) != _json(execution.candidate.payload())
                or _json(self.source_policy.payload()) != _json(execution.entry_source.payload())):
            raise ValueError("Managed source candidate or producer policy differs from complete declaration")
        return verify_declared_managed_configuration(self.client, self._managed_spec,
            envelope, approval=approval)
