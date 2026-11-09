"""Rebuild quote fields only from frozen canonical events, with bounded caching.

Existing sample origins/trades are retained. The shared producer is invoked
with a quote-only projection, avoiding full fine-trade bar materialization.
Only final aggregates cross ClickHouse. No historical flatfile is consulted.
"""

from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

import numpy as np

from .quote_quality_v1 import condition_sql
from .shards import source
from .shards.contract import digest
from .shards.range_sql import event_source
from .shards.sql import clock

NY = ZoneInfo("America/New_York")
QUOTE = np.dtype([("values", "<f4", (12,)), ("valid", "u1", (12,)), ("rebuild", "u1")])


def interval_statement(ticker, bounds, rules, day):
    """Locate conservative source-change intervals, using only 100ms statistics.

    A changed observation can alter its own bucket and, if it is the latest
    actual quote, carry until the next actual quote. Coarsening the next clock
    to 100ms can only select extra rebuilds; it cannot miss a changed output.
    Filtering in ClickHouse returns only affected intervals, not raw events.
    """
    selected = [r for r in bounds if r["source_date"] == day]
    if not selected:
        raise ValueError("Quote audit day is outside frozen source bounds")
    raw = event_source(
        ticker,
        selected,
        int(selected[0]["first_ordinal"]),
        int(selected[-1]["next_ordinal"]),
    )
    tokens = "arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)])"
    return f"""WITH decoded AS (SELECT *,
      toUInt64(price_primary_int)*if(bitAnd(event_meta,2)!=0,1,100) price_int,
      toUInt64(price_secondary_int)*if(bitAnd(event_meta,4)!=0,1,100) secondary_int,
      {tokens} tokens,
      secondary_int>0 AND price_int>=secondary_int AND isFinite(size_primary) AND isFinite(size_secondary)
        AND size_primary>=0 AND size_secondary>=0 old_ok,
      old_ok AND size_primary>0 AND size_secondary>0 AND ({condition_sql(rules)}) new_ok,
      old_ok AND NOT new_ok changed
      FROM ({raw}) WHERE bitAnd(event_meta,1)=0 AND sip_timestamp_us>={clock(day,4)} AND sip_timestamp_us<{clock(day,20)}),
    buckets AS (SELECT toInt64(intDiv(sip_timestamp_us,100000)*100000) b,
      countIf(changed) changed_messages,
      countIf(old_ok AND (size_primary=0 OR size_secondary=0)) zero_size_messages,
      countIf(old_ok AND NOT ({condition_sql(rules)})) condition_messages,
      argMax(toUInt8(changed),tuple(sip_timestamp_us,ordinal)) carry_changed
      FROM decoded GROUP BY b),
    intervals AS (SELECT *,leadInFrame(b,1,toInt64({clock(day,20)})) OVER
      (ORDER BY b ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING) next_b FROM buckets)
    SELECT b begin_us,if(carry_changed,next_b+100000,b+100000) end_us,
      changed_messages,zero_size_messages,condition_messages FROM intervals WHERE changed_messages>0 ORDER BY b FORMAT ArrowStream"""


def statement(ticker, bounds, rules, frame, ends):
    """Return only requested raw quote aggregates, ordered by target bar close.

    ``ends`` is a unique increasing Int64[N] vector, <=4096 per query. The
    session's complete prefix is read so a missing update has the true causal
    book. An ASOF join uses STRICTLY earlier quotes; a quote at the close belongs
    to the next bar. Same-time message order uses the canonical ordinal.
    """
    if not 0 < len(ends) <= 4096 or np.any(np.diff(ends) <= 0):
        raise ValueError("Rebuild targets must be 1..4096 unique ordered clocks")
    first_day = datetime.fromtimestamp(int(ends[0] - 1) / 1e6, NY).date().isoformat()
    last_day = datetime.fromtimestamp(int(ends[-1] - 1) / 1e6, NY).date().isoformat()
    selected = [r for r in bounds if first_day <= r["source_date"] <= last_day]
    if not selected:
        raise ValueError(
            "Requested quote clocks are outside the frozen source inventory"
        )
    raw = event_source(
        ticker,
        selected,
        int(selected[0]["first_ordinal"]),
        int(selected[-1]["next_ordinal"]),
    )
    daily = frame == 86_400_000_000
    bucket = (
        "session_open+19800000000"
        if daily
        else f"intDiv(sip_timestamp_us,{frame})*{frame}"
    )
    target_bucket = "session_open+19800000000" if daily else f"end_us-{frame}"
    regular = "local_second>=34200 AND local_second<57600" if daily else "1"
    aggregates, values, masks = [], [], []
    for side, price, size, book_price, book_size in (
        ("bid", "bid", "bs", 2, 4),
        ("ask", "ask", "az", 3, 5),
    ):
        aggregates += [
            f"argMinIf({price},tuple(sip_timestamp_us,ordinal),ok AND {regular}) {side}_o",
            f"maxIf({price},ok AND {regular}) {side}_h",
            f"minIf({price},ok AND {regular}) {side}_l",
            f"countIf(ok AND {regular}) {side}_n",
        ]
        carried = f"if(book.1,book.{book_price},0.)"
        values += [
            f"if({side}_n>0,{side}_{field},{carried})" for field in ("o", "h", "l")
        ]
        values += [carried, f"if(book.1,book.{book_size},0.)", f"toFloat64({side}_n)"]
        masks += (
            [f"toUInt8({side}_n>0 OR book.1)"] * 3
            + ["toUInt8(book.1)"] * 2
            + ["toUInt8(1)"]
        )
    tokens = "arrayFilter(t->t>0,[toUInt16(condition_token_1),toUInt16(condition_token_2),toUInt16(condition_token_3),toUInt16(condition_token_4),toUInt16(condition_token_5)])"
    # Times and ordinals are filtered on the canonical table's frozen identity.
    # Price decoding exactly matches range_sql, including separate scale bits.
    return f"""WITH decoded AS (SELECT *,
      toUInt64(price_primary_int)*if(bitAnd(event_meta,2)!=0,1,100)/10000. ask,
      toUInt64(price_secondary_int)*if(bitAnd(event_meta,4)!=0,1,100)/10000. bid,
      toFloat64(size_primary) az,toFloat64(size_secondary) bs,
      toDateTime(intDiv(sip_timestamp_us,1000000),'America/New_York') local_time,
      toHour(local_time)*3600+toMinute(local_time)*60+toSecond(local_time) local_second,
      toInt64(toUnixTimestamp(toDateTime(concat(toString(toDate(local_time)),' 04:00:00'),'America/New_York')))*1000000 session_open,
      {tokens} tokens,
      (bid>0 AND ask>=bid AND isFinite(az) AND isFinite(bs) AND az>0 AND bs>0 AND {condition_sql(rules)}) ok
      FROM ({raw}) WHERE bitAnd(event_meta,1)=0 AND sip_timestamp_us>={clock(first_day,4)} AND sip_timestamp_us<{int(ends[-1])}),
    quotes AS (SELECT *,{bucket} bucket FROM decoded WHERE local_second>=14400 AND local_second<72000),
    targets AS (SELECT end_us,
      toInt64(toUnixTimestamp(toDateTime(concat(toString(toDate(toDateTime(intDiv(end_us-1,1000000),'America/New_York'))),' 04:00:00'),'America/New_York')))*1000000 session_open,
      {target_bucket} bucket FROM (SELECT arrayJoin({ends.tolist()}) end_us)),
    aggregates AS (SELECT bucket,session_open,{','.join(aggregates)} FROM quotes GROUP BY bucket,session_open),
    books AS (SELECT session_open,sip_timestamp_us,argMax(tuple(toUInt8(ok),bid,ask,bs,az),ordinal) book
      FROM quotes GROUP BY session_open,sip_timestamp_us ORDER BY session_open,sip_timestamp_us),
    carried AS (SELECT t.*,q.book book FROM (SELECT * FROM targets ORDER BY session_open,end_us) t
      ASOF LEFT JOIN books q ON t.session_open=q.session_open AND t.end_us>q.sip_timestamp_us),
    joined AS (SELECT t.*,a.* EXCEPT(bucket,session_open) FROM carried t LEFT JOIN aggregates a USING(bucket,session_open))
    SELECT end_us,[{','.join(f'toFloat32({v})' for v in values)}] values,
      [{','.join(masks)}] valid FROM joined ORDER BY end_us FORMAT ArrowStream"""


class Rebuilder:
    """Worker-owned transport and disk cache, bounded to <=4096 final rows/query.

    The cache key binds the canonical dictionary/ordinal plan and producer
    revision. It stores RAW quote fields; phase one reapplies each sample's split
    basis after lookup. A cache hit never changes sample or decision identity.
    """

    def __init__(self, cache, root, phase, binding):
        import json

        self.root, self.phase = Path(root), phase
        parent = json.loads(
            (self.root / ("contract.json" if phase == 1 else "plan.json")).read_text()
        )
        self.authority = (
            parent["contract"]["authority"] if phase == 1 else parent["authority"]
        )
        self.actions = (
            self.authority["corporate_actions"] if phase == 1 else parent["actions"]
        )["rows"]
        manifest = self.root / (
            "plan/manifest.json" if phase == 1 else "inventory/plan/manifest.json"
        )
        self.jobs = {j["ticker"]: j for j in json.loads(manifest.read_text())["jobs"]}
        self.bounds = OrderedDict()
        self.client = source.client(memory_gib=1)
        self.client.default_query_params["max_threads"] = 1
        self.db = sqlite3.connect(cache, timeout=60, check_same_thread=False)
        self.db.executescript(
            "PRAGMA journal_mode=WAL; CREATE TABLE IF NOT EXISTS quotes(binding TEXT,ticker TEXT,frame INTEGER,end INTEGER,payload BLOB,PRIMARY KEY(binding,ticker,frame,end)) WITHOUT ROWID; CREATE TABLE IF NOT EXISTS intervals(binding TEXT,ticker TEXT,day TEXT,payload TEXT,PRIMARY KEY(binding,ticker,day)) WITHOUT ROWID;"
        )
        from . import bar_semantics_v1
        from .quote_quality_v1 import POLICY

        self.binding = digest(
            [POLICY, bar_semantics_v1.CONTRACT, self.authority["dictionary_hash"]]
        )

    def close(self):
        """Release both private transports and the cache handle after drain."""
        self.db.close()
        source.close(self.client)

    def get(self, ticker, frame, ends):
        """Return raw QUOTE[N] for arbitrary repeated Int64[N] bar-close clocks."""
        from .shards.balanced_plan import ticker_bounds

        import json
        from .quote_quality_v1 import REJECTED_QUOTE_MODIFIERS

        unique, inverse = np.unique(ends, return_inverse=True)
        result = np.zeros(len(unique), dtype=QUOTE)
        if ticker not in self.bounds:
            inventory = self.root if self.phase == 1 else self.root / "inventory"
            self.bounds[ticker] = ticker_bounds(inventory, self.jobs[ticker])
            if len(self.bounds) > 4:
                self.bounds.popitem(last=False)
        dates = np.array(
            [
                datetime.fromtimestamp(int(t - 1) / 1e6, NY).date().isoformat()
                for t in unique
            ]
        )
        affected = np.zeros(len(unique), dtype=bool)
        for day in np.unique(dates):
            cached = self.db.execute(
                "SELECT payload FROM intervals WHERE binding=? AND ticker=? AND day=?",
                (self.binding, ticker, str(day)),
            ).fetchone()
            if cached:
                intervals = json.loads(cached[0])
            else:
                table = source.arrow(
                    self.client,
                    interval_statement(
                        ticker, self.bounds[ticker], self.authority["rules"], str(day)
                    ),
                    "quote-intervals-" + digest([ticker, str(day)])[:24],
                    allow_empty=True,
                )
                intervals = table.to_dicts()
                with self.db:
                    self.db.execute(
                        "INSERT OR IGNORE INTO intervals VALUES(?,?,?,?)",
                        (self.binding, ticker, str(day), json.dumps(intervals)),
                    )
            positions = np.flatnonzero(dates == day)
            clocks = unique[positions]
            starts = (
                np.full(len(clocks), clock(str(day), 9) + 1800000000, dtype=np.int64)
                if frame == 86_400_000_000
                else clocks - frame
            )
            if intervals:
                begins = np.array([i["begin_us"] for i in intervals], dtype=np.int64)
                stops = np.maximum.accumulate(
                    np.array([i["end_us"] for i in intervals], dtype=np.int64)
                )
                # Conservative half-open interval intersection. At most one
                # searchsorted lookup per stored bar, no per-event Python loop.
                prior = np.searchsorted(begins, clocks, side="left") - 1
                affected[positions] = (prior >= 0) & (
                    stops[np.maximum(prior, 0)] >= starts
                )
        targets = np.flatnonzero(affected)
        active = unique[targets]
        for begin in range(0, len(active), 512):
            batch = active[begin : begin + 512]
            found = {
                int(t): payload
                for t, payload in self.db.execute(
                    f"SELECT end,payload FROM quotes WHERE binding=? AND ticker=? AND frame=? AND end IN ({','.join('?' for _ in batch)})",
                    (self.binding, ticker, int(frame), *map(int, batch)),
                )
            }
            missing = np.array(
                [t for t in batch if int(t) not in found], dtype=np.int64
            )
            if len(missing):
                if ticker not in self.bounds:
                    inventory = (
                        self.root if self.phase == 1 else self.root / "inventory"
                    )
                    self.bounds[ticker] = ticker_bounds(inventory, self.jobs[ticker])
                    if len(self.bounds) > 4:
                        self.bounds.popitem(last=False)
                # Bound source spans as well as returned rows. Fine batches
                # never scan more than a single source session per query.
                dates = np.array(
                    [
                        datetime.fromtimestamp(int(t - 1) / 1e6, NY).date().isoformat()
                        for t in missing
                    ]
                )
                for day in np.unique(dates):
                    clocks = missing[dates == day]
                    table = source.arrow(
                        self.client,
                        statement(
                            ticker,
                            self.bounds[ticker],
                            self.authority["rules"],
                            int(frame),
                            clocks,
                        ),
                        "quote-repair-"
                        + digest([ticker, int(frame), clocks.tolist()])[:24],
                        allow_empty=False,
                    )
                    if table["end_us"].to_list() != clocks.tolist():
                        raise ValueError(
                            "Canonical quote rebuild changed requested clocks"
                        )
                    records = np.zeros(len(table), dtype=QUOTE)
                    records["rebuild"] = 1
                    records["values"] = table["values"].list.to_array(12).to_numpy()
                    records["valid"] = table["valid"].list.to_array(12).to_numpy()
                    with self.db:
                        for t, record in zip(clocks, records):
                            payload = record.tobytes()
                            self.db.execute(
                                "INSERT OR IGNORE INTO quotes VALUES(?,?,?,?,?)",
                                (self.binding, ticker, int(frame), int(t), payload),
                            )
                            found[int(t)] = payload
            for i, t in enumerate(batch):
                result[targets[begin + i]] = np.frombuffer(
                    found[int(t)], dtype=QUOTE, count=1
                )[0]
        return result[inverse]
