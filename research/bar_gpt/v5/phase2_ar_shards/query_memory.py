"""Per-request memory tiers; identical SQL, bounded reconnects, no global settings."""

import time

from ..shards import source as sip


def tiers(initial, maximum):
    if not 0.25 <= initial <= maximum <= 8:
        raise ValueError('Require bounded per-query memory tiers in [0.25,8] GiB')
    result=[float(initial)]
    while result[-1]<maximum:result.append(min(float(maximum),result[-1]*2))
    return result


def execute(client, sql, query_id, gate, initial, maximum, timing):
    """Retry ONLY memory-limit errors at larger limits; always restore client."""
    limits=tiers(initial,maximum)
    for index,budget in enumerate(limits):
        awaiting=time.monotonic()
        try:
            with gate(budget):
                began=time.monotonic()
                timing['admission_wait_seconds']=timing.get('admission_wait_seconds',0)+began-awaiting
                params=client.default_query_params
                existed='max_memory_usage' in params;prior=params.get('max_memory_usage')
                params['max_memory_usage']=int(budget*1024**3)
                try:
                    for connection_attempt in range(3):
                        timing['queries']=timing.get('queries',0)+1
                        try:
                            return sip.arrow(client,sql,f'{query_id}-m{int(budget*1024)}-c{connection_attempt}',allow_empty=True)
                        except (ConnectionError,OSError):
                            if connection_attempt==2:raise
                            time.sleep(.25*2**connection_attempt)
                finally:
                    if existed:params['max_memory_usage']=prior
                    else:params.pop('max_memory_usage',None)
                    timing['query_seconds']=timing.get('query_seconds',0)+time.monotonic()-began
        except RuntimeError as error:
            if 'MEMORY_LIMIT_EXCEEDED' not in str(error) or index==len(limits)-1:raise
            timing['memory_retries']=timing.get('memory_retries',0)+1
    raise AssertionError('Unreachable memory tier exhaustion')
