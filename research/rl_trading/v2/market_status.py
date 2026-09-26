"""Consumer contract for an ingestion-owned, point-in-time status sidecar.

This module does not acquire halts or infer them from missing trades. A producer
must certify canonical SIP provenance and coverage before this consumer accepts
the sidecar. Absence of a status update starts as UNKNOWN, never TRADING.
"""
from collections import defaultdict
import json
from pathlib import Path
import numpy as np

from research.rl_trading.v2.io import read, file_hash

UNKNOWN, TRADING, HALTED = 0, 1, 2
VERSION = 'ingestion-market-status-asof-v1'


class StatusSidecar:
    def __init__(self, root, *, day, listings, first_us, seconds):
        self.root = Path(root).resolve()
        cert = read(self.root/'complete.json')
        if (cert.get('version') != VERSION or cert.get('state') != 'complete'
                or cert.get('date') != day
                or cert.get('authority_table') != f'market_sip_compact.events_{day[:4]}'
                or cert.get('producer_owner') != 'canonical_ingestion'
                or not cert.get('source_certificate_hash')
                or cert.get('first_us') != first_us
                or cert.get('end_us') != first_us+(seconds-1)*1000000
                or set(cert.get('listing_ids',[])) != {str(x['listing_id']) for x in listings}
                or cert.get('clock') != 'provider_effective_and_first_available'
                or cert.get('event_count',0) > 200000):
            raise ValueError('Require complete ingestion-owned causal status coverage for this population')
        path = self.root/'events.jsonl'
        if file_hash(path) != cert['events_hash']:
            raise ValueError('Status sidecar hash changed')
        self.events = defaultdict(list)
        count = 0
        seen = set()
        effective_seen = set()
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                row = json.loads(line)
                identity = str(row['listing_id'])
                effective,available = int(row['effective_us']),int(row['available_us'])
                if (identity not in cert['listing_ids'] or effective > available
                        or row['state'] not in (UNKNOWN,TRADING,HALTED)
                        or (identity,available) in seen or (identity,effective) in effective_seen):
                    raise ValueError('Ambiguous identity, clock, or status transition')
                seen.add((identity,available))
                effective_seen.add((identity,effective))
                self.events[identity].append((effective,available,int(row['state'])))
                count += 1
                if count > 200000:
                    raise ValueError('Status sidecar exceeds bounded event admission')
        if count != cert['event_count']:
            raise ValueError('Status sidecar event count mismatch')
        self.first_us,self.seconds = first_us,seconds
        self.certificate = dict(root=str(self.root),complete_hash=file_hash(self.root/'complete.json'),**cert)

    def states(self, listing_id, *, execution=False):
        values = np.zeros(self.seconds,dtype=np.uint8)
        index = 0 if execution else 1
        events = sorted(self.events[str(listing_id)],key=lambda row:row[index])
        if events:
            transitions = np.asarray(events,dtype=np.int64)
            clocks = self.first_us+np.arange(self.seconds,dtype=np.int64)*1000000
            selected = np.searchsorted(transitions[:,index],clocks,side='right')-1
            valid = selected >= 0
            values[valid] = transitions[selected[valid],2]
        return values
