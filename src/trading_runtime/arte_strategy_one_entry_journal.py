"""Staged normalized Strategy 1 entry evidence, not yet a writer family.

The existing typed intent and protection rows own prices, policy, sizing and
target fraction. This child owns only Strategy 1's nonredundant causal facts.
It must be added to the V4 commit seal and cold recovery before publication.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from hashlib import sha256
import re
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from .arte_intent_projection import _number
from .arte_intent_projection import load_committed_strategy_intent_page
from .arte_journal_writer import (
    _CONTRACTS, _canonical_typed_content, _literal, _rows,
    _sealed_families, typed_row,
)
from .arte_journal_commit_v4 import V4CommittedPrefix, load_verified_commit_v4
from .journal_contract import canonical_json
from .arte_strategy_one_entry_schema import ENTRY_EVIDENCE
from .signals import StrategyIntent
from .strategy_one_intent import strategy_one_entry_intent
from .strategy_one_stateful import StrategyOneEntryProposal
from .arte_rising_momentum_entry_v4 import (
    MOMENTUM, momentum_select_columns, decode_momentum_row,
    seal_rising_momentum_rows, restore_rising_momentum,
)
from .arte_initial_momentum_entry_v4 import (
    INITIAL_MOMENTUM, initial_momentum_select_columns, decode_initial_momentum_row,
    seal_initial_momentum_rows, restore_initial_momentum,
)


def project_strategy_one_entry_evidence(
    proposal: StrategyOneEntryProposal, intent: StrategyIntent, *,
    session_date: date, run_id: str, batch_id: str, parent_record_id: str,
    first_price_source=None,
) -> dict[str, str | int]:
    """Link only exact numbered evidence to its separately typed intent.

    `content_hash` and batch sealing belong to the background journal writer,
    never the execution callback. This projector performs no I/O.
    """
    if (not isinstance(proposal, StrategyOneEntryProposal)
            or not isinstance(intent, StrategyIntent)
            or not run_id or not proposal.target_level_id
            or not proposal.bos_support_level_id):
        raise ValueError("Strategy 1 entry evidence identity is incomplete")
    batch = str(UUID(batch_id))
    parent = str(UUID(parent_record_id))
    if proposal.strategy_number in (37, 38, 39, 40, 41, 42, 46):
        from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
        from src.backend.backtest_strategy_episode_activity_source import certified_episode_entry_intent
        if type(first_price_source) is not CertifiedPriceReadbackAuthority or first_price_source.run_id != run_id:
            raise ValueError('Strategy37 entry projection requires its exact native source')
        expected = certified_episode_entry_intent(first_price_source, proposal, session_date=session_date)
    elif proposal.strategy_number in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46):
        from src.backend.backtest_strategy_certified_price_break import (
            CertifiedPriceReadbackAuthority, certified_price_entry_intent,
        )
        if type(first_price_source) is not CertifiedPriceReadbackAuthority or first_price_source.run_id != run_id:
            raise ValueError("Strategy20 entry projection requires its native source context")
        expected = certified_price_entry_intent(first_price_source.plan, proposal, session_date=session_date)
    else:
        expected = strategy_one_entry_intent(proposal, session_date=session_date)
    if intent != expected:
        raise ValueError("Strategy 1 entry evidence differs from its typed intent")
    gap = _number(proposal.frozen_gap)
    if gap is None or Decimal(gap) <= 0:
        raise ValueError("Strategy 1 entry evidence requires a positive frozen gap")
    if (type(proposal.episode_start_ms) is not int
            or not 0 < proposal.episode_start_ms <= proposal.boundary_ms
            or type(proposal.bos_break_boundary_ms) is not int
            or not 0 < proposal.bos_break_boundary_ms <= proposal.boundary_ms):
        raise ValueError("Strategy 1 entry evidence has invalid causal boundaries")
    return {
        "record_id": str(uuid5(NAMESPACE_URL, f"{parent}:strategy-one-entry")),
        "parent_record_id": parent,
        "run_id": run_id,
        "event_month": session_date.replace(day=1).isoformat(),
        "batch_id": batch,
        "strategy_number": proposal.strategy_number,
        "assignment_id": proposal.assignment_id,
        "episode_start_ms": proposal.episode_start_ms,
        "boundary_ms": proposal.boundary_ms,
        "target_level_id": proposal.target_level_id,
        "frozen_gap": gap,
        "bos_break_boundary_ms": proposal.bos_break_boundary_ms,
        "bos_support_level_id": proposal.bos_support_level_id,
    }


def seal_strategy_one_entry_evidence(row: dict[str, str | int]) -> dict[str, str | int]:
    """Hash the normalized persisted values on the journal writer lane."""
    return typed_row(ENTRY_EVIDENCE.name, row)


@dataclass(frozen=True, slots=True)
class RecoveredStrategyOneEntry:
    sequence: int
    parent_record_id: str
    batch_id: str
    proposal: StrategyOneEntryProposal
    intent: StrategyIntent


@dataclass(frozen=True, slots=True)
class RecoveredStrategyOneEntryPage:
    entries: tuple[RecoveredStrategyOneEntry, ...]
    scanned_through_sequence: int
    exhausted: bool


def load_committed_strategy_one_entry_page(
    client, prefix: V4CommittedPrefix, *, after_sequence: int = 0,
    limit: int = 200,
    first_price_source=None,
) -> RecoveredStrategyOneEntryPage:
    """Reconstruct exact proposals from a previously cold-verified V4 prefix.

    Verify the run prefix once before paging; rechecking every commit for
    every page would make recovery quadratic in a long Backtest journal.
    """
    if (not isinstance(prefix, V4CommittedPrefix)
            or not 1 <= limit <= 500 or not 0 <= after_sequence <= prefix.last_sequence
            or not prefix.run_id or not prefix.batch_ids
            or prefix.last_sequence < len(prefix.batch_ids)):
        raise ValueError("Strategy 1 recovery needs the current verified V4 prefix")
    intents = load_committed_strategy_intent_page(
        client, prefix, after_sequence=after_sequence, limit=limit)
    scanned = intents[-1].sequence if intents else prefix.last_sequence
    exhausted = len(intents) < limit
    selected = tuple(row for row in intents
                     if row.intent.reason == "strategy_one_entry")
    if not selected:
        return RecoveredStrategyOneEntryPage((), scanned, exhausted)
    ids = {row.record_id: row for row in selected}
    parent_intents = tuple(
        {"record_id": entry.record_id, "ticker": entry.intent.ticker,
         "reason": entry.intent.reason, "action": entry.intent.action,
         "intent_id": entry.intent.intent_id, "account_id": entry.account_id,
         "run_id": prefix.run_id, "batch_id": entry.batch_id,
         "event_month": entry.intent.event_time.astimezone(ZoneInfo("America/New_York")).date().replace(day=1).isoformat()}
        for entry in selected)
    parent_events = tuple(
        {"record_id": entry.record_id, "event_time": entry.intent.event_time.isoformat(),
         "category": "strategy", "entity_type": "strategy_intent",
         "entity_id": entry.intent.intent_id, "account_id": entry.account_id,
         "run_id": prefix.run_id, "batch_id": entry.batch_id,
         "event_month": entry.intent.event_time.astimezone(ZoneInfo("America/New_York")).date().replace(day=1).isoformat()}
        for entry in selected)
    sql_ids = ",".join(f"toUUID({_literal(record_id)})" for record_id in ids)
    columns = ",".join(name for name, _ in ENTRY_EVIDENCE.columns)
    rows = _rows(client,
        f"SELECT {columns} FROM arte.{ENTRY_EVIDENCE.name} "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND parent_record_id IN ({sql_ids}) "
        f"LIMIT {len(ids) + 1} FORMAT JSONEachRow")
    if len(rows) != len(ids):
        raise RuntimeError("Committed Strategy 1 entry evidence is missing or duplicated")
    momentum_rows = ()
    if any(row["strategy_number"] in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46) for row in rows):
        momentum_rows = tuple(decode_momentum_row(row) for row in _rows(client,
            f"SELECT {momentum_select_columns()} FROM arte.{MOMENTUM.name} "
            f"WHERE run_id={_literal(prefix.run_id)} AND parent_record_id IN ({sql_ids}) "
            f"LIMIT {2 * len(ids) + 1} FORMAT JSONEachRow"))
        seal_rising_momentum_rows(momentum_rows, rows, parent_intents, parent_events)
    allowed_batches = set(prefix.batch_ids)
    initial_rows = ()
    if any(row["strategy_number"] in (18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46) for row in rows):
        initial_rows = tuple(decode_initial_momentum_row(row) for row in _rows(client,
            f"SELECT {initial_momentum_select_columns()} FROM arte.{INITIAL_MOMENTUM.name} "
            f"WHERE run_id={_literal(prefix.run_id)} AND parent_record_id IN ({sql_ids}) "
            f"LIMIT {2 * len(ids) + 1} FORMAT JSONEachRow"))
        seal_initial_momentum_rows(initial_rows, rows, parent_intents, parent_events, momentum_rows)
    price_by_parent = {}
    if any(row["strategy_number"] in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46) for row in rows):
        from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
        from .arte_first_price_entry_v4 import FIRST_PRICE, seal_first_price_rows
        if type(first_price_source) is not CertifiedPriceReadbackAuthority or first_price_source.run_id != prefix.run_id:
            raise RuntimeError("Strategy20 recovery requires its independent native source context")
        columns = ",".join(name for name, _ in FIRST_PRICE.columns)
        price_rows = tuple(_rows(client,
            f"SELECT {columns} FROM arte.{FIRST_PRICE.name} "
            f"WHERE run_id={_literal(prefix.run_id)} AND parent_record_id IN ({sql_ids}) "
            f"LIMIT {len(ids) + 1} FORMAT JSONEachRow"))
        authorities = first_price_source.resolve(prefix.run_id, rows, parent_intents)
        seal_first_price_rows(price_rows, rows, parent_intents, parent_events, authorities)
        for price_row in price_rows:
            price_by_parent.setdefault(price_row['parent_record_id'], []).append(price_row)
    if any(row['strategy_number'] in (36, 37, 38, 39, 40, 41, 42, 46) for row in rows):
        from .arte_entry_activity_v4 import ENTRY_ACTIVITY, seal_certified_entry_activity_rows
        columns = ','.join(name for name, _ in ENTRY_ACTIVITY.columns)
        stored_activity = tuple(_rows(client,
            f'SELECT {columns} FROM arte.{ENTRY_ACTIVITY.name} '
            f'WHERE run_id={_literal(prefix.run_id)} AND parent_record_id IN ({sql_ids}) '
            f'LIMIT {len(ids) + 1} FORMAT JSONEachRow'))
        activity_rows = []
        for row in stored_activity:
            canonical = _canonical_typed_content(ENTRY_ACTIVITY.name,
                {key: value for key, value in row.items() if key != 'content_hash'}, stored_utc=True)
            if sha256(canonical_json(canonical).encode()).hexdigest() != row['content_hash']:
                raise RuntimeError('Committed activity evidence differs from its row hash')
            activity_rows.append(dict(canonical, content_hash=row['content_hash']))
        seal_certified_entry_activity_rows(tuple(activity_rows), rows, parent_intents,
            parent_events, run_id=prefix.run_id,
            source=getattr(first_price_source, 'entry_activity_source', None))
    seen = set()
    result = []
    for row in rows:
        parent = str(UUID(str(row["parent_record_id"])))
        if parent in seen or parent not in ids:
            raise RuntimeError("Committed Strategy 1 entry has an unknown parent")
        seen.add(parent)
        content = {key: value for key, value in row.items()
                   if key != "content_hash"}
        canonical = _canonical_typed_content(
            ENTRY_EVIDENCE.name, content, stored_utc=True)
        digest = sha256(canonical_json(canonical).encode()).hexdigest()
        recovered = ids[parent]
        instant = recovered.intent.event_time.astimezone(
            ZoneInfo("America/New_York"))
        session_date = instant.date()
        if (row["content_hash"] != digest
                or str(UUID(str(row["batch_id"]))) not in allowed_batches
                or str(UUID(str(row["batch_id"]))) != recovered.batch_id
                or row["record_id"] != str(uuid5(
                    NAMESPACE_URL, f"{parent}:strategy-one-entry"))
                or row["event_month"] != session_date.replace(day=1).isoformat()
                or row["strategy_number"] not in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46)):
            raise RuntimeError("Committed Strategy 1 entry evidence changed")
        intent = recovered.intent
        if intent.invalidation_price is None or intent.profit_target_price is None:
            raise RuntimeError("Committed Strategy 1 entry lacks typed protection")
        proposal = StrategyOneEntryProposal(
            str(row["assignment_id"]), recovered.account_id, intent.ticker,
            int(row["boundary_ms"]), int(row["episode_start_ms"]),
            intent.reference_price, intent.invalidation_price,
            intent.profit_target_price, str(row["target_level_id"]),
            float(row["frozen_gap"]), int(row["bos_break_boundary_ms"]),
            str(row["bos_support_level_id"]), int(row["strategy_number"]),
            restore_rising_momentum(tuple(r for r in momentum_rows if r["parent_record_id"] == parent),
                ticker=intent.ticker, boundary_ms=int(row["boundary_ms"]),
                strategy_number=row["strategy_number"])
            if row["strategy_number"] in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46) else None,
        )
        if proposal.strategy_number in (18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46):
            from dataclasses import replace
            proposal = replace(proposal, initial_momentum=restore_initial_momentum(
                tuple(r for r in initial_rows if r["parent_record_id"] == parent),
                ticker=intent.ticker, boundary_ms=proposal.boundary_ms,
                episode_start_ms=proposal.episode_start_ms, current_momentum=proposal.momentum,
                strategy_number=proposal.strategy_number))
        if proposal.strategy_number in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46):
            from src.backend.backtest_strategy_certified_price_break import (
                restore_certified_price_proposal, certified_price_entry_intent,
            )
            proposal = restore_certified_price_proposal(first_price_source, proposal,
                tuple(price_by_parent.get(parent, ())), parent_record_id=parent, batch_id=recovered.batch_id)
            if proposal.strategy_number in (37, 38, 39, 40, 41, 42, 46):
                from src.backend.backtest_strategy_episode_activity_source import certified_episode_entry_intent
                expected_intent = certified_episode_entry_intent(first_price_source, proposal, session_date=session_date)
            else:
                expected_intent = certified_price_entry_intent(first_price_source.plan, proposal, session_date=session_date)
        else:
            expected_intent = strategy_one_entry_intent(proposal, session_date=session_date)
        if expected_intent != intent:
            raise RuntimeError("Committed Strategy 1 proposal differs from its intent")
        result.append(RecoveredStrategyOneEntry(
            recovered.sequence, parent, recovered.batch_id, proposal, intent))
    return RecoveredStrategyOneEntryPage(
        tuple(sorted(result, key=lambda item: item.sequence)), scanned, exhausted)


def load_committed_strategy_one_source(
    client, prefix: V4CommittedPrefix, entry: RecoveredStrategyOneEntry,
    *, first_price_source=None,
):
    """Rebuild an exact OMS source revision from a cold-verified typed entry.

    This is a SELECT-only, per-source recovery operation, not a hot-path scan.
    The caller obtains `entry` through the bounded verified page above.
    """
    from .arte_intent_projection import strategy_intent_batch
    from .arte_journal_commit_v4 import verified_batch_predecessor

    if (not isinstance(prefix, V4CommittedPrefix)
            or not isinstance(entry, RecoveredStrategyOneEntry)
            or entry.batch_id not in prefix.batch_ids
            or not 1 <= entry.sequence <= prefix.last_sequence):
        raise ValueError("Strategy 1 source is outside the verified V4 prefix")
    context = {}
    preceding = verified_batch_predecessor(client, prefix, entry.batch_id)
    if preceding is not None:
        context['verified_prior_prefix'] = preceding
    if first_price_source is not None:
        context['first_price_source'] = first_price_source
    commit, family_rows = load_verified_commit_v4(
        client, run_id=prefix.run_id, batch_id=entry.batch_id, **context)
    if not commit['first_sequence'] <= entry.sequence <= commit['last_sequence']:
        raise RuntimeError('Strategy 1 source is outside its committed batch')
    if entry.proposal.strategy_number in (13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46):
        companions = tuple(decode_momentum_row(row) for row in _rows(client,
            f"SELECT {momentum_select_columns()} FROM arte.{MOMENTUM.name} "
            f"WHERE run_id={_literal(prefix.run_id)} "
            f"AND batch_id=toUUID({_literal(entry.batch_id)}) "
            f"AND parent_record_id IN (toUUID({_literal(entry.parent_record_id)})) "
            "LIMIT 3 FORMAT JSONEachRow"))
        actual = restore_rising_momentum(companions, ticker=entry.proposal.ticker,
                                         boundary_ms=entry.proposal.boundary_ms,
                                         strategy_number=entry.proposal.strategy_number)
        if actual != entry.proposal.momentum:
            raise RuntimeError("Strategy 13 source differs from its committed momentum detail")
        if entry.proposal.strategy_number in (18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46):
            anchors = tuple(decode_initial_momentum_row(row) for row in _rows(client,
                f"SELECT {initial_momentum_select_columns()} FROM arte.{INITIAL_MOMENTUM.name} "
                f"WHERE run_id={_literal(prefix.run_id)} "
                f"AND batch_id=toUUID({_literal(entry.batch_id)}) "
                f"AND parent_record_id IN (toUUID({_literal(entry.parent_record_id)})) "
                "LIMIT 3 FORMAT JSONEachRow"))
            initial = restore_initial_momentum(anchors, ticker=entry.proposal.ticker,
                boundary_ms=entry.proposal.boundary_ms,
                episode_start_ms=entry.proposal.episode_start_ms, current_momentum=actual,
                strategy_number=entry.proposal.strategy_number)
            if initial != entry.proposal.initial_momentum:
                raise RuntimeError("Strategy 18 source differs from committed first-setup selection")
        if entry.proposal.strategy_number in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46):
            from dataclasses import replace
            from .arte_first_price_entry_v4 import FIRST_PRICE
            from src.backend.backtest_strategy_certified_price_break import restore_certified_price_proposal
            columns = ",".join(name for name, _ in FIRST_PRICE.columns)
            price_rows = tuple(_rows(client,
                f"SELECT {columns} FROM arte.{FIRST_PRICE.name} "
                f"WHERE run_id={_literal(prefix.run_id)} AND batch_id=toUUID({_literal(entry.batch_id)}) "
                f"AND parent_record_id IN (toUUID({_literal(entry.parent_record_id)})) LIMIT 2 FORMAT JSONEachRow"))
            actual_proposal = restore_certified_price_proposal(first_price_source,
                replace(entry.proposal, first_price=None, price_source_token=None), price_rows,
                parent_record_id=entry.parent_record_id, batch_id=entry.batch_id)
            if actual_proposal != entry.proposal:
                raise RuntimeError("Strategy20 source differs from committed native price binding")
    columns = ",".join(name for name, _ in _CONTRACTS["trading_event_v1"].columns)
    events = _rows(client,
        f"SELECT {columns} FROM arte.trading_event_v1 "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND batch_id=toUUID({_literal(entry.batch_id)}) "
        f"AND record_id IN (toUUID({_literal(entry.parent_record_id)})) "
        f"LIMIT 2 FORMAT JSONEachRow")
    if len(events) != 1:
        raise RuntimeError("Strategy 1 source event is missing or ambiguous")
    event = events[0]
    if (str(UUID(str(event["record_id"]))) != entry.parent_record_id
            or event["sequence"] != entry.sequence
            or event["attempt_id"] != commit["attempt_id"]
            or event["category"] != "strategy"
            or event["entity_type"] != "strategy_intent"
            or event["entity_id"] != entry.intent.intent_id
            or event["account_id"] != entry.proposal.account_id):
        raise RuntimeError("Strategy 1 source event differs from recovered intent")
    recorded = str(event["recorded_at"])
    if re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{6}", recorded) is None:
        raise RuntimeError("Strategy 1 source receipt clock is not canonical UTC")
    rebuilt = strategy_intent_batch(
        entry.intent, run_id=prefix.run_id,
        run_month=date.fromisoformat(str(commit["run_month"])),
        account_id=str(event["account_id"]),
        attempt_id=str(event["attempt_id"]), batch_id=entry.batch_id,
        prior_batch_id=str(commit["prior_batch_id"]),
        sequence=entry.sequence, source_cursor=str(commit["source_cursor"]),
        run_status=str(commit["status"]),
        recorded_at=datetime.fromisoformat(recorded).replace(tzinfo=timezone.utc),
        record_id=entry.parent_record_id,
        correlation_id=str(event["correlation_id"]),
        causation_id=str(event["causation_id"]),
    )
    if typed_row("trading_event_v1", rebuilt.events[0])["content_hash"] != event["content_hash"]:
        raise RuntimeError("Strategy 1 source revision differs from its committed event")
    inventory = {row["family_name"]: row for row in family_rows}
    for name, rows in _sealed_families(rebuilt):
        if not rows:
            continue
        expected = inventory.get(name)
        identities = sorted((str(UUID(str(row["record_id"]))), row["content_hash"])
                            for row in rows)
        if expected is None or expected['row_count'] < len(rows):
            raise RuntimeError("Strategy 1 source revision differs from its committed detail")
        # The complete batch was independently verified above. A compound
        # commit may contain other entries or a profit exit; select exactly
        # this source's rows and verify their typed hashes against the rebuilt
        # intent rather than comparing a subset to a whole-family inventory.
        columns = ','.join(column for column, _ in _CONTRACTS[name].columns)
        keys = ','.join(f'toUUID({_literal(identity)})' for identity, _ in identities)
        actual = _rows(client,
            f'SELECT {columns} FROM arte.{name} WHERE run_id={_literal(prefix.run_id)} '
            f'AND batch_id=toUUID({_literal(entry.batch_id)}) '
            f'AND record_id IN ({keys}) LIMIT {len(rows) + 1} FORMAT JSONEachRow')
        actual_identities = sorted((str(UUID(str(row['record_id']))), row['content_hash'])
                                   for row in actual)
        if (actual_identities != identities
                or any(sha256(canonical_json(_canonical_typed_content(name,
                    {key: value for key, value in row.items() if key != 'content_hash'},
                    stored_utc=True)).encode()).hexdigest() != row['content_hash']
                    for row in actual)):
            raise RuntimeError('Strategy 1 source revision differs from its committed detail')
    return rebuilt, entry.intent
