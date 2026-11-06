"""Lossless column-wise runs for immutable, repeatedly used price references."""
from collections.abc import MutableMapping
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class Runs:
    values: np.ndarray
    lengths: np.ndarray
    shape: tuple

    def decode(self):
        # Compare/store integer bits: preserve signed zero and every NaN payload.
        # PricePlan stacks these arrays into a bank addressed as contiguous by
        # its fused GPU kernel. A transposed view changes that bank's strides.
        return np.repeat(self.values,self.lengths).view(np.float64).reshape(self.shape[::-1]).T.copy(order='C')

    @property
    def nbytes(self):
        return self.values.nbytes+self.lengths.nbytes


def pack(value):
    if not isinstance(value,np.ndarray) or value.dtype!=np.float64 or value.ndim!=2 or value.nbytes<4096:
        return value
    bits=np.ascontiguousarray(value.T).view(np.uint64).reshape(-1)
    starts=np.flatnonzero(np.r_[True,bits[1:]!=bits[:-1]])
    lengths=np.diff(np.r_[starts,len(bits)])
    if lengths.max()>np.iinfo(np.uint32).max:return value
    if len(starts)*12>value.nbytes*.75:return value
    return Runs(bits[starts],lengths.astype(np.uint32),value.shape)


class PriceCache(MutableMapping):
    """Private cache; callers read arrays, never mutate cached reference values."""
    def __init__(self):
        self.records={}

    def __getitem__(self,key):
        value=self.records[key]
        return value.decode() if isinstance(value,Runs) else value

    def __setitem__(self,key,value):
        self.records[key]=pack(value)

    def __delitem__(self,key):
        del self.records[key]

    def __iter__(self):
        return iter(self.records)

    def __len__(self):
        return len(self.records)

    def __contains__(self,key):
        return key in self.records

    @property
    def nbytes(self):
        return sum(v.nbytes if hasattr(v,'nbytes') else sum(a.nbytes for a in v)
                   for v in self.records.values())
