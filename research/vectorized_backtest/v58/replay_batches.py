"""Execution-only cohort packing; preserve candidate order and memory limits."""
from .config import STATES


def plan_batches(population, missing, width, base_size=512, maximum_size=1024,
                 *, workspace_bytes=4*2**30):
    if not 1<=base_size<=maximum_size<=6144 or width<1:
        raise ValueError('Invalid bounded replay batch configuration')
    batches=[];offset=0
    while offset<len(missing):
        selection=missing[offset:offset+maximum_size]
        nodes=sum(len(net.nodes) for i in selection for net in population[i].networks.values())
        required=nodes*width*5+len(selection)*width*len(STATES)*4
        # Keep the established batch when a larger one would exceed the
        # existing neural workspace limit. Never truncate a candidate or listing.
        if required>workspace_bytes:selection=missing[offset:offset+base_size]
        batches.append(selection);offset+=len(selection)
    return batches
