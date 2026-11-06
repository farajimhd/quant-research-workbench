"""Release redundant source-bar LRU frames after all replay inputs exist."""


def release_completed_source_cache(item):
    session=getattr(item,'shard_session',None)
    if session is None:return 0
    if not hasattr(item,'resident_features') or not hasattr(item,'shard_values'):
        raise ValueError('Complete prepared market inputs required before releasing source cache')
    released=session.cache_used
    session.cache.clear();session.cache_used=0
    # Keep seals/verified-file provenance, rank arrays, prepared tensors and the
    # on-disk authority. This is the same eviction supported by Session._read.
    item.quote_coverage['released_source_cache_bytes']=released
    return released
