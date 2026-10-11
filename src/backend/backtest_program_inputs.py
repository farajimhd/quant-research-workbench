"""Bounded QMD input preparation for the app's shared program actor.

Network packets are transport bounds, not rule evaluation blocks. All packets
of an explicit block are joined before evaluating its market-only Polars DAGs.
No history calculation, broker projection or alternative backtest is owned here.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from types import MappingProxyType
from typing import Mapping

import polars as pl

from .qmd_columnar_history import CertifiedHistoryPin
from .qmd_columnar_history_transport import read_history_window_async, window_request, _owned_worker
from src.trading_runtime.columnar_rule_program import evaluate_rules
from src.trading_runtime.columnar_strategy_release import ProgramRelease


@dataclass(frozen=True)
class PreparedProgramBlock:
    release_hash: str
    evaluation_block: str
    first_ordinal: int
    stop_ordinal: int
    history_hashes: Mapping[str, str]
    market: pl.DataFrame
    masks: pl.DataFrame
    _offsets: Mapping[str, int] = field(init=False, repr=False)

    def __post_init__(self):
        width = self.stop_ordinal - self.first_ordinal
        keys = ("listing_id", "evaluation_block", "observed_ordinal", "boundary_at")
        if (width < 1 or self.market.height != len(self.history_hashes) * width
                or self.masks.height != self.market.height
                or not self.market.select(keys).equals(self.masks.select(keys))):
            raise ValueError("Prepared program block has inconsistent market/mask coverage")
        groups = self.market.group_by("listing_id", maintain_order=True).len()
        listings = groups["listing_id"].to_list()
        if listings != sorted(self.history_hashes) or groups.filter(pl.col("len") != width).height:
            raise ValueError("Prepared program block has inconsistent listing intervals")
        object.__setattr__(self, "_offsets", MappingProxyType({listing: index * width
            for index, listing in enumerate(listings)}))

    def row_for(self, listing_id: str, ordinal: int):
        """O(1) completed-row lookup; never rescan a full block at each clock."""
        if type(ordinal) is not int or not self.first_ordinal <= ordinal < self.stop_ordinal:
            raise ValueError("Program row is outside its prepared evaluation block")
        index = self._offsets[listing_id] + ordinal - self.first_ordinal
        market, masks = self.market.row(index, named=True), self.masks.row(index, named=True)
        if any(row["listing_id"] != listing_id or row["observed_ordinal"] != ordinal
                or row["evaluation_block"] != self.evaluation_block for row in (market, masks)):
            raise ValueError("Prepared program row lost its exact clock/listing identity")
        return market, masks

    def ordinal_for(self, listing_id: str, at: datetime) -> int:
        """Locate an exact certified dense 1s clock, including cold readback."""
        first, _ = self.row_for(listing_id, self.first_ordinal)
        elapsed = at - first["boundary_at"]
        if elapsed < timedelta(0) or elapsed % timedelta(seconds=1):
            raise ValueError("Program completion is outside its exact dense clock")
        ordinal = self.first_ordinal + elapsed // timedelta(seconds=1)
        row, _ = self.row_for(listing_id, ordinal)
        if row["boundary_at"] != at:
            raise ValueError("Program completion differs from its prepared clock")
        return ordinal

    def boundary_for(self, release, population, *, listing_id, ticker, ordinal):
        from src.trading_runtime.columnar_program_coordinator import ProgramBoundary
        if self.release_hash != release.header["content_hash"]:
            raise ValueError("Program boundary release differs from prepared inputs")
        row, mask = self.row_for(listing_id, ordinal)
        policy = release.header
        stop_field = f"history.swing_low.{policy['swing_left']}.{policy['swing_right']}"
        if policy["swing_left"]:
            if stop_field not in row or stop_field + "__valid" not in row:
                raise ValueError("Program dispatch lacks its certified initial-stop field")
            stop = row[stop_field] if row[stop_field + "__valid"] else None
        else:
            stop = row["reference_close"] * (1 - policy["stop_fraction"])
        return ProgramBoundary(row["boundary_at"], row["available_at"], listing_id, ticker,
            row["reference_close"], population.is_ranked(listing_id, row["boundary_at"]),
            {stage: mask[stage] for stage in release.programs},
            {stage: mask[stage + "__valid"] for stage in release.programs}, stop)

    def completion_identity(self, *, run_id, assignment_id, source_hash,
            listing_id, ordinal, terminal):
        from src.trading_runtime.journal_contract import canonical_json
        if type(terminal) is not bool:
            raise ValueError("Program completion terminal flag must be typed")
        self.row_for(listing_id, ordinal)
        return sha256(canonical_json(dict(schema_version=1, run_id=run_id,
            assignment_id=assignment_id, source_commit_hash=source_hash,
            release_hash=self.release_hash, history_hash=self.history_hashes[listing_id],
            evaluation_block=self.evaluation_block, observed_ordinal=ordinal,
            terminal=terminal)).encode()).hexdigest()


async def prepare_program_block(release: ProgramRelease, pins: Mapping[str, CertifiedHistoryPin], *,
        first_ordinal: int, stop_ordinal: int, evaluation_block: str, as_of: datetime,
        concurrency: int, history_post=None) -> PreparedProgramBlock:
    """Read certified columns with a fixed worker pool and one explicit origin.

    The caller supplies the optimizer's complete evaluation block including its
    warm-up. Trimming or starting a block at an HTTP packet boundary is forbidden.
    The resulting masks are selected sequentially using actual filled state.
    """
    if (type(release) is not ProgramRelease or not pins
            or type(first_ordinal) is not int or type(stop_ordinal) is not int
            or not 0 <= first_ordinal < stop_ordinal
            or len(pins) * (stop_ordinal - first_ordinal) > 262144
            or type(concurrency) is not int or not 1 <= concurrency <= 32
            or type(evaluation_block) is not str or not 1 <= len(evaluation_block) <= 128):
        raise ValueError("Program input block exceeds its explicit preparation bounds")
    owned = dict(pins)
    jobs = []
    session = None
    for listing, pin in sorted(owned.items()):
        if type(pin) is not CertifiedHistoryPin or listing != pin.scope.listing_id:
            raise ValueError("Program input listing differs from its cold history pin")
        identity = (pin.scope.build_id, pin.scope.session_date, pin.parents.market_plan_hash,
                    pin.parents.opening_reference_hash, pin.scope.first_bucket,
                    pin.scope.product_revision)
        if session is not None and session != identity:
            raise ValueError("Program inputs differ in their certified session or clock origin")
        session = identity
        catalog = {field.name: field for field in pin.fields}
        for name, field in release.fields.items():
            if (name not in catalog or field.unit != catalog[name].unit
                    or field.product != "arte.qmd_causal_history_value_v1"
                    or field.revision != pin.scope.product_revision or field.resolution_ms != 1000):
                raise ValueError("Program field differs from its producer-owned history contract")
        packet_size = min(4096, 65536 // max(1, len(pin.fields)))
        for first in range(first_ordinal, stop_ordinal, packet_size):
            stop = min(first + packet_size, stop_ordinal)
            window_request(pin, first_ordinal=first, stop_ordinal=stop, as_of=as_of)
            jobs.append((listing, pin, first, stop))
    # All pins, field contracts and future-clock checks precede the first I/O.
    iterator = iter(enumerate(jobs))
    packets = [None] * len(jobs)
    async def worker():
        while (item := next(iterator, None)) is not None:
            index, (listing, pin, first, stop) = item
            packets[index] = await read_history_window_async(pin, first_ordinal=first,
                stop_ordinal=stop, evaluation_block=evaluation_block, as_of=as_of,
                history_post=history_post)
    workers = [asyncio.create_task(worker()) for _ in range(min(concurrency, len(jobs)))]
    try:
        await asyncio.gather(*workers)
    except BaseException:
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
    def evaluate():
        # One cumulative Float32 origin per listing/block, across every packet.
        market = pl.concat(packets).sort("listing_id", "evaluation_block", "observed_ordinal")
        masks = evaluate_rules(market, release.programs, release.fields)
        return market, masks
    market, masks = await _owned_worker(evaluate)
    return PreparedProgramBlock(release.header["content_hash"], evaluation_block,
        first_ordinal, stop_ordinal,
        MappingProxyType({listing: pin.header["content_hash"] for listing, pin in owned.items()}),
        market, masks)
