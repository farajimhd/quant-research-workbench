"""Verified, time-indexed access to the complete Phase 2 market value table."""
from bisect import bisect_left
from hashlib import sha256
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from research.rl_trading.v1.common import digest
from src.market_engine.level_book_store import read


class _TimeTable:
    def __init__(self, root, certificate, files):
        path = root/certificate['file']
        h = sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):
                h.update(block)
        if h.hexdigest() != certificate['file_hash'] or files.get(path.name) != certificate['file_hash']:
            raise ValueError('Market value tensor file hash mismatch')
        self.parquet = pq.ParquetFile(path)
        if self.parquet.metadata.num_rows != certificate['rows']:
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
        return pl.concat(frames) if frames else None

    def close(self):
        self.parquet.close()


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
        self.expected_rows = 2*len(plan['selected'])
        if tensor['rows'] != self.expected_rows*57601:
            raise ValueError('Market value tensor row count mismatch')
        self.sparse = 'holding' in tensor
        self.holding = _TimeTable(root,tensor['holding'] if self.sparse else tensor,complete['files'])
        self.opening = _TimeTable(root,tensor['opening'],complete['files']) if self.sparse else None
        if self.holding.parquet.metadata.num_rows != tensor['rows']:
            raise ValueError('Market holding grid row count mismatch')
        if self.sparse and (tensor.get('missing_opening') != 'can_open_false'
                or tensor['opening']['rows'] > tensor['rows']):
            raise ValueError('Invalid sparse opening contract')

    def at(self, time_us):
        if type(time_us) is not int:
            raise ValueError('Decision time must be an integer UTC microsecond timestamp')
        result = self.holding.at(time_us)
        if result is None or result.height != self.expected_rows or result.select('listing_index','side').n_unique() != self.expected_rows:
            raise ValueError('Incomplete, duplicate or absent market snapshot')
        if self.sparse:
            openings = self.opening.at(time_us)
            keys = ['time_us','listing_index','side']
            if openings is not None:
                if openings.select(*keys).n_unique() != openings.height or not openings['can_open'].all():
                    raise ValueError('Duplicate or invalid sparse openings')
                result = result.join(openings,on=keys,how='left',validate='1:1',maintain_order='left')
            else:
                # An empty second still has the same opening schema.
                empty = pa.Table.from_batches([],schema=self.opening.parquet.schema_arrow)
                result = result.join(pl.from_arrow(empty),
                    on=keys,how='left',validate='1:1',maintain_order='left')
            result = result.with_columns(pl.col('can_open').fill_null(False),
                pl.col('open_value_available').fill_null(False),
                pl.col('value_available').fill_null(False))
        return result

    def close(self):
        self.holding.close()
        if self.opening is not None:
            self.opening.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
