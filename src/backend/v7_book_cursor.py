"""Strategy compatibility projection; all V7 construction remains QMD-owned."""
from datetime import datetime,timezone
from threading import RLock
from uuid import uuid4
from .qmd_gateway_client import qmd_level_book_v7


class V7BookCursor:
    def __init__(self,build_id,ticker,fingerprint=None):
        from .experimental_structure_book import resolve
        self.build=resolve(build_id);self.ticker=ticker;self.lock=RLock();self.cached=None;self.cutoff=None
        self.cursor_id=uuid4().hex
        if self.build['ticker'] not in ('*',ticker) or fingerprint and fingerprint!=self.build['fingerprint']:
            raise ValueError('V7 book identity changed')

    def snapshot(self,cutoff,sequence=None):
        if cutoff.tzinfo is None:raise ValueError('V7 strategy cutoff requires a timezone')
        second=int(cutoff.timestamp())
        with self.lock:
            if self.cutoff!=second:
                value=qmd_level_book_v7(self.ticker,datetime.fromtimestamp(second,timezone.utc),cursor_id=self.cursor_id)
                if value['provenance']['catalog_hash']!=self.build['fingerprint']:
                    raise ValueError('V7 catalog changed during strategy execution')
                self.cached=value;self.cutoff=second
            return self.cached
