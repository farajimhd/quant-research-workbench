"""Complete cold verification before liquidity evidence can enter publication.

This composes existing native readers; it grants no installed release, writer
or order admission. Callers must independently certify the supplied market plan
and preceding prefix. Supplied financial views are claims to check, not proof.
"""
from collections.abc import Mapping
from datetime import datetime, timezone
from types import MappingProxyType
from uuid import NAMESPACE_URL, UUID, uuid5
from zoneinfo import ZoneInfo

from .arte_liquidity_fade_failure_v4 import restore_liquidity_fade_failure
from .strategy_liquidity_fade_exit import REASON
from .strategy_liquidity_fade_financial_checkpoint import load_liquidity_fade_financial_checkpoint
from .strategy_liquidity_fade_checkpoint import load_liquidity_fade_manager_checkpoint
from .strategy_liquidity_fade_market_source import load_liquidity_fade_market_observations


def validate_liquidity_fade_publication_rows(
    client, rows, intents, events, *, verified_prefix, market_plan,
    financial_views, first_price_source=None,
):
    """Return immutable scalar rows only after every independent check passes.

    The view mapping uses parent record IDs and must match this family exactly.
    Quantity and pending exits are checked against native broker/OMS rows before
    the manager reader binds persisted first-held time to the original entry.
    Market reads then verify four completed bars, MACD and the current quote.
    Checks run on cold publication/recovery, never per 100ms decision.
    """
    if (any(type(family) is not tuple for family in (rows, intents, events))
            or max(len(rows), len(intents), len(events)) > 65_536
            or not isinstance(financial_views, Mapping)):
        raise ValueError('Liquidity publication requires bounded immutable family inputs')
    parents = {str(p['record_id']): p for p in intents if p['reason'] == REASON}
    event_map = {str(e['record_id']): e for e in events}
    if (len({str(p['record_id']) for p in intents}) != len(intents)
            or len(event_map) != len(events) or len(rows) != len(parents)
            or set(financial_views) != set(parents)):
        raise ValueError('Liquidity publication has missing, extra or duplicate parents/views')
    # Preflight the entire graph before any cold read. Never silently accept a
    # valid subset when a later child is missing, duplicated or malformed.
    seen, records, pending = set(), set(), []
    for row in rows:
        witness = restore_liquidity_fade_failure(row)
        parent_id, record_id = str(row['parent_record_id']), str(row['record_id'])
        expected_id = str(uuid5(NAMESPACE_URL, f"{row['run_id']}:{parent_id}:liquidity-fade-failure"))
        if (parent_id in seen or record_id in records or parent_id not in parents
                or parent_id not in event_map or str(UUID(record_id)) != expected_id):
            raise ValueError('Liquidity publication lacks a unique deterministic exit child')
        seen.add(parent_id)
        records.add(record_id)
        at = datetime.fromisoformat(str(event_map[parent_id]['event_time']).replace('Z', '+00:00'))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        day = at.astimezone(ZoneInfo('America/New_York')).date()
        pending.append((row, witness, parents[parent_id], event_map[parent_id],
                        financial_views[parent_id], day))
    result = []
    for row, witness, parent, event, financial, day in pending:
        load_liquidity_fade_financial_checkpoint(client, verified_prefix, row, parent, event,
            financial, first_price_source=first_price_source)
        load_liquidity_fade_manager_checkpoint(client, verified_prefix, row, parent, event,
            financial, first_price_source=first_price_source)
        load_liquidity_fade_market_observations(client, witness, row,
            plan=market_plan, session_date=day, ticker=parent['ticker'])
        result.append(MappingProxyType({key: value for key, value in row.items() if key != 'content_hash'}))
    return tuple(result)


def prepare_liquidity_fade_publication_rows(client, rows, intents, events, **context):
    """Hash fully checked native rows; this does not write or publish a commit.

    Cold readers must verify stored hashes before supplying persisted rows to
    this preparation function. Newly projected rows need all independent checks
    above; a typed hash alone never attests trading or market authority.
    """
    from .arte_liquidity_fade_failure_v4 import LIQUIDITY_FADE_FAILURE
    from .arte_journal_writer import typed_row
    checked = validate_liquidity_fade_publication_rows(client, rows, intents, events, **context)
    return tuple(typed_row(LIQUIDITY_FADE_FAILURE.name, row) for row in checked)


def prepare_native_liquidity_fade_rows(client, rows, intents, events, *,
                                     verified_prefix, first_price_source):
    """Use the run's existing certified source authority for native publication.

    Factory replay views describe claimed exit preconditions only. The composed
    reader independently checks their quantity and pending exits; permissions
    and order admission remain the runtime's separate responsibility.
    """
    from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority
    from .strategy_one_stateful import StrategyOneFinancialView
    from .strategy_engine import AssignmentStatus, StrategyPermissions
    parents = tuple(parent for parent in intents if parent['reason'] == REASON)
    if not rows and not parents:
        return ()
    if (type(first_price_source) is not CertifiedPriceReadbackAuthority
            or verified_prefix is None or first_price_source.run_id != verified_prefix.run_id):
        raise ValueError('Liquidity publication requires its independently certified run market authority')
    parent_map = {str(parent['record_id']): parent for parent in parents}
    views = {}
    for row in rows:
        parent_id = str(row['parent_record_id'])
        if parent_id not in parent_map:
            raise ValueError('Liquidity native publication lacks its exact exit parent')
        parent = parent_map[parent_id]
        views[parent_id] = StrategyOneFinancialView(row['assignment_id'], parent['account_id'],
            parent['ticker'], AssignmentStatus.MANAGING, StrategyPermissions(),
            float(parent['quantity']), False, False, False, 1)
    return prepare_liquidity_fade_publication_rows(client, tuple(rows), tuple(intents), tuple(events),
        verified_prefix=verified_prefix, market_plan=first_price_source.plan.source.market,
        financial_views=views, first_price_source=first_price_source)
