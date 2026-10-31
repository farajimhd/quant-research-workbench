"""Bounded asynchronous Parquet order journal, separate from fitness messages."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from hashlib import sha256
import time,uuid,json,os
import numpy as np
import polars as pl
from research.vectorized_backtest.v60.runtime import require_runtime


def write_json(path,value):
    # Validate the ordinary path first; use extended paths only at the Windows
    # filesystem boundary. Receipts retain their portable ordinary paths.
    path=Path(path);require_runtime(path.parent)
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False)
    target=Path('\\\\?\\'+str(path.resolve())) if os.name=='nt' else path
    temporary=target.with_name(uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(raw,encoding='utf8')
        for attempt in range(12):
            try:temporary.replace(target);break
            except PermissionError as error:
                if getattr(error,'winerror',None) not in (5,32) or attempt==11:raise
                time.sleep(min(.02*2**attempt,.2))
    finally:temporary.unlink(missing_ok=True)

COLUMNS=('listing','kind','requested_quantity','filled_quantity','filled_notional',
         'fees','submitted_clock','first_fill_clock','completed_clock')


class Journal:
    def __init__(self,root,population,identity):
        self.root=Path(root)/uuid.uuid4().hex;self.root.mkdir(parents=True)
        self.identity=identity;self.population=population
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='order-journal')
        self.pending=deque();self.pages=[];self.serial=0;self.wait_seconds=0.;self.closed=False
        self.counts=np.zeros(population,dtype=np.int64)
        self.hashes=[sha256() for _ in range(population)]

    def write(self,serial,ids,counts,data):
        candidates=np.repeat(ids,counts)
        frame=pl.DataFrame(dict(candidate=candidates,**{name:data[:,i] for i,name in enumerate(COLUMNS)}))
        frame=frame.with_columns(pl.col('listing','kind').cast(pl.Int64))
        name=f'orders-{serial:05d}.parquet';path=self.root/name;temp=path.with_suffix('.partial')
        frame.write_parquet(temp,compression='zstd');temp.replace(path)
        h=sha256()
        with path.open('rb') as f:
            for block in iter(lambda:f.read(2**20),b''):h.update(block)
        self.pages.append(dict(file=name,sha256=h.hexdigest(),rows=len(data)))
        offset=0
        for index,count in zip(ids,counts):
            end=offset+int(count)
            if count:self.hashes[int(index)].update(data[offset:end].tobytes(order='C'))
            self.counts[int(index)]+=int(count);offset=end

    def submit(self,ids,counts,data):
        # Two bounded CPU pages can overlap replay. Backpressure is measured,
        # and only necessary when the storage lane cannot sustain its producer.
        if len(self.pending)>=2:
            started=time.perf_counter();self.pending.popleft().result();self.wait_seconds+=time.perf_counter()-started
        self.pending.append(self.pool.submit(self.write,self.serial,ids,counts,data));self.serial+=1

    def finish(self):
        self.close()
        path=self.root/'complete.json'
        hashes=[h.hexdigest() for h in self.hashes]
        write_json(path,dict(version='v60-order-journal-v1',identity=self.identity,population=self.population,
            columns=list(COLUMNS),pages=self.pages,counts=self.counts.tolist(),candidate_sha256=hashes))
        return [dict(receipt=str(path),candidate_index=i,orders=int(self.counts[i]),sha256=hashes[i]) for i in range(self.population)]

    def close(self):
        if self.closed:return
        self.closed=True
        try:
            for future in self.pending:future.result()
        finally:self.pool.shutdown(wait=True,cancel_futures=True)
