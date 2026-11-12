"""Exact full-session origin counts from verified terminal ancestor receipts.

This is an offline query cache, never a liquidity estimate or an input feature.
Missing entries fall open to the original canonical query. History stays intact.
"""

from collections import OrderedDict
import json
from pathlib import Path

import polars as pl

from .continuation import semantic_identity
from ..quote_repair_v1 import verify_seal
from ..shards.contract import digest, file_hash

SOURCE_COLUMNS=('ticker','source_date','event_count','first_ordinal','next_ordinal',
                'first_sip_timestamp_us','last_sip_timestamp_us','build_step')


def bucket(ticker):
    return digest(ticker)[:2]


class EligibilityCache:
    def __init__(self, root, plan):
        self.root=Path(root)
        self.manifest=json.loads((self.root/'manifest.json').read_text())
        verify_seal(self.manifest,'hash')
        m=self.manifest
        if m['version']!='sealed-origin-count-cache-v1' or m['semantic_identity']!=semantic_identity(plan):
            raise ValueError('Eligibility cache changes source/producer authority')
        parent=m['parent']
        allowed={(str(Path(p['root']).resolve()),p['binding']) for p in plan.get('continuation',{}).get('parents',[])}
        if (str(Path(parent['root']).resolve()),parent['binding']) not in allowed:
            raise ValueError('Eligibility cache is not from a frozen ancestor')
        self.memory=OrderedDict()

    def get(self,ticker,row):
        key=bucket(ticker)
        if key not in self.memory:
            part=self.manifest['parts'].get(key)
            if part is None:return None
            path=self.root/part['name']
            if path.name!=part['name'] or path.stat().st_size!=part['bytes'] or file_hash(path)!=part['sha256']:
                raise ValueError('Eligibility cache part changed')
            frame=pl.read_parquet(path)
            if len(frame)!=part['rows']:raise ValueError('Eligibility cache row count differs')
            entries={}
            for entry in frame.to_dicts():
                identity=(entry['ticker'],entry['source_date'])
                if identity in entries or not 0<=entry['origin_count']<=57600:
                    raise ValueError('Invalid or duplicate eligibility count')
                entries[identity]=entry
            self.memory[key]=entries
            while len(self.memory)>2:self.memory.popitem(last=False)
        self.memory.move_to_end(key)
        entry=self.memory[key].get((ticker,row['source_date']))
        if entry is None:return None
        if set(row)!=set(SOURCE_COLUMNS) or any(entry[k]!=row[k] for k in SOURCE_COLUMNS):
            raise ValueError('Eligibility cache frozen source row differs')
        return int(entry['origin_count'])
