from collections import OrderedDict
import json,zlib
from pathlib import Path
import numpy as np
from .runtime import file_hash
from .features import ORIGINAL as CATALOG
VERSION="v7-lossless-feature-tiles-v1"
FEATURE_VERSION="v7-original-plus-causal-history-relative-v1"
TILE=2048
LISTINGS=4
WIDTH=len(CATALOG)
MASK_WIDTH=(WIDTH+7)//8

def identity(data):
    return dict(version=VERSION,feature_version=FEATURE_VERSION,source=data.identity,
        catalog=[f.name for f in CATALOG],clocks=data.clocks,listings=data.listing_ids.tolist(),
        tile=TILE,listing_tile=LISTINGS,implementation_sha256='8351a204cd640b6b6e02832a50a1e96a9c17ad341c646f62b9eb9b8fbd8f0a2c',
        preparation_sha256='272796087455666012dd65d4fcc7e13d11caf0bb049fb2e183d374dccd7e5857')

def tile_name(ticker,clock):return f'{ticker:04d}-{clock:05d}.bin'

class FeatureCache:
    def __init__(self,root,data,maximum_host_gib=1.):
        self.root=Path(root);self.receipt=json.loads((self.root/'complete.json').read_text())
        if self.receipt.get('identity')!=identity(data) or self.receipt.get('status')!='complete' or self.receipt.get('validation_opened') is not False:
            raise ValueError('Feature cache source/schema identity mismatch')
        expected={tile_name(u,t) for u in range(0,len(data.listing_ids),LISTINGS) for t in range(0,data.clocks,TILE)}
        if set(self.receipt['files'])!=expected:raise ValueError('Feature cache tile coverage mismatch')
        self.stats={name:(p.stat().st_size,p.stat().st_mtime_ns) for name in expected for p in [self.root/name]}
        self.clocks=data.clocks;self.listings=len(data.listing_ids);self.limit=int(maximum_host_gib*1024**3)
        self.cache=OrderedDict();self.bytes=0;self.verified=set();self.loads=0;self.hits=0
    def load(self,first,begin):
        name=tile_name(first,begin);path=self.root/name
        if (path.stat().st_size,path.stat().st_mtime_ns)!=self.stats[name]:raise ValueError('Feature tile changed')
        if name in self.cache:
            self.hits+=1;self.cache.move_to_end(name);return self.cache[name]
        if name not in self.verified:
            if file_hash(path)!=self.receipt['files'][name]:raise ValueError('Feature tile integrity mismatch')
            self.verified.add(name)
        shape=(min(LISTINGS,self.listings-first),min(TILE,self.clocks-begin),WIDTH)
        value_bytes=int(np.prod(shape))*4;mask_bytes=shape[0]*shape[1]*MASK_WIDTH
        decoder=zlib.decompressobj();raw=decoder.decompress(path.read_bytes(),value_bytes+mask_bytes+1)
        if len(raw)!=value_bytes+mask_bytes or not decoder.eof or decoder.unused_data:raise ValueError('Feature tile length mismatch')
        values=np.frombuffer(raw,dtype='<f4',count=value_bytes//4).reshape(shape)
        masks=np.frombuffer(raw,dtype=np.uint8,offset=value_bytes).reshape((*shape[:2],MASK_WIDTH))
        valid=np.unpackbits(masks,axis=-1,count=WIDTH,bitorder='little').astype(bool)
        size=values.nbytes+valid.nbytes
        if size>self.limit:raise MemoryError('Feature tile exceeds host cache envelope')
        while self.bytes+size>self.limit:
            _,old=self.cache.popitem(last=False);self.bytes-=sum(v.nbytes for v in old)
        self.cache[name]=(values,valid);self.bytes+=size;self.loads+=1
        return values,valid
    def block(self,begin,end,listings):
        if not 0<=begin<end<=self.clocks or any(not 0<=i<self.listings for i in listings):raise ValueError('Invalid feature cache slice')
        values=np.empty((len(listings),end-begin,WIDTH),dtype=np.float32);valid=np.empty_like(values,dtype=bool)
        for slot,listing in enumerate(listings):
            first=listing//LISTINGS*LISTINGS
            for clock in range(begin//TILE*TILE,end,TILE):
                v,m=self.load(first,clock);left=max(begin,clock);right=min(end,clock+TILE)
                values[slot,left-begin:right-begin]=v[listing-first,left-clock:right-clock]
                valid[slot,left-begin:right-begin]=m[listing-first,left-clock:right-clock]
        return values,valid
    def close(self):self.cache.clear();self.bytes=0
