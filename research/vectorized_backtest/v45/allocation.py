"""Exact approved population budgets; fallbacks replace slots rather than add them."""
import math


def budgets(target=1024):
    if type(target) is not int or not 10 <= target <= 65536:
        raise ValueError('Bounded integer population required')
    elites = target // 10
    fresh = math.ceil(target * .2)
    cross = target - elites - fresh
    return dict(elites=elites, fresh=fresh, global_crossovers=cross // 2,
                case_crossovers=cross - cross // 2)
