"""Strategy compatibility projection; all V7 construction remains QMD-owned."""
from datetime import datetime,timezone
from threading import RLock
from uuid import uuid4
from collections import OrderedDict
from src.market_engine.v7_snapshot_transport import Decoder
from .qmd_gateway_client import qmd_level_book_v7


class V7BookCursor:
    def __init__(self,build_id,ticker,fingerprint=None):
        from .experimental_structure_book import resolve
        self.build=resolve(build_id);self.ticker=ticker;self.lock=RLock();self.cached=None;self.cutoff=None
        self.cursor_id=uuid4().hex
        self.decoder=Decoder()
        self.snapshots=OrderedDict()
        self.prefetch_errors={}
        if self.build['ticker'] not in ('*',ticker) or fingerprint and fingerprint!=self.build['fingerprint']:
            raise ValueError('V7 book identity changed')

    def snapshot(self,cutoff,sequence=None):
        if cutoff.tzinfo is None:raise ValueError('V7 strategy cutoff requires a timezone')
        second=int(cutoff.timestamp())
        with self.lock:
            failure=self.prefetch_errors.pop(second,None)
            if failure is not None:raise failure
            if second not in self.snapshots:
                packet=qmd_level_book_v7(self.ticker,datetime.fromtimestamp(second,timezone.utc),cursor_id=self.cursor_id,
                    delta=True,base_version=self.decoder.version)
                value=self.decoder.decode(packet)
                if value['provenance']['catalog_hash']!=self.build['fingerprint']:
                    raise ValueError('V7 catalog changed during strategy execution')
                if value['as_of']!=second or value['max_input_timestamp']>second:
                    raise ValueError('V7 snapshot crossed the requested causal cutoff')
                self.snapshots[second]=value
                while len(self.snapshots)>32:self.snapshots.popitem(last=False)
            self.snapshots.move_to_end(second)
            self.cached=self.snapshots[second];self.cutoff=second
            return self.cached

    def prefetch(self,cutoff):
        # Fail at the original consumption boundary, not at a later input
        # merely read ahead by a preparation worker.
        try:
            self.snapshot(cutoff)
        except Exception as exc:
            with self.lock:
                self.prefetch_errors[int(cutoff.timestamp())]=exc


class PreparedV7Cursors:
    """Batched QMD transport; strategy reads retain independent exact cutoffs."""
    def __init__(self, stream_id, build, tickers):
        self.stream_id=stream_id
        self.build=build
        self.cursors={ticker:PreparedV7Cursor(self,ticker) for ticker in tickers}

    def fetch(self, requests):
        from .qmd_gateway_client import qmd_history_post_json
        missing={}
        counts={}
        for ticker,at in requests:
            cursor=self.cursors[ticker]
            second=int(at.timestamp())
            if second not in cursor.snapshots and (ticker,second) not in missing and counts.get(ticker,0)<32:
                missing[(ticker,second)]=dict(ticker=ticker,as_of=datetime.fromtimestamp(second,timezone.utc).isoformat(),base_version=cursor.decoder.version)
                counts[ticker]=counts.get(ticker,0)+1
        rows=list(missing.values())
        for start in range(0,len(rows),256):
            batch=rows[start:start+256]
            seen=set()
            for row in batch:
                row['continue_batch']=row['ticker'] in seen
                row['base_version']=self.cursors[row['ticker']].decoder.version
                seen.add(row['ticker'])
            try:
                response=qmd_history_post_json('/level-book-v7/stream',dict(operation='advance',stream_id=self.stream_id,requests=batch),timeout=180)
                if sorted((r['ticker'],r['as_of']) for r in response['rows']) != sorted((r['ticker'],r['as_of']) for r in batch):
                    raise ValueError('Prepared V7 batch response is incomplete or duplicated')
            except Exception as exc:
                for row in batch:
                    self.cursors[row['ticker']].errors[int(datetime.fromisoformat(row['as_of']).timestamp())]=str(exc)
                continue
            for row in response['rows']:
                cursor=self.cursors[row['ticker']]
                second=int(datetime.fromisoformat(row['as_of']).timestamp())
                if 'error' in row:
                    cursor.errors[second]=row['error']
                    continue
                value=cursor.decoder.decode(row['packet'])
                if value['as_of']!=second or value['max_input_timestamp']>second or value['provenance']['catalog_hash']!=self.build['fingerprint']:
                    raise ValueError('Prepared V7 snapshot identity or causal cutoff mismatch')
                cursor.snapshots[second]=value
                while len(cursor.snapshots)>32:cursor.snapshots.popitem(last=False)

    def close(self):
        from .qmd_gateway_client import qmd_history_post_json
        qmd_history_post_json('/level-book-v7/stream',dict(operation='release',stream_id=self.stream_id),timeout=30)


class PreparedV7Cursor:
    def __init__(self,pool,ticker):
        self.pool=pool;self.ticker=ticker;self.build=pool.build
        self.snapshots=OrderedDict();self.decoder=Decoder()
        self.errors={}

    def snapshot(self,cutoff,sequence=None):
        if cutoff.tzinfo is None:raise ValueError('Prepared V7 cutoff requires a timezone')
        second=int(cutoff.timestamp())
        if second in self.errors:raise ValueError(self.errors.pop(second))
        if second not in self.snapshots:self.pool.fetch([(self.ticker,cutoff)])
        if second in self.errors:raise ValueError(self.errors.pop(second))
        return self.snapshots[second]
