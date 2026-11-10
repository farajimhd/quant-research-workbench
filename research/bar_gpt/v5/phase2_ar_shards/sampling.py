"""Warm once at a seeded UTC origin, append unique bars, emit shared blocks."""

from datetime import datetime, timezone

import numpy as np
import time

from .contract import BAR, BLOCK, Compiler, Geometry, ORIGIN
from ..shards.contract import seed_for


def utc_month(month):
    year, number = map(int, month.split("-"))
    first = datetime(year, number, 1, tzinfo=timezone.utc)
    last = (
        datetime(year + 1, 1, 1, tzinfo=timezone.utc)
        if number == 12
        else datetime(year, number + 1, 1, tzinfo=timezone.utc)
    )
    return int(first.timestamp() * 1_000_000), int(last.timestamp() * 1_000_000)


def origin_clock(unit, seed):
    low, high = int(unit["first_us"]), int(unit["last_us"]) + 1
    if low >= high:
        raise ValueError("Sampled month has no UTC source interval")
    # Frozen UTC clock sampling, not an unreported claim of uniform bar sampling.
    return low + seed_for(seed, unit["ticker"], unit["month"], "first-origin") % (
        high - low
    )


def rebase_cache(rows, actions, basis_us):
    """Use phase-one adjustment math once from original-basis real bars.

    The shared math operates on named arrays, independently of window width.
    Keep raw cache separate so repeated session advances never compound Float32
    rounding or change an earlier session snapshot.
    """
    from ..shards.storage import adjust_splits

    result = rows.copy()
    if len(result):
        fields = dict(values=result["values"][None], end_us=result["end_us"][None])
        adjust_splits(fields, actions, origin_us=int(basis_us))
    return result


def compile_sessions(
    reader,
    job,
    unit,
    geometry=Geometry(),
    *,
    gate,
    actions,
    chunk_events=0,
    seed=20261109,
):
    """One CPU owner warms once, then emits independent single-session snapshots."""
    from ..shards.sql import clock
    from ..shards.contract import digest

    rows = [r for _, month in reader.months(job) for r in month]
    chosen = origin_clock(unit, seed)
    selected = [
        r
        for r in rows
        if r["source_date"].startswith(unit["month"])
        and int(r["last_sip_timestamp_us"]) >= chosen
    ]
    pages = iter(reader.pages(selected, chunk_events))
    first = next(pages, None)
    if first is None:
        raise ValueError("Frozen sampled month has no session")
    first_open = clock(first[0]["source_date"], 4)
    initial, timing = reader.fetch(
        unit["ticker"], first, gate, chunk_events, frames=(0, 1, 2)
    )
    counts = [
        int(
            (
                (initial[0]["end_us"] > clock(r["source_date"], 4))
                & (initial[0]["end_us"] <= clock(r["source_date"], 20))
            ).sum()
        )
        for r in first
    ]
    if len(first) == len(selected) and all(n < BLOCK for n in counts):
        # Offline session eligibility, never an input feature. A month with no
        # eligible session needs neither coarse reconstruction nor64-day warmup.
        # Unknown history is explicit NULL, not invented zero-bar evidence.
        for i, (row, n) in enumerate(zip(first, counts)):
            day = row["source_date"]
            session = dict(
                unit,
                month=day,
                session=day,
                parent_month=unit["month"],
                split=int(day[:4] == "2026"),
            )
            deferred = dict(
                blocks=np.empty((0, BLOCK), dtype=ORIGIN),
                stats=dict(
                    source_origins=n,
                    session_short_deferred=n,
                    warmup_deferred=None,
                    pending_origins=0,
                ),
                sampling=dict(
                    reason="session_has_fewer_than4096_origins",
                    history_queried=False,
                    warmup_bars=None,
                    first_eligible_origin_us=None,
                    session_origins=n,
                ),
            )
            yield session, deferred, (
                dict(timing) if i == 0 else dict(queries=0, query_seconds=0.0)
            )
        return
    coarse, elapsed = reader.fetch(
        unit["ticker"], first, gate, chunk_events, frames=(3, 4)
    )
    initial[3:] = coarse[3:]
    for field in (
        "queries",
        "query_seconds",
        "admission_wait_seconds",
        "fetch_seconds",
        "resource_page_splits",
    ):
        timing[field] = timing.get(field, 0) + elapsed.get(field, 0)
    cache = [
        a[a["end_us"] <= first_open][-h:].copy()
        for a, h in zip(initial, geometry.history)
    ]
    prior = [r for r in rows if r["source_date"] < first[0]["source_date"]]
    for page in reversed(list(reader.pages(prior, chunk_events))):
        needed = tuple(i for i, h in enumerate(geometry.history) if len(cache[i]) < h)
        if not needed:
            break
        older, elapsed = reader.fetch(
            unit["ticker"], page, gate, chunk_events, frames=needed
        )
        timing["query_seconds"] += elapsed["query_seconds"]
        for field in ("admission_wait_seconds", "fetch_seconds", "resource_page_splits"):
            timing[field] = timing.get(field, 0.0) + elapsed.get(field, 0.0)
        timing["queries"] += elapsed["queries"]
        for i in needed:
            cache[i] = np.concatenate(
                (older[i][older[i]["end_us"] <= first_open], cache[i])
            )[-geometry.history[i] :]

    def consume(page, arrays, page_timing):
        nonlocal cache
        for ordinal, row in enumerate(page):
            compiling = time.monotonic()
            day = row["source_date"]
            opened, closed = clock(day, 4), clock(day, 20)
            current = [
                a[(a["end_us"] > opened) & (a["end_us"] <= closed)] for a in arrays
            ]
            count = len(current[0])
            start = (
                seed_for(seed, unit["ticker"], day, "origin") % (count - BLOCK + 1)
                if count >= BLOCK
                else 0
            )
            origin = int(current[0]["end_us"][start]) if count else opened
            # Prefix can include already-completed bars from the current session.
            # Every stored prefix is on this session's one immutable share basis.
            adjusted = [rebase_cache(a, actions, opened) for a in cache]
            prefix = [
                np.concatenate((old, new[new["end_us"] < origin]))[-h:]
                for old, new, h in zip(adjusted, current, geometry.history)
            ]
            compiler = Compiler(geometry)
            compiler.bootstrap(prefix)
            core = [a[a["end_us"] >= origin] for a in current]
            compiled = compiler.compile(core, int(unit["liquid"]))
            split = int(int(day[:4]) == 2026)
            if any(s != split for s in compiled["splits"]):
                raise ValueError("Session origin year split changed")
            compiled["independent_unit"] = True
            compiled["price_basis"] = dict(
                session_date=day,
                basis_us=opened,
                actions_hash=digest(actions),
                policy="phase-one executed-date04NY common basis; applied exactly once",
            )
            eligible = (
                compiled["blocks"]["end_us"][0, 0].item()
                if len(compiled["blocks"])
                else (
                    compiler.pending["end_us"][0].item()
                    if len(compiler.pending)
                    else None
                )
            )
            compiled["sampling"] = dict(
                month_clock_us=chosen,
                first_eligible_origin_us=eligible,
                warmup_bars=[len(a) for a in prefix],
                session_origins=count,
                unselected_prefix_origins=start,
                origin_distribution="seeded feasible observed1s offset within one session",
                reason=(
                    "session_has_fewer_than4096_origins"
                    if count < BLOCK
                    else (
                        "insufficient_history"
                        if not len(compiled["blocks"])
                        else "complete_blocks"
                    )
                ),
            )
            # Freeze compiled arrays before advancing the separate RAW cache.
            # Even skipped/short sessions contribute real context to the next day.
            cache = [
                np.concatenate((old, new))[-h:].copy()
                for old, new, h in zip(cache, current, geometry.history)
            ]
            session_unit = dict(
                unit, month=day, session=day, parent_month=unit["month"], split=split
            )
            costs = (
                dict(page_timing)
                if ordinal == 0
                else dict(
                    query_seconds=0.0,
                    queries=0,
                    admission_wait_seconds=0.0,
                    fetch_seconds=0.0,
                )
            )
            costs["compile_seconds"] = time.monotonic() - compiling
            yield session_unit, compiled, costs

    yield from consume(first, initial, timing)
    for page in pages:
        arrays, elapsed = reader.fetch(unit["ticker"], page, gate, chunk_events)
        yield from consume(page, arrays, elapsed)
