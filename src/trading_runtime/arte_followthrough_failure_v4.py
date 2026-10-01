"""Normalized Strategy 9 failure witness; no alteration of occupied schemas."""
from dataclasses import dataclass, fields
from types import MappingProxyType
from uuid import UUID, NAMESPACE_URL, uuid5
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from .arte_journal_schema import TableContract
from .strategy_followthrough_failure import FollowThroughFailure
from .strategy_followthrough_exit import validate_witness

FAILURE = TableContract("trading_followthrough_failure_v4", (
    ("record_id", "UUID"), ("parent_record_id", "UUID"),
    ("run_id", "String"), ("event_month", "Date"), ("batch_id", "UUID"),
    ("strategy_number", "UInt32"), ("source_entry_intent_id", "UUID"),
    ("assignment_id", "String"), ("boundary_ms", "UInt32"),
    ("first_held_boundary_ms", "UInt32"), ("reference_ask", "Decimal(38, 18)"),
    ("initial_stop", "Decimal(38, 18)"), ("completed_close_int", "UInt64"),
    ("macd_line", "Float64"), ("macd_signal", "Float64"),
    ("bid", "Decimal(38, 18)"), ("ask", "Decimal(38, 18)"),
    ("quote_age_us", "UInt64"), ("content_hash", "FixedString(64)")),
    "toYYYYMM(event_month)", "run_id, parent_record_id, record_id")
TABLES = (FAILURE,)
REASON = "strategy_nine_followthrough_failure"


@dataclass(frozen=True, slots=True)
class V4FollowThroughFailureBatch:
    base: object
    failure: object

    def __post_init__(self):
        from .arte_journal_writer import TypedJournalBatch
        if (type(self.base) is not TypedJournalBatch or self.base.status != "running"
                or len(self.base.events) != 1 or len(self.base.intents) != 1):
            raise ValueError("Follow-through witness requires one exact typed intent")
        object.__setattr__(self, "failure", MappingProxyType(dict(self.failure)))



def validate_numbered_failure(witness, strategy_number):
    """Pin the successor eligibility bound at every persistence boundary."""
    validate_witness(witness, strategy_number=strategy_number if strategy_number in (25, 26, 27, 28, 29, 30, 31, 32) else 9)
    if type(strategy_number) is not int or strategy_number not in (9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32):
        raise ValueError("Failure evidence requires Strategy 9 through 32")
    if strategy_number in (11, 12, 13, 14, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28):
        from .strategy_early_followthrough_failure import EARLY_FAILURE_WINDOW_MS
        if witness.boundary_ms - witness.first_held_boundary_ms > EARLY_FAILURE_WINDOW_MS:
            raise ValueError("Strategy 11 failure witness exceeds the first-minute eligibility window")

def project_followthrough_failure(witness, intent, source_entry_intent_id, *,
                                run_id, batch_id, parent_record_id, assignment_id, strategy_number=9):
    validate_numbered_failure(witness, strategy_number)
    UUID(source_entry_intent_id)
    if intent.reason != REASON or intent.action != "exit" or intent.metadata or not assignment_id:
        raise ValueError("Failure witness requires a metadata-free Strategy 9 exit")
    return dict(record_id=str(uuid5(NAMESPACE_URL, f"{run_id}:{parent_record_id}:followthrough")),
        parent_record_id=parent_record_id, run_id=run_id,
        event_month=intent.event_time.astimezone(timezone.utc).strftime("%Y-%m-01"),
        batch_id=batch_id, strategy_number=strategy_number, source_entry_intent_id=source_entry_intent_id,
        assignment_id=assignment_id, **{f.name: getattr(witness, f.name) for f in fields(witness)})


def restore_failure(row):
    integer = {"boundary_ms", "first_held_boundary_ms", "completed_close_int", "quote_age_us"}
    witness = FollowThroughFailure(**{f.name: (int(row[f.name]) if f.name in integer else float(row[f.name]))
                                     for f in fields(FollowThroughFailure)})
    validate_numbered_failure(witness, row["strategy_number"])
    return witness


def _source_entry(client, run_id, intent_id, *, prior_batch_id, exit_batch_id,
                  verified_prefix=None, first_price_source=None):
    from .arte_journal_writer import _rows, _literal, _CONTRACTS
    from .arte_journal_commit_v4 import load_verified_commit_v4, verified_batch_predecessor
    from .arte_strategy_one_entry_schema import ENTRY_EVIDENCE
    def read(name, predicate):
        columns = ','.join(k for k, _ in _CONTRACTS[name].columns)
        rows = _rows(client, f"SELECT {columns} FROM arte.{name} WHERE run_id={_literal(run_id)} AND {predicate} LIMIT 2 FORMAT JSONEachRow")
        if len(rows) != 1:
            raise RuntimeError("Follow-through original entry authority is missing or ambiguous")
        return rows[0]
    intent = read("trading_strategy_intent_v1", f"intent_id IN ({_literal(intent_id)})")
    event = read("trading_event_v1", f"record_id IN (toUUID({_literal(str(intent['record_id']))}))")
    child = read(ENTRY_EVIDENCE.name, f"parent_record_id IN (toUUID({_literal(str(intent['record_id']))}))")
    context = {}
    if verified_prefix is not None:
        if verified_prefix.run_id != run_id or event['sequence'] > verified_prefix.last_sequence:
            raise RuntimeError('Original entry differs from its verified source prefix')
        preceding = verified_batch_predecessor(client, verified_prefix, str(intent['batch_id']))
        if preceding is not None:
            context['verified_prior_prefix'] = preceding
    if first_price_source is not None:
        context['first_price_source'] = first_price_source
    source_commit, _ = load_verified_commit_v4(
        client, run_id=run_id, batch_id=str(intent['batch_id']), **context)
    # Read the bounded interval once: chain length must not turn a failure
    # decision into one HTTP round trip per predecessor. Source details retain
    # their independent committed seal; this scalar inventory proves ancestry.
    cursor = prior_batch_id
    if cursor is None:
        current = read("trading_commit_v4", f"batch_id=toUUID({_literal(exit_batch_id)})")
        cursor = str(current['prior_batch_id'])
    predecessor = (source_commit if cursor == str(source_commit['batch_id']) else
        read("trading_commit_v4", f"batch_id=toUUID({_literal(cursor)})"))
    _verify_source_ancestor_interval(client, run_id, source_commit, predecessor)
    return intent, event, child



def _verify_source_ancestor_interval(client, run_id, source_commit, predecessor,
                                     *, max_commits=100_000):
    """One bounded SELECT, then exact causal predecessor checks in memory."""
    from .arte_journal_writer import _rows, _literal
    if type(max_commits) is not int or not 1 <= max_commits <= 100_000:
        raise ValueError("Failure ancestry needs a bounded commit inventory")
    lower = int(source_commit['first_sequence'])
    upper = int(predecessor['last_sequence'])
    if lower < 1 or upper < lower:
        raise RuntimeError("Failure entry is outside its committed ancestor interval")
    rows = _rows(client,
        "SELECT batch_id,prior_batch_id,first_sequence,last_sequence "
        "FROM arte.trading_commit_v4 "
        f"WHERE run_id={_literal(run_id)} AND first_sequence>={lower} "
        f"AND last_sequence<={upper} ORDER BY first_sequence "
        f"LIMIT {max_commits + 1} FORMAT JSONEachRow")
    if not rows or len(rows) > max_commits:
        raise RuntimeError("Failure ancestry is missing or exceeds its bounded cold read")
    by_id = {}
    for row in rows:
        identity = str(UUID(str(row['batch_id'])))
        if (identity in by_id or UUID(identity).int == 0
                or not lower <= int(row['first_sequence']) <= int(row['last_sequence']) <= upper):
            raise RuntimeError("Failure ancestry has ambiguous or invalid commit intervals")
        by_id[identity] = row
    source_id = str(UUID(str(source_commit['batch_id'])))
    cursor = str(UUID(str(predecessor['batch_id'])))
    expected_last, seen = upper, set()
    while True:
        row = by_id.get(cursor)
        if row is None or cursor in seen or int(row['last_sequence']) != expected_last:
            raise RuntimeError("Failure original entry is outside its contiguous ancestor chain")
        seen.add(cursor)
        if cursor == source_id:
            if any(str(row[k]) != str(source_commit[k]) for k in (
                    'batch_id', 'prior_batch_id', 'first_sequence', 'last_sequence')):
                raise RuntimeError("Failure source ancestry differs from its committed seal")
            break
        expected_last = int(row['first_sequence']) - 1
        cursor = str(UUID(str(row['prior_batch_id'])))
    if len(seen) != len(rows):
        raise RuntimeError("Failure ancestry includes an orphan or conflicting commit")

def seal_followthrough_rows(client, rows, intents, events, entries=(), *, prior_batch_id=None,
                            verified_prefix=None, first_price_source=None):
    """Bind witness, exit and original entry graph before committing a head."""
    from .arte_journal_writer import typed_row, _canonical_typed_content
    parents = {str(r['record_id']): r for r in intents if r['reason'] == REASON}
    event_map = {str(r['record_id']): r for r in events}
    if len(rows) != len(parents):
        raise ValueError("Follow-through exit witness is missing or extra")
    sealed, seen = [], set()
    for row in rows:
        parent_id = str(row['parent_record_id'])
        if parent_id in seen or parent_id not in parents or parent_id not in event_map:
            raise ValueError("Follow-through witness lacks a unique exit parent")
        seen.add(parent_id)
        parent, event = parents[parent_id], event_map[parent_id]
        witness = restore_failure(row)
        local = datetime.fromisoformat(str(event['event_time']).replace('Z', '+00:00'))
        if local.tzinfo is None:
            local = local.replace(tzinfo=timezone.utc)
        local = local.astimezone(ZoneInfo('America/New_York'))
        elapsed = local - datetime.combine(local.date(), time(4), local.tzinfo)
        source = next((r for r in intents if str(r['intent_id']) == str(row['source_entry_intent_id'])), None)
        if source is None:
            context = {}
            if verified_prefix is not None:
                context['verified_prefix'] = verified_prefix
            if first_price_source is not None:
                context['first_price_source'] = first_price_source
            source, source_event, source_child = _source_entry(client, row['run_id'], str(row['source_entry_intent_id']),
                prior_batch_id=prior_batch_id, exit_batch_id=str(row['batch_id']), **context)
        else:
            source_event = event_map[str(source['record_id'])]
            matches = [r for r in entries if str(r['parent_record_id']) == str(source['record_id'])]
            if len(matches) != 1:
                raise ValueError("Follow-through original entry has no exact typed evidence")
            source_child = matches[0]
        if (row['strategy_number'] not in (9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32)
                or source_child['strategy_number'] != row['strategy_number']
                or row['assignment_id'] != source_child['assignment_id']
                or row['run_id'] != parent['run_id'] or row['batch_id'] != parent['batch_id']
                or row['event_month'] != parent['event_month']
                or event['account_id'] != source_event['account_id']
                or source['ticker'] != parent['ticker'] or parent['action'] != 'exit'
                or source['action'] != 'enter_long' or source['reason'] != 'strategy_one_entry'
                or source_event['sequence'] >= event['sequence']
                or source_child['boundary_ms'] >= witness.first_held_boundary_ms
                or float(source['reference_price']) != witness.reference_ask
                or float(source['invalidation_price']) != witness.initial_stop
                or float(parent['reference_price']) != witness.bid
                or round(elapsed.total_seconds() * 1000) != witness.boundary_ms
                or parent['intent_id'] != event['entity_id']):
            raise ValueError("Follow-through scalar witness differs from its original entry or exit")
        from .strategy_one_stateful import StrategyOneFinancialView
        from .strategy_engine import AssignmentStatus, StrategyPermissions
        from .strategy_followthrough_exit import followthrough_exit_intent
        from .arte_intent_projection import project_strategy_intent
        financial = StrategyOneFinancialView(row['assignment_id'], event['account_id'],
            parent['ticker'], AssignmentStatus.WATCHING, StrategyPermissions(),
            float(parent['quantity']), False, False, False, 1)
        expected = followthrough_exit_intent(witness, financial, session_date=local.date(),
            source_entry_intent_id=str(row['source_entry_intent_id']),
            strategy_number=row['strategy_number'] if row['strategy_number'] in (25, 26, 27, 28, 29, 30, 31, 32) else 9)
        content = {k: v for k, v in parent.items() if k != 'content_hash'}
        expected_content = {**content, **{k: v for k, v in project_strategy_intent(expected).core.items()
                                         if k != "event_time"}}
        if (_canonical_typed_content('trading_strategy_intent_v1', content, stored_utc=True)
                != _canonical_typed_content('trading_strategy_intent_v1', expected_content, stored_utc=True)):
            raise ValueError("Follow-through exit differs from its immutable factory intent")
        sealed.append(typed_row(FAILURE.name, {k: v for k, v in row.items() if k != 'content_hash'}))
    return tuple(sealed)


def load_followthrough_failure(client, prefix, exit_record_id):
    from .arte_journal_writer import _literal, _rows, _CONTRACTS
    from .arte_intent_projection import _verify_stored_row
    columns = ','.join(k for k, _ in _CONTRACTS[FAILURE.name].columns)
    rows = _rows(client, f"SELECT {columns} FROM arte.{FAILURE.name} WHERE run_id={_literal(prefix.run_id)} AND parent_record_id IN (toUUID({_literal(exit_record_id)})) LIMIT 2 FORMAT JSONEachRow")
    if len(rows) != 1 or str(rows[0]['batch_id']) not in prefix.batch_ids:
        raise RuntimeError("Recovered failure witness lacks its committed prefix")
    _verify_stored_row(FAILURE.name, rows[0])
    return rows[0], restore_failure(rows[0])
