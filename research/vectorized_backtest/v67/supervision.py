"""Hash-bound, CPU-only 1b targets. Never exposed to inference or execution decisions."""
import json
from pathlib import Path
import numpy as np
import polars as pl
from .distributed_assets import sha

VERSION = 'saved-1b-entry-profit-and-request-penalties-v1'
APPROVED_SHA = 'f3357d530cdf6ba8b53d4e559c6b219dc3ebeda172a728cead5c5296c3749543'
SETTINGS = dict(version=VERSION, entry_penalty=5., exit_penalty=5.,
                late_notional_second=0.00001, timing='decision-candle-close',
                actions='saved-1b-only', missing='masked-no-positive-credit',
                profit='episode-net-aligned-buy-notional-fraction',
                requests='rising-edge-per-action-and-pair', protective_exits='exempt')


def contract(value):
    if not value or value.get('settings') != SETTINGS or value.get('sha256') != APPROVED_SHA:
        raise ValueError('Approved 1b objective binding required')
    path = Path(value['manifest'])
    if sha(path) != value['sha256']:
        raise ValueError('Approved label dataset changed')
    return path, json.loads(path.read_bytes())


def load(item, value, manifest):
    path, dataset = contract(value)
    role = 'validation' if item.receipt['validation_opened'] else 'training'
    if dataset['binding']['source_manifests'][role] != sha(manifest):
        raise ValueError('Labels do not belong to this market manifest')
    day = item.root.name
    records = [r for r in dataset['days'] if r['day'] == day and r['role'] == role]
    if len(records) != 1:
        raise ValueError('Unique label session required')
    root = path.parent/day; certificate = root/'complete.json'
    if sha(certificate) != records[0]['certificate']:
        raise ValueError('Label session certificate changed')
    proof = json.loads(certificate.read_bytes())
    if proof['binding'] != dataset['binding']:
        raise ValueError('Label producer binding changed')
    listing = {r['ticker']: r for r in proof['listings']}
    if set(listing) != set(item.tickers):
        raise ValueError('Label ticker universe differs')
    shape = (item.clocks, len(item.tickers))
    action = np.zeros(shape, np.int8)  # 0 missing, 1 WAIT, 2 ENTRY, 3 HOLD, 4 EXIT
    pair = np.full(shape, -1, np.int32)
    deadline = np.full(shape, -1, np.int32)
    origin = int(item.arrays['clocks'][0])*1000000
    files = [path, certificate]; rows = 0
    for col, ticker in enumerate(item.tickers):
        record = listing[ticker]
        if not record['valid_rows']:
            continue
        file = root/record['file']; files.append(file)
        if sha(file) != proof['files'][file.name]['sha256']:
            raise ValueError('Label shard changed: '+str(file))
        frame = pl.read_parquet(file, columns=['time_us','listing_id','pair_id','action','episode_selected'])
        if frame.height != record['valid_rows'] or frame['listing_id'].unique().to_list() != [ticker]:
            raise ValueError('Label row identity differs')
        times = frame['time_us'].to_numpy()
        ix = (times-origin)//1000000
        if np.any((times-origin)%1000000) or np.any(np.diff(ix)<=0) or np.any((ix<0)|(ix>=item.clocks)):
            raise ValueError('Labels must be unique ordered decision-close seconds')
        selected = frame['episode_selected'].fill_null(False).to_numpy()
        codes = frame['action'].replace_strict({'WAIT':1,'ENTRY':2,'HOLD':3,'EXIT':4},return_dtype=pl.Int8).to_numpy()
        if np.any((~selected)&(codes!=1)):
            raise ValueError('Rejected 1b pair leaked into supervision')
        ids = frame['pair_id'].fill_null(-1).to_numpy()
        if np.any(ids>np.iinfo(np.int32).max):
            raise ValueError('Pair identifier overflow')
        ids = np.where(selected,ids,-1).astype(np.int32)
        action[ix,col] = codes; pair[ix,col] = ids
        # Exit windows are the actual saved 1b actions, never reconstructed 1a gains.
        ends = frame.with_columns(pl.Series('_clock',ix)).filter(pl.col('action')=='EXIT').group_by('pair_id').agg(pl.col('_clock').max())
        mapped = pl.DataFrame({'pair_id':ids}).join(ends,on='pair_id',how='left',validate='m:1',maintain_order='left')
        deadline[ix,col] = mapped['_clock'].fill_null(-1).to_numpy().astype(np.int32)
        rows += frame.height
    if rows != proof['valid_rows']:
        raise ValueError('Label census differs')
    for array in (action,pair,deadline): array.flags.writeable=False
    item.supervision = (action,pair,deadline)
    item.supervision_bytes = sum(a.nbytes for a in item.supervision)
    item.identity['supervision'] = dict(value, certificate=records[0]['certificate'])
    item.file_stats.update({str(p):(p.stat().st_size,p.stat().st_mtime_ns) for p in files})
    item.quote_coverage['supervision'] = dict(rows=rows,missing=int(action.size-rows),host_bytes=item.supervision_bytes)
