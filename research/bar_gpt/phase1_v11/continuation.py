"""Explicit scientific continuation: only schedule, horizon and source may change."""
def validate_continuation(parent, contract):
    old = parent['contract']
    stable = ('config','split_hash','calibration_sha256','batch_size','workers','prefetch',
              'shard_prefetch','shard_prefetch_mib','validation_batch_size','validation_workers',
              'validation_graphs','seed','weight_decay','gradient_clip')
    for key in stable:
        if old.get(key) != contract.get(key):
            raise ValueError('Continuation changed preserved contract: ' + key)
    if parent['phase'] not in ('train','validation') or not 0 <= parent['epoch'] < contract['epochs']:
        raise ValueError('Continuation horizon does not extend the saved traversal')
    if not contract['retain_optimizer']:
        raise ValueError('Continuation must retain optimizer state')
    return dict(parent_source=old['source_revision'],step=parent['step'],epoch=parent['epoch'],
                batch_cursor=parent['batch_cursor'],origins=parent['origins'],phase=parent['phase'])
