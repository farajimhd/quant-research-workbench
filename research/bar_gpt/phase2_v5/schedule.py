"""Pass-local cosine decay and deterministic certified-session traversal."""
import math
import polars as pl

from ..phase2_v2.release import ReleasedUnits
from ..v5.phase2_ar_shards.contract import BLOCK

PASSES = 10
FLOOR = 5e-6
PEAK_FACTOR = .9
LEGACY_SOURCE = "001f861f8bd3833afca302988994c5d0c727eb0e"


def cosine_rate(peak, pass_index, consumed, total):
    """Use the one-based position after the incoming batch to hit the floor."""
    if not 0 <= pass_index < PASSES or not 0 <= consumed <= total or total <= 0:
        raise ValueError("Invalid pass or origin count")
    ceiling = peak * PEAK_FACTOR**pass_index
    if not math.isfinite(ceiling) or ceiling <= FLOOR:
        raise ValueError("Pass peak must exceed the learning-rate floor")
    return FLOOR + (ceiling - FLOOR) * (1 + math.cos(math.pi * consumed / total)) / 2


def order_for_pass(units, pass_index):
    if pass_index == 0:
        return units
    groups = []
    for root_index, (root, binding, rows) in enumerate(units.groups):
        train = rows.filter(pl.col("unit_split") == 0)
        train = train.sample(fraction=1., shuffle=True, seed=17041 + root_index + 1009 * pass_index)
        validation = rows.filter(pl.col("unit_split") == 1)
        groups.append((root, binding, train.vstack(validation)))
    return ReleasedUnits(groups)


def candidate_prefix(units, session_index, offset=0):
    """Count selected training origins before a cursor and committed rows within it."""
    if not 0 <= session_index <= len(units) or offset < 0:
        raise ValueError("Invalid certified session cursor")
    count = 0
    for _, _, table in units.groups:
        included = min(session_index, table.height)
        prefix = table.head(included).filter(pl.col("unit_split") == 0)
        count += int(prefix["ordinal"].list.len().sum()) * BLOCK
        session_index -= included
        if session_index == 0:
            break
    return count + offset
