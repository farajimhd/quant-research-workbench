"""Exact binary-occupancy metrics without materializing ticker/second grids."""
import numpy as np


def intervals(listings, entries, exits, *, listing_count):
    def integers(values):
        raw=np.asarray(values)
        if raw.size and (raw.dtype.kind not in 'iuf' or not np.isfinite(raw).all() or not (raw==np.floor(raw)).all()):
            raise ValueError('Integer finite position coordinates required')
        return raw.astype(np.int64)
    lane,a,b=map(integers,(listings,entries,exits))
    if any(x.ndim!=1 for x in (lane,a,b)) or not(len(lane)==len(a)==len(b)):
        raise ValueError('Position ledger lengths differ')
    if np.any(lane<0) or np.any(lane>=listing_count) or np.any(a<0) or np.any(b<a) or np.any(b>=86400):
        raise ValueError('Position outside certified listing/second bounds')
    b=np.maximum(b,a+1);a=lane*86400+a;b=lane*86400+b
    order=np.argsort(a,kind='stable');a,b=a[order],b[order]
    if len(a)>1 and np.any(a[1:]<b[:-1]):raise ValueError('Overlapping positions in one candidate')
    if len(a):
        boundaries=a[1:]!=b[:-1]
        a=a[np.r_[True,boundaries]];b=b[np.r_[boundaries,True]]
    return a,b


def intersection_matrix(candidates, *, scratch_mib=32):
    """Deduplicate exact interval patterns, then batch integral queries.

    Each sorted interval union is a lossless run-length encoding of binary
    occupancy. Prefix integrals return shared seconds exactly. Queries cover
    only the upper triangle and are split along candidates AND endpoints to
    bound scratch, independently of session length and position count.
    """
    if not .01<=scratch_mib<=256:raise ValueError('Scratch budget must be 0.01..256 MiB')
    unique=[];lookup={};inverse=[]
    for a,b in candidates:
        key=(a.tobytes(),b.tobytes())
        if key not in lookup:lookup[key]=len(unique);unique.append((a,b))
        inverse.append(lookup[key])
    n=len(unique);durations=np.array([int((b-a).sum()) for a,b in unique],dtype=np.int64)
    shared=np.diag(durations);maximum=max((int(b[-1]) for a,b in unique if len(b)),default=0)
    stride=maximum+86401
    if stride*(n+1)>=np.iinfo(np.int64).max:raise ValueError('Occupancy coordinate overflow')
    starts=np.concatenate([a+i*stride for i,(a,b) in enumerate(unique)]) if n else np.empty(0,dtype=np.int64)
    ends=np.concatenate([b+i*stride for i,(a,b) in enumerate(unique)]) if n else starts
    prefix=np.r_[0,np.cumsum(ends-starts,dtype=np.int64)]
    def integral(points):
        j=np.searchsorted(ends,points,side='right');k=np.minimum(j,len(starts)-1)
        return prefix[j]+np.where(j<len(starts),np.clip(points-starts[k],0,ends[k]-starts[k]),0)
    budget=int(scratch_mib*1024**2)
    if len(starts):
        for i,(a,b) in enumerate(unique):
            if not len(a):continue
            for first in range(i+1,n,32):
                last=min(n,first+32);offset=np.arange(first,last,dtype=np.int64)[:,None]*stride
                step=max(1,budget//(128*(last-first)))
                total=np.zeros(last-first,dtype=np.int64)
                for begin in range(0,len(a),step):
                    finish=begin+step
                    total+=(integral(offset+b[None,begin:finish])-integral(offset+a[None,begin:finish])).sum(1,dtype=np.int64)
                shared[i,first:last]=total;shared[first:last,i]=total
    inverse=np.asarray(inverse,dtype=np.int64)
    return shared[np.ix_(inverse,inverse)],durations[inverse],dict(candidates=len(candidates),unique_patterns=n,scratch_mib=scratch_mib)


def metrics(shared,durations):
    if np.any(shared<0) or not np.array_equal(shared,shared.T) or not np.array_equal(shared.diagonal(),durations) or np.any(shared>np.minimum(durations[:,None],durations[None,:])):
        raise ValueError('Invalid occupancy intersection matrix')
    occupied=durations>0;valid=occupied[:,None]&occupied[None,:]
    a=durations.astype(np.float64);intersection=shared.astype(np.float64)
    def divide(denominator):
        out=np.full(shared.shape,np.nan)
        np.divide(intersection,denominator,out=out,where=valid)
        return out
    return dict(jaccard=divide(a[:,None]+a[None,:]-intersection),
                cosine=divide(np.sqrt(a[:,None]*a[None,:])),
                coverage=divide(np.broadcast_to(a[:,None],shared.shape)))
