"""Declared first/current growth from unchanged completed10s producer values."""
from dataclasses import dataclass
import re
import numpy as np
from .strategy_rising_momentum_entry import rising_momentum_entry_mask
from .strategy_initial_strong_momentum import validate_initial_strong_momentum_witness
from .strategy_rising_momentum_witness import validate_momentum_witness

@dataclass(frozen=True, slots=True)
class EntryMomentumGrowthPolicy:
    policy_id: str
    first_fraction: tuple[int, int]
    current_fraction: tuple[int, int]
    def __post_init__(self):
        if type(self.policy_id) is not str or re.fullmatch(r'entry-momentum-first-(?:5|10)-current-(?:5|10)-percent@1', self.policy_id) is None:
            raise ValueError('Declared momentum policy identity differs')
        for value in (self.first_fraction, self.current_fraction):
            if type(value) is not tuple or value not in ((1,10),(1,20)) or any(type(v) is not int for v in value):
                raise ValueError('Declared momentum fraction is outside frozen grid')
        expected = f'entry-momentum-first-{100*self.first_fraction[0]//self.first_fraction[1]}-current-{100*self.current_fraction[0]//self.current_fraction[1]}-percent@1'
        if self.policy_id != expected:
            raise ValueError('Declared momentum fractions differ from policy identity')
    def payload(self):
        return dict(policy_id=self.policy_id, first_fraction=list(self.first_fraction), current_fraction=list(self.current_fraction), resolution_ms=10000,
                    comparison='H > 0 and H > P + fraction * abs(P)', clock='completed_adjacent_producer_observations',
                    first_anchor='earliest_structurally_eligible_ticker_activation_episode_before_momentum_pruning', scope='entry_and_reentry', missing='reject_without_shifting_first_anchor')

def declared_momentum_policy(strategy_number):
    from .numbered_fixed_strategy import numbered_fixed_strategy
    return getattr(numbered_fixed_strategy(strategy_number), 'entry_momentum_growth_policy', None)

def growth_mask(policy, fraction, boundaries, *arrays):
    if (type(policy) is not EntryMomentumGrowthPolicy
            or type(fraction) is not tuple or len(fraction) != 2
            or any(type(value) is not int for value in fraction)
            or fraction not in (policy.first_fraction,policy.current_fraction)):
        raise ValueError('Momentum mask requires declared fraction')
    # Reuse unchanged clock/Float64/missing validation, never its10% eligibility.
    rising_momentum_entry_mask(boundaries, *arrays)
    arrays = tuple(np.asarray(value) for value in arrays)
    current, prior, line, signal, old_line, old_signal = arrays
    available=(current[:,1]>0)&(prior[:,1]>0)&np.isfinite(line[:,1])&np.isfinite(signal[:,1])&np.isfinite(old_line[:,1])&np.isfinite(old_signal[:,1])
    with np.errstate(over='ignore',invalid='ignore'):
        h=line[:,1]-signal[:,1];p=old_line[:,1]-old_signal[:,1];threshold=p+(fraction[0]/fraction[1])*np.abs(p)
    if np.any(available&(~np.isfinite(h)|~np.isfinite(p)|~np.isfinite(threshold))):
        raise ValueError('Declared momentum comparison overflows')
    return available&(h>0)&(h>threshold)

def observation_entry(witness, policy, fraction):
    validate_momentum_witness(witness)
    clocks=tuple(np.asarray([[getattr(o,n) for o in witness.observations]],dtype=np.int64) for n in ('current_boundary_ms','prior_boundary_ms'))
    values=tuple(np.asarray([[np.nan if getattr(o,n) is None else getattr(o,n) for o in witness.observations]],dtype=np.float64) for n in ('current_line','current_signal','prior_line','prior_signal'))
    return bool(growth_mask(policy,fraction,np.asarray([witness.boundary_ms],dtype=np.int64),*clocks,*values)[0])

def declared_initial_entry(current, initial, policy):
    validate_initial_strong_momentum_witness(current, initial)
    first=observation_entry(initial.first_setup,policy,policy.first_fraction)
    now=observation_entry(current,policy,policy.current_fraction)
    return first and now

def selected_initial_entry(current, initial, strategy_number):
    policy = declared_momentum_policy(strategy_number)
    if policy is not None:
        return declared_initial_entry(current, initial, policy)
    from .strategy_initial_strong_momentum import initial_strong_momentum_entry
    return initial_strong_momentum_entry(current, initial)
