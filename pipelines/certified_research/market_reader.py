"""Bounded completed-bucket reads pinned to independently verified receipts."""
from datetime import datetime,timezone

from .contracts import identifier
from .publication import Publisher,Scope,PRODUCTS


def read(query,database,scope,claim,authority_guard,*,cutoff,resolutions=(1000,),maximum_rows=100000):
    """Read common primitives; no padding, feature export or current-ticker join.

    The caller supplies the frozen scope and a guard that independently verifies
    dataset/collection and dated membership authority. A table name or a dataset
    ID alone is not that authority. Both guards and receipt readback are repeated
    before returning rows, so a changed authority never yields an admitted read.
    """
    identifier(database)
    if (not isinstance(scope,Scope) or scope.product not in ('bars','technical','broker_100ms','activity')
            or not callable(authority_guard) or not isinstance(cutoff,datetime) or cutoff.tzinfo is None
            or cutoff.utcoffset() is None or type(maximum_rows) is not int or not 1<=maximum_rows<=1000000):
        raise ValueError('Invalid bounded certified market read')
    if not isinstance(resolutions,(tuple,list)) or not 1<=len(resolutions)<=32:
        raise ValueError('Invalid explicit market resolutions')
    resolutions=tuple(resolutions)
    if (any(type(r) is not int or not 1<=r<=86400000 for r in resolutions)
            or len(set(resolutions))!=len(resolutions)):
        raise ValueError('Invalid explicit market resolutions')
    if scope.product=='broker_100ms' and resolutions!=(100,):
        raise ValueError('Liquidity evidence requires the 100ms frame')
    def guard():
        claim.check();authority_guard();claim.check()
    guard();publisher=Publisher(query,database,claim)
    receipt=publisher.receipt(scope)
    if receipt is None:raise ValueError('Market product has no certified receipt')
    delta=cutoff.astimezone(timezone.utc)-datetime(1970,1,1,tzinfo=timezone.utc)
    cutoff_us=(delta.days*86400+delta.seconds)*1000000+delta.microseconds
    origin="toUnixTimestamp64Micro(toDateTime64(toString(session_date),6,'America/New_York'))"
    start=f'{origin}+toInt64(bucket_index)*toInt64(resolution_ms)*1000'
    end=f'{origin}+(toInt64(bucket_index)+1)*toInt64(resolution_ms)*1000'
    rows=query(f'SELECT *,{start} bucket_start_us,{end} bucket_end_us FROM {database}.{PRODUCTS[scope.product]} '
        f'WHERE {scope.where(receipt["attempt_id"])} AND resolution_ms IN ({",".join(map(str,resolutions))}) '
        f'AND {end}<={cutoff_us} ORDER BY resolution_ms,bucket_index'
        +(',window_seconds' if scope.product=='activity' else '')+f' LIMIT {maximum_rows+1}')
    if len(rows)>maximum_rows:raise ValueError('Market read exceeds its row bound; narrow the scope')
    guard()
    if publisher.receipt(scope)!=receipt:raise ValueError('Market receipt changed during the read')
    guard()
    return dict(version='certified-completed-market-read-v1',receipt=receipt,rows=tuple(rows),
        cutoff_us=cutoff_us,completed_buckets_only=True)
