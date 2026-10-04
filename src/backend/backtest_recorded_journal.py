"""Read sealed recorded results without re-running strategy/source certification.

This verifies the complete stored normalized journal and its contiguous commit
chain. It grants neither execution admission nor recovery/write authority.
The full strategy-source auditor remains a separate, unchanged reader.
"""
from collections import defaultdict
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
import re
from threading import Lock
from uuid import UUID

from src.backend.typed_backtest_review_core import AuditedSessionCache, _cache_key, _client_scope, _head_matches
from src.trading_runtime.arte_journal_commit_v4 import V4CommittedPrefix, verify_commit_v4, _batched_detail_rows_v4
from src.trading_runtime.arte_journal_writer import _CONTRACTS, _literal, _rows, _canonical_typed_content, load_typed_run_context
from src.trading_runtime.journal_contract import canonical_json

_CACHE = AuditedSessionCache(max_sessions=64, max_bytes=8*1024*1024, max_entry_bytes=512*1024)
_REPORTS = AuditedSessionCache(max_sessions=64, max_bytes=16*1024*1024, max_entry_bytes=2*1024*1024)
_LOCK = Lock()
_SCOPES = {}


@contextmanager
def _recorded_read_scope(scope, run_id):
    key = (scope, run_id)
    with _LOCK:
        lock, users = _SCOPES.get(key, (Lock(), 0))
        _SCOPES[key] = (lock, users+1)
    try:
        with lock:
            yield
    finally:
        with _LOCK:
            _, users = _SCOPES[key]
            if users == 1:
                del _SCOPES[key]
            else:
                _SCOPES[key] = (lock, users-1)


def verify_recorded_chain(commits, families, rows_by_family, run_id):
    """Independently hash every persisted scalar; validate every family seal."""
    by_batch = defaultdict(list)
    details = defaultdict(lambda: defaultdict(list))
    for family in families:
        by_batch[str(family['batch_id'])].append(family)
    for name, rows in rows_by_family.items():
        for row in rows:
            content = {key: value for key, value in row.items() if key != 'content_hash'}
            digest = sha256(canonical_json(_canonical_typed_content(name, content, stored_utc=True)).encode()).hexdigest()
            if row['run_id'] != run_id or row['content_hash'] != digest:
                raise ValueError('Recorded journal detail differs from its scalar seal')
            details[str(row['batch_id'])][name].append((str(UUID(row['record_id'])), digest))
    previous, sequence, status, ids = str(UUID(int=0)), 0, 'running', []
    events_by_batch = defaultdict(list)
    for event in rows_by_family.get('trading_event_v1', []):
        events_by_batch[str(event['batch_id'])].append(event)
    if not commits:
        raise ValueError('Saved run has no committed journal')
    for commit in commits:
        identity = str(UUID(commit['batch_id']))
        if (commit['run_id'] != run_id or commit['run_month'] != commits[0]['run_month']
                or str(commit['prior_batch_id']) != previous or commit['first_sequence'] != sequence+1
                or status != 'running' or identity in ids
                or not isinstance(commit['source_cursor'], str) or not commit['source_cursor']
                or commit['source_cursor'].lstrip().startswith(('{', '['))
                or commit['status'] not in {'running', 'completed', 'stopped', 'failed'}):
            raise ValueError('Recorded journal chain is forked or not contiguous')
        verify_commit_v4(commit, by_batch.pop(identity, []), details.pop(identity, {}))
        if sorted(event['sequence'] for event in events_by_batch.pop(identity, [])) != list(range(commit['first_sequence'], commit['last_sequence']+1)):
            raise ValueError('Recorded journal batch event sequences differ from its commit')
        previous, sequence, status = identity, commit['last_sequence'], commit['status']
        ids.append(identity)
    if by_batch or details or status not in {'completed', 'stopped', 'failed'}:
        raise ValueError('Recorded journal has unfenced rows or is not terminal')
    events = rows_by_family.get('trading_event_v1', [])
    if sorted(row['sequence'] for row in events) != list(range(1, sequence+1)):
        raise ValueError('Recorded journal event sequences are incomplete or repeated')
    return V4CommittedPrefix(run_id, sequence, previous, commits[-1]['source_cursor'], status, tuple(ids))


def load_recorded_attestation(client, run_id):
    from src.backend import historical_runtime_versions as versions
    from src.trading_runtime.numbered_fixed_strategy import is_numbered_fixed_strategy
    from src.trading_runtime.arte_backtest_snapshot_anchor import load_terminal_backtest_snapshot
    from src.trading_runtime.arte_backtest_definition import load_committed_initial_cash
    from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
    from src.backend.backtest_v4_saved_review import _terminal_financial_accounts
    normalized = str(UUID(run_id))
    if versions.backend_source_fingerprint() != versions.LOADED_BACKEND_FINGERPRINT:
        raise RuntimeError('Saved journal reader source changed after startup; restart the backend')
    context = load_typed_run_context(client, normalized)
    if context['mode'] != 'backtest' or not is_numbered_fixed_strategy(context['strategy_id'], int(context['strategy_revision'])):
        raise ValueError('Recorded Review requires an installed numbered Backtest strategy')
    with _recorded_read_scope(_client_scope(client), normalized):
        for key in _CACHE.candidate_keys(_client_scope(client), normalized):
            cached = _CACHE.get(key)
            if cached and cached['context'] == context and _head_matches(client, normalized, cached['prefix']):
                return cached
        columns = ','.join(name for name, _ in _CONTRACTS['trading_commit_v4'].columns)
        commits = _rows(client, f'SELECT {columns} FROM arte.trading_commit_v4 WHERE run_id={_literal(normalized)} '
                        'ORDER BY first_sequence,batch_id LIMIT 10001 FORMAT JSONEachRow')
        if not commits or len(commits)>10000:
            raise ValueError('Recorded journal commit inventory is absent or exceeds the interactive budget')
        batch_ids = ','.join(f'toUUID({_literal(str(UUID(row["batch_id"])))})' for row in commits)
        filters = f'WHERE run_id={_literal(normalized)} AND batch_id IN ({batch_ids}) '
        families = _rows(client, f'SELECT * FROM arte.trading_commit_family_v4 {filters}LIMIT 200001 FORMAT JSONEachRow')
        if len(families)>200000:
            raise ValueError('Recorded journal family inventory exceeds the interactive budget')
        counts = defaultdict(int)
        for row in families:
            name, count = row['family_name'], row['row_count']
            if (name not in _CONTRACTS or name in {'trading_commit_v4', 'trading_commit_family_v4'}
                    or not {'record_id', 'content_hash', 'run_id', 'batch_id'} <= {column for column, _ in _CONTRACTS[name].columns}
                    or type(count) is not int or not 1<=count<=65536):
                raise ValueError('Recorded journal names an invalid family')
            counts[name] += count
        if sum(counts.values())>200000:
            raise ValueError('Recorded journal details exceed the interactive budget')
        from src.trading_runtime.arte_rising_momentum_entry_v4 import MOMENTUM, momentum_select_columns, decode_momentum_row
        from src.trading_runtime.arte_initial_momentum_entry_v4 import INITIAL_MOMENTUM, initial_momentum_select_columns, decode_initial_momentum_row
        special = {MOMENTUM.name: (momentum_select_columns, decode_momentum_row),
                   INITIAL_MOMENTUM.name: (initial_momentum_select_columns, decode_initial_momentum_row)}
        specs = tuple((name, tuple(column for column, _ in _CONTRACTS[name].columns), count)
                      for name, count in counts.items() if name not in special)
        rows = _batched_detail_rows_v4(client, specs, filters)
        for name, count in counts.items():
            if name in special:
                columns_fn, decoder = special[name]
                rows[name] = [decoder(row) for row in _rows(client, f'SELECT {columns_fn()} FROM arte.{name} {filters}LIMIT {count+1} FORMAT JSONEachRow')]
        prefix = verify_recorded_chain(commits, families, rows, normalized)
        cursor = load_latest_backtest_cursor(client, prefix)
        if (cursor is None and prefix.source_cursor != 'start' or cursor is not None and (
                str(cursor['session_date']) != str(context['session_date']) or prefix.source_cursor != f"{cursor['session_date']}:{int(cursor['boundary_ms'])}")):
            raise ValueError('Recorded journal cursor differs from its terminal commit')
        accounts = {account: load_terminal_backtest_snapshot(client, prefix, account_id=account)
                    for account in context['account_ids']}
        value = dict(context=context, prefix=prefix, cursor=cursor,
            initial_cash=load_committed_initial_cash(client, normalized, run_context=context),
            financial_accounts=_terminal_financial_accounts(client, prefix, tuple(context['account_ids'])),
            accounts={account: {key: snapshot[key] for key in ('state_hash', 'state_revision', 'snapshot_at')}
                      for account, snapshot in accounts.items()})
        if not _head_matches(client, normalized, prefix):
            raise ValueError('Recorded journal terminal head changed during read')
        _CACHE.put(_cache_key(client, normalized, context, prefix), value)
        return value


def load_recorded_page(client, run_id):
    attestation = load_recorded_attestation(client, run_id)
    prefix = attestation['prefix']
    return dict(schema_version='backtest-v4-recorded-journal-page-v1', journal_only=True,
        run={**attestation['context'], 'initial_cash': attestation['initial_cash']},
        status=prefix.status, verified_sequence=prefix.last_sequence,
        market_cursor=attestation['cursor'], market_cursor_verified=attestation['cursor'] is not None,
        limitations=[], financial_accounts=attestation['financial_accounts'], accounts=attestation['accounts'],
        events=[], next_sequence=0, complete=True, resume_supported=False, source_audit_status='not_requested')


# Preserve the sealed reader's financial projection using the shared canonical
# performance engine; only the attestation entry point differs.
from src.backend.backtest_v4_saved_review import _complete_detail_rows, _saved_protection_events, _utc_timestamp
from src.trading_runtime.arte_journal_writer import load_committed_execution_page, load_committed_commission_page

def project_recorded_performance(client, run_id: str, *,
                               cache: AuditedSessionCache | None = None) -> dict:
    """Derive the existing flat-to-flat report from complete normalized facts.

    This is a read-only presentation projection, not a journal or market writer.
    Fees must be final for every fill before net P&L can be shown.
    """
    from src.trading_runtime.domain import Execution, InstrumentContract, serialize_rows
    from src.trading_runtime.performance import (
        build_performance_report, derive_position_lifecycles,
        derive_trade_episodes,
    )
    from src.trading_runtime.protection_timeline import attach_protection_timelines

    try:
        normalized = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Strategy 1 performance requires a UUID run id") from exc
    prefix = load_recorded_attestation(client, normalized)["prefix"]
    fills = _complete_detail_rows(load_committed_execution_page, client, prefix)
    fees = _complete_detail_rows(load_committed_commission_page, client, prefix)
    fee_by_execution: dict[str, dict] = {}
    for fee in fees:
        identity = str(fee["execution_id"])
        if identity not in fee_by_execution or int(fee["sequence"]) > int(fee_by_execution[identity]["sequence"]):
            fee_by_execution[identity] = fee
    executions = []
    identities = set()
    journal_sequences: set[int] = set()
    for fill in fills:
        identity = str(fill["execution_id"])
        if identity in identities:
            raise RuntimeError("Saved Canvas performance repeats an execution identity")
        identities.add(identity)
        fee = fee_by_execution.get(identity)
        if fee is None or str(fee["status"]).lower() != "final":
            raise RuntimeError("Saved Canvas performance requires final fees for every fill")
        if fee["account_id"] != fill["account_id"]:
            raise RuntimeError("Saved Canvas fee account differs from its fill")
        if fee["currency"] != fill["currency"]:
            raise RuntimeError("Saved Canvas performance requires fee and fill currency parity")
        side = {"B": "BUY", "S": "SELL", "BUY": "BUY", "SELL": "SELL"}.get(str(fill["side"]).upper())
        if side is None:
            raise RuntimeError("Saved Canvas fill has an unsupported side")
        conid = int(fill["conid"])
        if conid <= 0:
            raise RuntimeError("Saved Canvas performance requires point-in-time conid on every fill")
        symbol = str(fill["ticker"])
        stamp = _utc_timestamp(fill["source_event_time"])
        sequence = fill.get("sequence")
        if type(sequence) is not int or sequence < 1 or sequence in journal_sequences:
            raise RuntimeError("Saved Canvas fill lacks a unique committed journal sequence")
        journal_sequences.add(sequence)
        executions.append(Execution(
            execution_id=identity, account_id=str(fill["account_id"]),
            instrument=InstrumentContract(
                instrument_id=f"conid:{conid}",
                conid=conid, symbol=symbol, security_type="STK",
                currency=str(fill["currency"]), exchange=str(fill["exchange"]) or "SMART"),
            side=side, quantity=Decimal(str(fill["quantity"])),
            price=Decimal(str(fill["price"])),
            source_event_time=stamp,
            broker_order_id=str(fill["broker_order_id"]),
            client_order_id=str(fill["client_order_id"]),
            exchange=str(fill["exchange"]),
            commission=Decimal(str(fee["commission"])),
            commission_currency=str(fee["currency"]),
            commission_status="final", strategy_id=str(fill["strategy_id"]),
            strategy_revision=int(fill["strategy_revision"]),
            run_id=normalized, setup=str(fill["setup"]),
            exit_reason=str(fill["exit_reason"]),
            signal_price=(Decimal(str(fill["signal_price"]))
                          if fill["signal_price"] is not None else None),
            arrival_midpoint=(Decimal(str(fill["arrival_midpoint"]))
                              if fill["arrival_midpoint"] is not None else None),
            planned_risk=(Decimal(str(fill["planned_risk"]))
                          if fill["planned_risk"] is not None else None),
            journal_sequence=sequence,
        ))
    if set(fee_by_execution) != identities:
        raise RuntimeError("Saved Canvas contains a commission without a matching fill")
    if not _head_matches(client, normalized, prefix):
        raise RuntimeError("Saved Canvas terminal head changed during performance projection")
    episodes = derive_trade_episodes(executions)
    report = build_performance_report(episodes, executions, ())
    lifecycles = derive_position_lifecycles(executions, ())
    protection_events = _saved_protection_events(client, prefix)
    # Opening-order identities, not ticker/price coincidence, assign broker
    # protection revisions to a lifecycle. Unmatched events remain journal
    # evidence but cannot be drawn as position-specific rails.
    attach_protection_timelines(
        lifecycles, protection_events, executions, datetime.max.replace(tzinfo=UTC))
    from src.backend.backtest_v4_performance_evidence import attach_exit_evidence
    attach_exit_evidence(client, prefix, lifecycles, executions)
    if not _head_matches(client, normalized, prefix):
        raise RuntimeError("Saved Canvas terminal head changed during chart projection")
    # No order lifecycle projection has been asserted yet. Do not turn an
    # absent order reader into a false zero order count or rejection count.
    report["execution"]["order_count"] = None
    report["execution"]["rejected_order_count"] = None
    return {
        "schema_version": "strategy-one-v4-performance-report-v2",
        "run_id": normalized,
        "verified_sequence": prefix.last_sequence,
        "report": report,
        "position_lifecycles": lifecycles,
        # Position Manager is deliberately eager, independently of query tables.
        "position_executions": serialize_rows(executions),
        "fill_count": len(executions),
        "fee_count": len(fee_by_execution),
    }



def load_recorded_performance(client, run_id):
    attestation = load_recorded_attestation(client, run_id)
    key = _cache_key(client, str(UUID(run_id)), attestation['context'], attestation['prefix'])
    cached = _REPORTS.get(key)
    if cached is not None:
        return cached['report']
    result = project_recorded_performance(client, run_id)
    result['journal_only'] = True
    try:
        _REPORTS.put(key, {'report': result})
    except ValueError:
        pass
    return result


def load_recorded_chart_trades(client, run_id: str, ticker: str) -> dict:
    """Bounded ticker projection of the verified terminal performance report.

    A chart needs only position markers and effective protection rails, not
    every execution, fee, episode, and journal-derived diagnostic for the run.
    Keep the full-prefix/head check in the shared report reader before pruning.
    """
    symbol = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.-]{1,24}", symbol):
        raise ValueError("Saved chart trade ticker is invalid")
    page = load_recorded_performance(client, run_id)
    from src.trading_runtime.arte_intent_projection import (
        load_committed_strategy_intent_page,
    )
    attestation = load_recorded_attestation(client, str(UUID(run_id)))
    prefix = attestation["prefix"]
    if int(page["verified_sequence"]) != prefix.last_sequence:
        raise RuntimeError("Saved chart intent head differs from performance report")
    intents = []
    after_sequence = 0
    while True:
        batch = load_committed_strategy_intent_page(
            client, prefix, after_sequence=after_sequence, limit=500)
        if not batch:
            break
        for recovered in batch:
            intent = recovered.intent
            if intent.ticker.upper() != symbol or intent.action not in {
                    "enter_long", "enter_short", "exit", "reduce_long",
                    "reduce_short", "take_profit", "cover"}:
                continue
            intents.append({
                "sequence": recovered.sequence,
                "account_id": recovered.account_id,
                "action": intent.action,
                "event_time": intent.event_time.isoformat(),
                "reference_price": intent.reference_price,
                "reason": intent.reason,
            })
        after_sequence = batch[-1].sequence
        if len(batch) < 500:
            break
    lifecycles = []
    for row in page["position_lifecycles"]:
        instrument = row.get("instrument")
        if not isinstance(instrument, dict) or not isinstance(instrument.get("symbol"), str):
            raise RuntimeError("Saved chart lifecycle lacks an instrument identity")
        if instrument["symbol"].upper() != symbol:
            continue
        required = ("episode_id", "opened_at", "entry_price", "side",
                    "quantity", "status", "protection_timeline")
        if any(key not in row for key in required):
            raise RuntimeError("Saved chart lifecycle lacks position evidence")
        rails = []
        for event in row["protection_timeline"]:
            if event.get("phase") != "effective" or event.get("kind") not in {"stop", "target"}:
                continue
            keys = ("event_time", "sequence", "order_id", "kind",
                    "phase", "price", "active")
            if any(key not in event for key in keys):
                raise RuntimeError("Saved chart protection rail lacks typed evidence")
            rails.append({key: event[key] for key in keys})
        lifecycles.append({
            "episode_id": row["episode_id"],
            "instrument": {"symbol": instrument["symbol"]},
            **{key: row.get(key) for key in (
                "account_id", "requested_at", "opened_at", "entry_price", "closed_at", "exit_price",
                "side", "quantity", "current_quantity", "status", "exit_reason",
                "presentation_exit_reason", "net_pnl")},
            "protection_timeline": rails,
        })
    if not _head_matches(client, str(UUID(run_id)), prefix):
        raise RuntimeError("Saved chart journal head changed during intent read")
    return {"schema_version": "strategy-one-v4-chart-trades-v1",
            "run_id": page["run_id"], "ticker": symbol,
            "verified_sequence": page["verified_sequence"],
            "position_lifecycles": lifecycles, "issued_intents": intents}
