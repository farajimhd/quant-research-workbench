"""Bounded read-ahead for completed contextual Signal Stream cold audits.

Only transport is batched. The existing cursor, occurrence, scalar and chain
verifiers consume exactly the same normalized rows in their original order.
No market inputs, state transitions, journal facts or rows are manufactured.
"""
from types import MappingProxyType

from .signal_source_clickhouse_storage import ClickHouseSignalSourceReadStorage
from .signal_stream_typed_cursor import COMMIT, STATE_DELTA, OCCURRENCE_REF, ADMISSION_DELTA
from .signal_execution_context import SignalExecutionContext
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix


class CompletedSignalCursorReadAhead:
    """One owned synchronous reader; at most four bounded family pages retained."""
    def __init__(self, client, catalogs, *, source_context, prefix,
                 batch_span=64, maximum_family_rows=65536):
        if (type(source_context) is not SignalExecutionContext
                or type(prefix) is not V4CommittedPrefix or prefix.status != self._required_status
                or prefix.run_id != source_context.run_id
                or source_context.mode != 'backtest'
                or type(batch_span) is not int or not 1 <= batch_span <= 256
                or type(maximum_family_rows) is not int or not 1 <= maximum_family_rows <= 65536):
            raise ValueError('Signal read-ahead requires bounded completed native authority')
        if client.execute("SELECT getSetting('readonly')").strip() != '1':
            raise ValueError('Completed Signal Stream audit requires SELECT-only credentials')
        self._base = ClickHouseSignalSourceReadStorage(client, catalogs,
            source_context=source_context, max_family_rows=maximum_family_rows)
        self._context = source_context
        self._span, self._maximum = batch_span, maximum_family_rows
        self._pages = {}
        self._family_spans = {}
        self._listed = None
        self._queries = self._capacity_retries = self._evictions = 0
        self._tables = frozenset(t.name for t in (COMMIT, STATE_DELTA, OCCURRENCE_REF, ADMISSION_DELTA))

    def __getattr__(self, name):
        # Existing occurrence authority still uses the existing native reader.
        return getattr(self._base, name)

    def list_cursor_commits(self, *, session_key):
        if session_key != self._context.session_key:
            raise ValueError('Completed source session differs')
        rows = self._base.list_cursor_commits(session_key=session_key)
        # ClickHouse does not promise response order without ORDER BY. Compare
        # the complete contiguous inventory in the native verifier's ordering.
        rows.sort(key=lambda row: row['batch_sequence'])
        if [row['batch_sequence'] for row in rows] != list(range(1, len(rows)+1)):
            raise ValueError('Completed source commit inventory is duplicate or gapped')
        saved = tuple(MappingProxyType(dict(row)) for row in rows)
        if self._listed is not None and saved != self._listed:
            raise ValueError('Completed source commit inventory changed')
        self._listed = saved
        return rows

    def verify_unchanged(self):
        if self._listed is None:
            raise ValueError('Completed source inventory was not verified')
        self.list_cursor_commits(session_key=self._context.session_key)

    def read_cursor_rows(self, table_name, *, session_key, batch_sequence):
        if (table_name not in self._tables or session_key != self._context.session_key
                or type(batch_sequence) is not int or batch_sequence < 1):
            raise ValueError('Completed source cursor scope differs')
        page = self._pages.get(table_name)
        if page is None or not page[0] <= batch_sequence <= page[1]:
            first = batch_sequence
            stop = first + self._family_spans.get(table_name, self._span) - 1
            while True:
                predicate = (f"session_key=toDate('{self._context.session_key}') "
                    f"AND batch_sequence>={first} AND batch_sequence<={stop}")
                try:
                    self._queries += 1
                    rows = self._base._rows(table_name, predicate, self._maximum)
                    break
                except RuntimeError as error:
                    # The shared reader exposes this exact bounded transport
                    # overflow. Never retry an integrity failure or omit rows.
                    if str(error) != 'Contextual family exceeds its explicit read bound' or first == stop:
                        raise
                    self._capacity_retries += 1
                    stop = first + (stop - first) // 2
            grouped = {}
            for row in rows:
                sequence = row['batch_sequence']
                if (row['session_key'] != session_key or type(sequence) is not int
                        or not first <= sequence <= stop):
                    raise ValueError('Prefetched source row escaped its exact session/cursor scope')
                grouped.setdefault(sequence, []).append(MappingProxyType(dict(row)))
            if table_name in self._pages:
                self._evictions += 1
            self._pages[table_name] = (first, stop, grouped)
            self._family_spans[table_name] = stop - first + 1
            page = self._pages[table_name]
        return tuple(dict(row) for row in page[2].get(batch_sequence, ()))

    def read_ahead_counts(self):
        return dict(family_packet_queries=self._queries, capacity_retries=self._capacity_retries,
            family_page_evictions=self._evictions, retained_families=len(self._pages),
            retained_rows=sum(len(rows) for _, _, grouped in self._pages.values() for rows in grouped.values()),
            maximum_retained_rows=4*self._maximum, batch_span=self._span)
    _required_status = 'completed'



class RunningResumeSignalCursorReadAhead(CompletedSignalCursorReadAhead):
    """Same bounded transport, pinned to an owned running resume head."""
    _required_status = 'running'

    def __init__(self, client, catalogs, *, source_context, prefix,
                 source_keeper, owner_id, epoch, batch_span=64, maximum_family_rows=65536):
        from .signal_context_transport import ContextSignalSessionHeadKeeper
        if (type(source_keeper) is not ContextSignalSessionHeadKeeper
                or source_keeper.source_context != source_context
                or type(owner_id) is not str or not owner_id or type(epoch) is not int or epoch < 2):
            raise ValueError('Running source transport requires its recovered contextual owner')
        super().__init__(client,catalogs,source_context=source_context,prefix=prefix,
            batch_span=batch_span,maximum_family_rows=maximum_family_rows)
        self._prefix=prefix;self._client=client
        from .backtest_market_data import market_day_boundary
        day,separator,boundary=prefix.source_cursor.rpartition(':')
        if not separator or day!=source_context.session_key or not boundary.isdigit():
            raise ValueError('Running source transport lacks its exact journal clock')
        self.anchor_cutoff_at=market_day_boundary(day,int(boundary))
        self._source_keeper,self._owner_id,self._epoch=source_keeper,owner_id,epoch
        self._head=source_keeper.read_head(source_context.session_key)
        self._verify_owner_and_prefix()

    def _verify_owner_and_prefix(self):
        from src.trading_runtime.arte_journal_writer import _rows,_literal
        prefix=self._prefix
        rows=_rows(self._client,'SELECT last_sequence,batch_id,status,source_cursor FROM arte.trading_commit_v4 '
            f'WHERE run_id={_literal(prefix.run_id)} ORDER BY last_sequence DESC LIMIT 2 FORMAT JSONEachRow')
        if (not rows or rows[0]['last_sequence']!=prefix.last_sequence
                or str(rows[0]['batch_id'])!=prefix.last_batch_id or rows[0]['status']!='running'
                or rows[0]['source_cursor']!=prefix.source_cursor
                or len(rows)>1 and rows[1]['last_sequence']==rows[0]['last_sequence']
                or not self._source_keeper.is_current(self._context.session_key,owner_id=self._owner_id,epoch=self._epoch)
                or self._source_keeper.read_head(self._context.session_key)!=self._head):
            raise ValueError('Running source owner, durable head or journal prefix changed')

    def list_cursor_commits(self, *, session_key):
        self._verify_owner_and_prefix()
        rows=super().list_cursor_commits(session_key=session_key)
        if (len(rows)!=self._head.batch_sequence
                or (rows[-1]['content_hash'] if rows else '0'*64)!=self._head.cursor_commit_hash):
            raise ValueError('Running source inventory differs from actual attested head')
        return rows

    def verify_unchanged(self):
        super().verify_unchanged()
        self._verify_owner_and_prefix()
