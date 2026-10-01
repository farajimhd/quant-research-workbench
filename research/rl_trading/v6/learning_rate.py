"""Stateless learning-rate schedule bound to chronological teacher progress."""
import math


def cosine_warmup(position, total, peak, warmup=1., minimum_ratio=.1):
    """Position and total are epochs, including fractional session progress.

    The first epoch warms from 10% to peak; remaining epochs decay to 10%.
    Computing from checkpoint progress avoids resetting the schedule on resume.
    """
    if not (0 < warmup < total and peak > 0 and 0 < minimum_ratio <= 1
            and math.isfinite(position) and 0 <= position <= total):
        raise ValueError('Invalid cosine warm-up bounds')
    if position < warmup:
        factor=minimum_ratio+(1-minimum_ratio)*position/warmup
    else:
        factor=minimum_ratio+(1-minimum_ratio)*.5*(1+math.cos(
            math.pi*(position-warmup)/(total-warmup)))
    return peak*factor
