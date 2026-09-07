"""Incremental compact presentation index over the authoritative run journal."""
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
import json


class ReplayActivityIndex:
    def __init__(self, journal, run_id):
        self.journal, self.run_id = journal, run_id
        self.lock = RLock()
        self.queries = OrderedDict()

    def payload(self, **options):
        from src.backend.trading_runtime_service import strategy_activity_payload
        with self.lock:
            filters = {key: str(options.get(key) or '').strip() for key in ('strategy_id', 'ticker', 'event_type')}
            filters['ticker'] = filters['ticker'].upper()
            filters['consequential_only'] = bool(options.get('consequential_only'))
            key = tuple(filters.items())
            index = self.queries.setdefault(key, dict(sequence=0, items=[], bytes=0))
            self.queries.move_to_end(key)
            while len(self.queries) > 8:
                self.queries.popitem(last=False)
            fence = self.journal.latest_sequence(self.run_id)
            if fence > index['sequence']:
                records = self.journal.strategy_activity_records(run_id=self.run_id,
                    after_sequence=index['sequence'], through_sequence=fence, limit=50_001, **filters)
                if len(records) + len(index['items']) > 50_000:
                    # Large histories retain the authoritative paged SQL path.
                    # No records are dropped or falsely marked complete.
                    self.queries.pop(key, None)
                    return strategy_activity_payload(journal=self.journal, run_id=self.run_id, **options)
                for record in records:
                    payload = strategy_activity_payload(journal=self.journal, run_id=self.run_id,
                        include_decision_evidence=False, _records=[record])
                    row = payload['rows'][0] if payload['rows'] else None
                    index['items'].append((record.event_time, record.recorded_at, record.sequence, row))
                    index['bytes'] += len(json.dumps(row, separators=(',', ':')))
                index['items'].sort(key=lambda item: item[:3], reverse=True)
                index['sequence'] = fence
            cutoff = options.get('as_of') or datetime.max.replace(tzinfo=timezone.utc)
            items = [item for item in index['items'] if item[0] <= cutoff]
            offset = max(0, int(options.get('offset', 0)))
            limit = max(1, min(int(options.get('limit', 500)), 50_000))
            selected = items[offset:offset+limit]
            result = strategy_activity_payload(journal=self.journal, run_id=self.run_id,
                as_of=options.get('as_of'), include_decision_evidence=False, _records=[])
            seen, rows = set(), []
            for _, _, _, row in selected:
                if row is None:
                    continue
                if row['event_type'] == 'decision' and row['entity_id']:
                    if row['entity_id'] in seen:
                        continue
                    seen.add(row['entity_id'])
                rows.append(row)
            result.update(rows=deepcopy(rows), complete=len(items) <= offset+limit,
                          next_offset=None if len(items) <= offset+limit else offset+len(selected))
            for name, field in [('strategies', 'strategy_id'), ('runs', 'run_id'), ('tickers', 'ticker')]:
                result['catalog'][name] = sorted({row[field] for row in rows if row[field]})
            while self.queries and sum(i['bytes'] for i in self.queries.values()) > 32*1024*1024:
                self.queries.popitem(last=False)
            return result
