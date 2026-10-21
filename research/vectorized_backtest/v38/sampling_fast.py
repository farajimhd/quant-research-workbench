"""Scalar uniform choices preserve NumPy Generator.choice's integer draw."""
from .genome import OPS

_ORDINARY=tuple(o for o in OPS if o not in ('identity','conv','attention'))
_ATTENTION=tuple(o for o in OPS if o not in ('identity','conv'))


def operator_choice(rng,fan_in):
    values=_ATTENTION if fan_in>=3 and fan_in%2==1 else _ORDINARY
    return values[int(rng.integers(len(values)))]
