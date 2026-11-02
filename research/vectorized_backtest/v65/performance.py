"""Execution scheduling only; never a genome, score, or replay identity input."""
DEFAULT=dict(time_block=256,transfer_mode='synchronous')
TRANSFER_BUFFERS=8


def tuning(job):
    value=dict(DEFAULT,**job.get('replay_tuning',{}))
    if set(value)!=set(DEFAULT) or value['time_block'] not in (128,256,512,1024):
        raise ValueError('Unsupported independent-clock block')
    if value['transfer_mode'] not in ('synchronous','events'):
        raise ValueError('Unsupported replay transfer scheduling')
    return value


def configuration(job):
    workers=job.get('global_session_workers',2)
    if workers not in (2,4,6,8,12,16):raise ValueError('Unsupported bounded replay worker count')
    return dict(session_workers=workers,**tuning(job))


def loading(job):
    value=dict(resident=False,workers=3,depth=6)
    value.update(job.get('host_loading',{}))
    if set(value)!={'resident','workers','depth'} or type(value['resident']) is not bool:
        raise ValueError('Invalid host loading configuration')
    if not 1<=value['workers']<=8 or not value['workers']<=value['depth']<=32:
        raise ValueError('Unbounded host preparation')
    return value


def memory_budget(item,population,batches,block):
    """Conservative compact-path envelope; CPU episode ledgers are not CUDA allocations."""
    from .strategy_mix import leaves,node_count
    from .features import CATALOG
    from .price_program import REFERENCES
    nodes=[node_count(m) for m in population]
    leaf_counts=[len(leaves(m)) for m in population]
    largest_nodes=max(sum(nodes[i] for i in batch) for batch in batches)
    largest_leaves=max(sum(leaf_counts[i] for i in batch) for batch in batches)
    resident=int(item.bytes+item.resident_features.bytes)
    # Count unshared networks conservatively, including retained founder plans.
    packed=sum(nodes)*512
    workspace=block*largest_nodes*5*5
    # Projection/transforms/lag intermediates plus the bounded output ring.
    projection=block*5*len(CATALOG)*128
    outputs=block*largest_leaves*5*96*TRANSFER_BUFFERS
    prices=item.clocks*5*(len(REFERENCES)+1)*8
    context=2*1024**3
    required=resident+context+int(1.25*(packed+workspace+projection+outputs+prices))
    return dict(required_bytes=required,resident_bytes=resident,context_headroom_bytes=context,
        packed_bytes=packed,workspace_bytes=workspace,projection_bytes=projection,
        output_bytes=outputs,price_reference_bytes=prices,cpu_episode_ledger_gpu_bytes=0)
