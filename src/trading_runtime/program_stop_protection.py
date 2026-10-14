"""Native protection layout bound after the frozen programme selects its stop."""
from dataclasses import dataclass
from math import isfinite, isclose

from .execution_policies import ProtectionProfile, ProtectionSlice, StopRule


@dataclass(frozen=True, slots=True)
class ProgramStopProtection:
    profile_id: str
    revision: int
    slice_fractions: tuple

    def __post_init__(self):
        if (type(self.profile_id) is not str or not self.profile_id or len(self.profile_id) > 256
                or type(self.revision) is not int or self.revision < 1
                or type(self.slice_fractions) is not tuple or not 1 <= len(self.slice_fractions) <= 512
                or any(type(value) is not float or not isfinite(value) or not 0 < value <= 1
                    for value in self.slice_fractions)
                or not isclose(sum(self.slice_fractions), 1., rel_tol=0., abs_tol=1e-12)):
            raise ValueError('Programme protection requires an explicit complete native slice layout')

    def for_proposal(self, proposal):
        from .columnar_program_coordinator import ProgramProposal
        if type(proposal) is not ProgramProposal:
            raise TypeError('Programme protection requires the selected typed proposal')
        if proposal.action not in {'enter_long', 'add_long'}:
            return None
        if proposal.stop_price is None or not isfinite(proposal.stop_price) or proposal.stop_price <= 0:
            raise ValueError('Programme acquisition lacks its final selected stop')
        return ProtectionProfile(self.profile_id, self.revision, tuple(
            ProtectionSlice(f'slice-{index + 1:03}', fraction, StopRule(price=proposal.stop_price))
            for index, fraction in enumerate(self.slice_fractions)))
