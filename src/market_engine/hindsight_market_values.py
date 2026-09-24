"""Verified, time-indexed access to the complete Phase 2 market value table."""
from bisect import bisect_left
from hashlib import sha256
from pathlib import Path

import polars as pl
import pyarrow.compute as pc
import pyarrow.parquet as pq

from src.market_engine.hindsight_phase1 import digest
from src.market_engine.level_book_store import read


class MarketValues:
    def __init__(self, root):
        root = Path(root)
        plan = read(root/'plan.json')
        complete = read(root/'complete.json')
        if plan.get('plan_hash') != digest({k:v for k,v in plan.items() if k != 'plan_hash'}):
            raise ValueError('Market value plan hash mismatch')
        if complete.get('plan_hash') != plan['plan_hash']:
            raise ValueError('Market value completion mismatch')
        tensor = complete['tensor']
        if (tensor['axis_order'] != ['macd_resolution_seconds','time_us','listing_index','side']
            or tensor['physical_order'] != ['time_us','listing_index','side']
            or tensor['listing_count'] != len(plan['selected']) or tensor['side_count'] != 2
            or tensor['time_count'] != 57601):
            raise ValueError('Market value tensor shape mismatch')
        path = root/tensor['file']
        h = sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                h.update(block)
        if h.hexdigest() != tensor['file_hash'] or complete['files'].get(tensor['file']) != tensor['file_hash']:
            raise ValueError('Market value tensor file hash mismatch')
        self.parquet = pq.ParquetFile(path)
        self.expected_rows = 2*len(plan['selected'])
        if self.parquet.metadata.num_rows != tensor['rows'] or tensor['rows'] != self.expected_rows*57601:
            raise ValueError('Market value tensor row count mismatch')
        column = self.parquet.schema_arrow.get_field_index('time_us')
        if column < 0:
            raise ValueError('Market value tensor time index missing')
        self.ranges = []
        for i in range(self.parquet.num_row_groups):
            stats = self.parquet.metadata.row_group(i).column(column).statistics
            if stats is None or not stats.has_min_max:
                raise ValueError('Market value tensor row-group time statistics missing')
            self.ranges.append((int(stats.min),int(stats.max)))
        if any(a[0] > a[1] for a in self.ranges) or any(
                self.ranges[i][0] < self.ranges[i-1][0] or
                self.ranges[i][1] < self.ranges[i-1][1] for i in range(1,len(self.ranges))):
            raise ValueError('Market value tensor row groups are not time ordered')
        self.maxima = [r[1] for r in self.ranges]
        self.cached_index = None
        self.cached_group = None

    def at(self, time_us):
        if type(time_us) is not int:
            raise ValueError('Decision time must be an integer UTC microsecond timestamp')
        index = bisect_left(self.maxima,time_us)
        frames = []
        while index < len(self.ranges) and self.ranges[index][0] <= time_us:
            if self.ranges[index][1] >= time_us:
                if self.cached_index != index:
                    self.cached_group = self.parquet.read_row_group(index)
                    self.cached_index = index
                group = self.cached_group
                frames.append(pl.from_arrow(group.filter(pc.equal(group['time_us'],time_us))))
            index += 1
        if not frames:
            raise ValueError('Decision time absent from market tensor')
        result = pl.concat(frames)
        if result.height != self.expected_rows or result.select('listing_index','side').n_unique() != self.expected_rows:
            raise ValueError('Incomplete or duplicate market snapshot')
        return result

    def close(self):
        self.parquet.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
