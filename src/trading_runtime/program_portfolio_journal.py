"""Closed native Portfolio records to existing normalized V4 journal families."""
import asyncio
from dataclasses import dataclass
from uuid import UUID, NAMESPACE_URL, uuid5

from .arte_journal_commit_v4 import load_verified_v4_prefix
from .arte_journal_projection import project_portfolio_reservation_record, project_journal_record
from .arte_journal_writer import ArteJournalWriter, TypedJournalBatch
from .arte_portfolio_allocation_v4 import V4PortfolioAllocationBatch, project_portfolio_allocation_v3
from .arte_portfolio_control_v4 import V4PortfolioControlBatch, project_portfolio_control_v3
from .arte_reservation_reason_v4 import V4ReservationReasonBatch, project_reservation_reasons_v3
from .journal_contract import JournalRecord


@dataclass(frozen=True, slots=True)
class ProgramPortfolioJournalReceipt:
    run_id: str
    batch_id: str
    last_sequence: int
    record_count: int


def project_program_portfolio_records(records, *, run_id, account_key, at,
        attempt_id, prior_batch_id, first_sequence, transition_hash, expected_mode):
    """Preserve every source fact and reason; unknown variants fail before writes."""
    if (not 1 <= len(records) <= 4096 or expected_mode not in {'backtest', 'replay', 'paper', 'live'}
            or at.tzinfo is None or at.utcoffset() is None):
        raise ValueError('Portfolio source recipe requires bounded records and an aware clock')
    UUID(attempt_id)
    UUID(prior_batch_id)
    units = []
    previous = prior_batch_id
    for ordinal, (kind, entity_id, account_id, payload) in enumerate(records):
        batch_id = str(uuid5(NAMESPACE_URL, f'{run_id}:{transition_hash}:batch:{ordinal}'))
        record_id = str(uuid5(NAMESPACE_URL, f'{run_id}:{transition_hash}:record:{ordinal}'))
        source = dict(payload)
        # Source emitters omit envelope lineage. Add only actual transaction
        # identity; every original scalar is preserved by the closed codec.
        source.setdefault('correlation_id', transition_hash)
        source.setdefault('causation_id', transition_hash)
        record = JournalRecord(record_id, run_id, first_sequence + ordinal, at, at,
            'portfolio_management', kind, entity_id, account_id, source)
        identity = dict(run_month=at.date().replace(day=1), attempt_id=attempt_id,
            batch_id=batch_id, prior_batch_id=previous, source_cursor='portfolio-transition:' + transition_hash)
        if kind == 'portfolio_control':
            projection = project_portfolio_control_v3(record, attempt_id=attempt_id,
                batch_id=batch_id, account_key=account_key)
            base = TypedJournalBatch(run_id, identity['run_month'], attempt_id, batch_id,
                previous, record.sequence, record.sequence, identity['source_cursor'], 'running', (projection.event,))
            unit = V4PortfolioControlBatch(base, projection.detail)
        elif kind == 'portfolio_allocation':
            projection = project_portfolio_allocation_v3(record, attempt_id=attempt_id, batch_id=batch_id)
            base = TypedJournalBatch(run_id, identity['run_month'], attempt_id, batch_id,
                previous, record.sequence, record.sequence, identity['source_cursor'], 'running', (projection.event,))
            unit = V4PortfolioAllocationBatch(base, projection.detail)
        elif kind == 'portfolio_reservation':
            base = project_portfolio_reservation_record(record, **identity, allow_reasons=True)
            reasons = project_reservation_reasons_v3(record, batch_id=batch_id)
            unit = V4ReservationReasonBatch(base, reasons) if reasons else base
        elif kind == 'portfolio_decision':
            unit = project_journal_record(record, **identity, expected_mode=expected_mode)
        else:
            raise ValueError('Portfolio transition has no exact normalized source family: ' + kind)
        units.append(unit)
        previous = batch_id
    return tuple(units)


class ProgramPortfolioRecordJournal:
    def __init__(self, *, client, writer, attempt_id):
        if type(writer) is not ArteJournalWriter or str(UUID(attempt_id)) != attempt_id:
            raise TypeError('Portfolio source journal requires native writer and exact attempt UUID')
        if writer.coalesce_batches is not False:
            raise ValueError('Portfolio source journal requires exact non-coalesced native receipts')
        self.client, self.writer, self.attempt_id = client, writer, attempt_id
        self.lock = asyncio.Lock()
        self.failed = False

    async def publish(self, records, captured, *, transition_hash):
        if self.failed:
            raise RuntimeError('Portfolio source journal requires cold reconciliation')
        async with self.lock:
            try:
                prefix = await asyncio.to_thread(load_verified_v4_prefix, self.client, captured.run_id)
                if prefix is None or prefix.status != 'running':
                    raise RuntimeError('Portfolio source journal lacks a verified running prefix')
                if len(records) == 1 and records[0][0] == 'portfolio_reconciliation':
                    from .arte_journal_projection import project_portfolio_reconciliation_records
                    batch_id = str(uuid5(NAMESPACE_URL, f'{captured.run_id}:{transition_hash}:reconciliation'))
                    units = (project_portfolio_reconciliation_records(records, captured=captured,
                        run_month=captured.snapshot_at.date().replace(day=1), attempt_id=self.attempt_id,
                        batch_id=batch_id, prior_batch_id=prefix.last_batch_id,
                        first_sequence=prefix.last_sequence + 1,
                        source_cursor='portfolio-transition:' + transition_hash),)
                else:
                    units = project_program_portfolio_records(records, run_id=captured.run_id,
                    account_key=captured.account_key, at=captured.snapshot_at,
                    attempt_id=self.attempt_id, prior_batch_id=prefix.last_batch_id,
                    first_sequence=prefix.last_sequence + 1, transition_hash=transition_hash,
                    expected_mode=self.writer._run_mode)
                for unit in units:
                    if type(unit) is V4PortfolioControlBatch:
                        future = self.writer.submit_portfolio_control_v4(unit)
                    elif type(unit) is V4PortfolioAllocationBatch:
                        future = self.writer.submit_portfolio_allocation_v4(unit)
                    elif type(unit) is V4ReservationReasonBatch:
                        future = self.writer.submit_reservation_reason_v4(unit)
                    else:
                        future = self.writer.submit_base_v4(unit)
                    base = unit if type(unit) is TypedJournalBatch else unit.base
                    if await asyncio.wrap_future(future) != base.batch_id:
                        raise RuntimeError('Portfolio source record lacks exact V4 commit receipt')
                after = await asyncio.to_thread(load_verified_v4_prefix, self.client, captured.run_id)
                if after is None or (after.last_batch_id, after.last_sequence) != (base.batch_id, base.last_sequence):
                    raise RuntimeError('Portfolio source journal prefix differs after publication')
                return ProgramPortfolioJournalReceipt(captured.run_id, base.batch_id,
                    base.last_sequence, len(records))
            except BaseException:
                self.failed = True
                raise


def program_portfolio_journal_write_tables():
    """Reviewed generic-principal inventory; existing profiles remain unchanged."""
    from .arte_journal_writer import v4_journal_write_tables
    from .arte_portfolio_control_v4 import CONTROL
    return v4_journal_write_tables() | {CONTROL.name}
