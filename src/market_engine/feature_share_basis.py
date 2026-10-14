"""Point-in-time share basis for QMD feature views, never broker prices."""
from datetime import date
from math import isfinite, isclose, prod


def price_factor(splits, previous, current):
    previous,current=date.fromisoformat(str(previous)),date.fromisoformat(str(current))
    if previous>=current:raise ValueError('Feature share-basis predecessor must precede current session')
    factors={}
    for row in splits:
        day=date.fromisoformat(str(row['execution_date']))
        if not previous<day<=current:continue
        before,after=float(row['split_from']),float(row['split_to'])
        if not all(isfinite(v) and v>0 for v in (before,after)):
            raise ValueError('Invalid feature split evidence')
        factor=before/after
        if day in factors and not isclose(factors[day],factor,rel_tol=1e-12,abs_tol=0):
            raise ValueError('Conflicting feature split evidence')
        factors[day]=factor
    result=prod(factors.values())
    if not isfinite(result) or result<=0:raise ValueError('Invalid accumulated feature split factor')
    return result
