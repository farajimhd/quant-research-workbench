"""Lossless, bounded feature tiles; source preparation runs once, never per batch."""
import os
os.environ['PYTHONDONTWRITEBYTECODE']='1'
import argparse
from collections import OrderedDict
import json
from pathlib import Path
import shutil
from time import perf_counter
import zlib
import numpy as np
from research.vectorized_backtest.v6.torch_backtest.runtime import file_hash,require_runtime,write_json
from research.vectorized_backtest.v6.torch_backtest.materialize import owned_run
from research.vectorized_backtest.v6.torch_backtest.run_structure import training_days
from .features import CATALOG,VERSION as FEATURE_VERSION

VERSION='v7-lossless-feature-tiles-v1'
TILE=2048
LISTINGS=4
WIDTH=len(CATALOG)
MASK_WIDTH=(WIDTH+7)//8

def identity(data):
    return dict(version=VERSION,feature_version=FEATURE_VERSION,source=data.identity,
        catalog=[f.name for f in CATALOG],clocks=data.clocks,listings=data.listing_ids.tolist(),
        tile=TILE,listing_tile=LISTINGS,implementation_sha256=file_hash(Path(__file__)),
        preparation_sha256=file_hash(Path(__file__).with_name('data.py')))

def tile_name(ticker,clock):return f'{ticker:04d}-{clock:05d}.bin'

def prepare(data,root,*,maximum_gib=1200.,progress=None):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);contract=identity(data)
    if (root/'complete.json').exists():
        cache=FeatureCache(root,data);record=cache.receipt;cache.close();return record
    started=perf_counter();files={};total=0;count=0
    with owned_run(root,version=VERSION):
        checkpoint=root/'checkpoint.json'
        if checkpoint.exists():
            saved=json.loads(checkpoint.read_text())
            if saved['identity']!=contract:raise ValueError('Feature preparation resume identity mismatch')
            files=saved['files']
            for name,checksum in files.items():
                if Path(name).name!=name or file_hash(root/name)!=checksum:raise ValueError('Prepared feature tile changed')
            total=sum((root/name).stat().st_size for name in files);count=len(files)
        for first in range(0,len(data.listing_ids),LISTINGS):
            listings=list(range(first,min(first+LISTINGS,len(data.listing_ids))))
            for begin in range(0,data.clocks,TILE):
                if (root/'STOP').exists():raise InterruptedError('Feature preparation stopped before next tile')
                name=tile_name(first,begin)
                if name in files:continue
                values,valid=data.prepare_feature_block(begin,min(begin+TILE,data.clocks),listings)
                raw=values.cpu().numpy().astype('<f4',copy=False).tobytes()+np.packbits(valid.cpu().numpy(),axis=-1,bitorder='little').tobytes()
                packed=zlib.compress(raw,level=1);total+=len(packed)
                if total>maximum_gib*1024**3:raise MemoryError('Feature cache exceeds explicit storage budget')
                if shutil.disk_usage(root).free<len(packed)+1024**3:raise MemoryError('Feature cache storage headroom exhausted')
                temporary=root/(name+'.tmp');temporary.write_bytes(packed);temporary.replace(root/name)
                files[name]=file_hash(root/name);count+=1
                write_json(checkpoint,dict(identity=contract,files=files))
                if progress:progress(dict(tiles=count,stored_bytes=total,wall_seconds=perf_counter()-started))
        record=dict(identity=contract,status='complete',validation_opened=False,files=files,
            stored_bytes=total,dense_bytes=data.clocks*len(data.listing_ids)*(WIDTH*4+MASK_WIDTH),wall_seconds=perf_counter()-started)
        write_json(root/'complete.json',record)
    return record

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

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','history','output'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--maximum-gib',type=float,default=1200.)
    p.add_argument('--first-session-only',action='store_true')
    a=p.parse_args();root=require_runtime(a.output)
    from .data import SessionData
    days=training_days(a.inputs);records={};started=perf_counter()
    with owned_run(root,version=VERSION):
        for day in days[:1] if a.first_session_only else days:
            if (root/'STOP').exists():raise InterruptedError('Feature preparation stopped')
            data=SessionData(a.inputs/day,a.history/day)
            try:
                record=prepare(data,root/day,maximum_gib=a.maximum_gib-sum(v['stored_bytes'] for v in records.values()),
                    progress=lambda status:write_json(root/'status.json',dict(status,day=day,completed=len(records),total=1 if a.first_session_only else 30)))
                records[day]={k:record[k] for k in ('stored_bytes','dense_bytes','wall_seconds')}
                records[day]['receipt_sha256']=file_hash(root/day/'complete.json')
                print(json.dumps(dict(day=day,**records[day])),flush=True)
            finally:data.close()
        write_json(root/('pilot.json' if a.first_session_only else 'complete.json'),dict(sessions=records,wall_seconds=perf_counter()-started,
            validation_opened=False,stored_bytes=sum(v['stored_bytes'] for v in records.values())))

if __name__=='__main__':main()
