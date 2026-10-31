import threading
_completion_lock=threading.Lock()
_completion_reserved=0
_host_reserved=0

def reserve_host_load(available,reserve_gib,admission_gib,*,speculative=False):
    """Reserve unallocated load bytes against concurrent completion tasks."""
    global _host_reserved
    budget=admission_gib*2**30
    with _completion_lock:
        # Prefetch owns the same explicit allocation reservation as demand.
        # Charging a second unallocated 32 GiB prevented otherwise safe loads.
        required=(reserve_gib+admission_gib)*2**30+_completion_reserved+_host_reserved
        if available<required:return None,required
        _host_reserved+=budget
        return budget,required

def release_host_load(budget):
    global _host_reserved
    with _completion_lock:
        _host_reserved-=budget
        if _host_reserved<0:raise RuntimeError('Host load reservation released twice')
