"""Separately declared causal geometry binding; absent means legacy binding."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class LadderGeometryBindingPolicy:
    version: str = 'ladder-wait-first-complete-geometry-v1'

    def __post_init__(self):
        if type(self.version) is not str or self.version != 'ladder-wait-first-complete-geometry-v1':
            raise ValueError('Unsupported ladder geometry binding policy')

    def payload(self):
        return asdict(self)


def declared_geometry_binding_policy(market_policy):
    if 'geometry_binding_policy' not in market_policy:
        return None
    value = market_policy['geometry_binding_policy']
    if type(value) is not dict or set(value) != {'version'}:
        raise ValueError('Invalid declared ladder geometry binding policy')
    return LadderGeometryBindingPolicy(**value)


def declared_ladder_runner_options(configuration):
    """Select the typed writer profile from the actual sealed declaration."""
    from src.backend.backtest_declared_ladder_plan import automatic_policy
    release = configuration.get('strategy', {}).get('numbered_release', {})
    geometry = declared_geometry_binding_policy(release.get('automatic_market_policy', {}))
    if automatic_policy(configuration) is None:
        if geometry is not None:
            raise ValueError('Waiting geometry requires a declared automatic ladder policy')
        return {}
    result = {'automatic_ladder':True}
    if geometry is not None:
        result['ladder_geometry_policy'] = geometry
    return result


@dataclass(frozen=True, slots=True)
class LadderGeometryBindingWitness:
    policy_version: str
    trigger_source_row_index: int
    trigger_boundary_ms: int
    freeze_boundary_ms: int
    admission_expiry_boundary_ms: int

    def __post_init__(self):
        LadderGeometryBindingPolicy(self.policy_version)
        if (type(self.trigger_source_row_index) is not int or not 0 <= self.trigger_source_row_index < 2**32
                or any(type(v) is not int or not 0 < v < 2**32 or v % 100 for v in
                       (self.trigger_boundary_ms, self.freeze_boundary_ms, self.admission_expiry_boundary_ms))
                or not 0 < self.trigger_boundary_ms <= self.freeze_boundary_ms < self.admission_expiry_boundary_ms):
            raise ValueError('Invalid causal ladder geometry binding witness')
