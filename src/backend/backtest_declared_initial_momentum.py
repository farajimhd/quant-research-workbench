"""Versioned declared selection; original structural anchor never changes."""
from dataclasses import dataclass
from bisect import bisect_left
from hashlib import sha256
import numpy as np
from .backtest_strategy_one_candidate_store import CertifiedCandidatePlan
from .backtest_strategy_one_entry_store import CertifiedEntryEvidencePlan
from .backtest_strategy_rising_momentum import CertifiedRisingMomentumPlan
import re
from .backtest_strategy_rising_momentum import _frozen
from src.trading_runtime.entry_momentum_growth import EntryMomentumGrowthPolicy,growth_mask,declared_initial_entry
from src.trading_runtime.strategy_initial_strong_momentum import InitialStrongMomentumWitness,InitialMomentumSelectionWitness,initial_strong_momentum_entry_mask
from src.trading_runtime.journal_contract import canonical_json

def _declared_selection(candidates,entry,momentum,policy):
    if type(policy) is not EntryMomentumGrowthPolicy:
        raise ValueError('Declared selection requires exact policy')
    if (type(candidates) is not CertifiedCandidatePlan or type(entry) is not CertifiedEntryEvidencePlan
            or type(momentum) is not CertifiedRisingMomentumPlan
            or momentum.source_build_id != candidates.source_build_id
            or momentum.candidate_plan_token != candidates.token
            or any(type(token) is not str or re.fullmatch(r'[0-9a-f]{64}',token) is None for token in (candidates.token,entry.token))):
        raise ValueError('Declared initial momentum requires exact certified parent plans')
    from .backtest_strategy_one_static_gate import compile_static_entry_gate
    base=compile_static_entry_gate(candidates,entry,strategy_number=12)
    keys=tuple((fact.ticker,fact.boundary_ms) for fact in base.facts)
    if keys != momentum.keys or np.any((base.rejection_mask==0)&~momentum.requested_mask):
        raise ValueError('Declared source omits or changes original base candidate keys')
    _,codes=np.unique(np.asarray([fact.ticker for fact in base.facts]),return_inverse=True)
    starts=np.fromiter((fact.episode_start_ms for fact in base.facts),dtype=np.int64)
    boundaries=np.asarray([key[1] for key in keys],dtype=np.int64)
    args=(momentum.current_boundaries_ms,momentum.prior_boundaries_ms,momentum.current_line,momentum.current_signal,momentum.prior_line,momentum.prior_signal)
    first_mask=growth_mask(policy,policy.first_fraction,boundaries,*args)
    current_mask=growth_mask(policy,policy.current_fraction,boundaries,*args)
    first,_=initial_strong_momentum_entry_mask(codes.astype(np.int64),starts,boundaries,base.rejection_mask==0,current_mask)
    eligible=(base.rejection_mask==0)&current_mask&(first>=0)
    if len(first):
        eligible &= first_mask[np.maximum(first,0)]
    digest=sha256(canonical_json(policy.payload()).encode())
    for token in (candidates.token,entry.token,momentum.token):digest.update(token.encode())
    for value in (starts,first,eligible):digest.update(value.tobytes())
    return starts,first,eligible,digest.hexdigest()

@dataclass(frozen=True,slots=True)
class CertifiedDeclaredInitialMomentumPlan:
    candidates: object
    entry: object
    momentum: object
    policy: EntryMomentumGrowthPolicy
    episode_start_ms: np.ndarray
    first_indices: np.ndarray
    eligible_mask: np.ndarray
    token: str
    def __post_init__(self):
        expected=_declared_selection(self.candidates,self.entry,self.momentum,self.policy)
        for name,actual,wanted in zip(('episode_start_ms','first_indices','eligible_mask'),(self.episode_start_ms,self.first_indices,self.eligible_mask),expected[:3]):
            if type(actual) is not np.ndarray or actual.dtype!=wanted.dtype or not np.array_equal(actual,wanted):
                raise ValueError('Declared momentum original anchor/mask differs')
            object.__setattr__(self,name,_frozen(actual))
        if self.token!=expected[3]:raise ValueError('Declared momentum policy/source seal differs')
    @property
    def initial(self):return self
    def lookup(self,ticker,boundary_ms):
        if type(ticker) is not str or type(boundary_ms) is not int:raise ValueError('Declared lookup requires exact keys')
        index=bisect_left(self.momentum.keys,(ticker,boundary_ms))
        if index>=len(self.momentum.keys) or self.momentum.keys[index]!=(ticker,boundary_ms) or not self.eligible_mask[index]:
            raise ValueError('Declared momentum lookup outside eligible population')
        anchor=InitialStrongMomentumWitness(int(self.episode_start_ms[index]),self.momentum.lookup(*self.momentum.keys[int(self.first_indices[index])]))
        if not declared_initial_entry(self.momentum.lookup(ticker,boundary_ms),anchor,self.policy):raise ValueError('Declared scalar differs from vector selection')
        return anchor
    def selection_witness(self,ticker,boundary_ms):
        return InitialMomentumSelectionWitness(self.lookup(ticker,boundary_ms),self.candidates.token,self.entry.token,self.token)

def compile_declared_initial_momentum_plan(candidates,entry,momentum,policy):
    values=_declared_selection(candidates,entry,momentum,policy)
    return CertifiedDeclaredInitialMomentumPlan(candidates,entry,momentum,policy,*values)
