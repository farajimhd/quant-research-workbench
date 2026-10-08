"""Cold-verified, disk-free terminal evidence for immutable Strategy 1.

This is a bounded normalized-journal page, not a fabricated legacy Canvas
controller or a resumable execution state. JSON is only the API transport.
"""
from __future__ import annotations
from src.trading_runtime.numbered_fixed_strategy import declared_fixed_rule

from datetime import UTC, datetime
from decimal import Decimal
import re
from uuid import UUID

from src.trading_runtime.arte_backtest_snapshot_anchor import (
    load_terminal_backtest_snapshot,
)
from src.trading_runtime.arte_backtest_definition import load_committed_initial_cash
from src.trading_runtime.arte_journal_commit_v4 import load_verified_v4_prefix
from src.trading_runtime.arte_journal_projection import load_latest_backtest_cursor
from src.trading_runtime.arte_journal_reader import load_typed_event_page
from src.trading_runtime.arte_journal_writer import (
    _CONTRACTS, _committed_batch_filter, _literal, _rows, load_committed_commission_page,
    load_committed_execution_page, load_committed_order_command_page,
    load_committed_order_transition_page, load_typed_run_context,
)
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER
from src.trading_runtime.numbered_fixed_strategy import is_numbered_fixed_strategy
from src.backend.backtest_terminal_v2_fence import _verify_rows, _verify_v1_rows
from src.backend.typed_backtest_review_core import (
    AuditedSessionCache, _cache_key, _client_scope, _head_matches,
)


_V4_CACHE = AuditedSessionCache(max_sessions=8, max_bytes=8 * 1024 * 1024,
                                max_entry_bytes=512 * 1024, ttl_seconds=300)
_V4_PERFORMANCE_CACHE = AuditedSessionCache(
    max_sessions=8, max_bytes=16 * 1024 * 1024,
    max_entry_bytes=2 * 1024 * 1024, ttl_seconds=300,
)


from contextvars import ContextVar

_DECLARED_READ_SCOPE = ContextVar("declared_saved_read_scope", default=None)


class _DeclaredReadProfileRequired(Exception):
    def __init__(self, options):
        self.options = options


def _require_declared_read_profile(client, context, *, sealed_configuration=None):
    """Select authority from the already validated typed run context."""
    from src.trading_runtime.numbered_fixed_strategy import resolve_numbered_fixed_strategy
    contract = resolve_numbered_fixed_strategy(context['strategy_id'], int(context['strategy_revision']))
    from .backtest_fixed_structural_lot_configuration import declared_fixed_structural_lot_contract
    if declared_fixed_structural_lot_contract(int(context['strategy_revision'])) is not None:
        from .backtest_fixed_structural_lot_saved_runtime_source import fixed_lot_saved_read_options
        options = fixed_lot_saved_read_options(client, context, sealed_configuration)
        if options and _DECLARED_READ_SCOPE.get() is not None:
            raise _DeclaredReadProfileRequired(options)
        return options
    if (getattr(contract, 'automatic_entry_policy', None) is not None
            or getattr(contract, 'confirmed_original_risk_policy', None) is not None):
        from src.trading_runtime.squeeze_ladder_geometry import declared_ladder_runner_options
        if sealed_configuration is None:
            from contextlib import closing
            from src.backend.backtest_market_data import readonly_clickhouse_client
            from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
            with closing(readonly_clickhouse_client(v3_read_principal=True)) as reader:
                sealed_configuration = certify_numbered_configuration(reader, int(context['strategy_revision']))
        if (sealed_configuration.payload_hash != context['configuration_hash']
                or sealed_configuration.strategy_number != int(context['strategy_revision'])
                or sealed_configuration.payload['strategy']['strategy_id'] != context['strategy_id']):
            raise ValueError('Saved ladder read profile differs from sealed run configuration')
        if getattr(contract, 'confirmed_original_risk_policy', None) is not None:
            from src.trading_runtime.original_risk_diagnostic_profile import declared_fixed_runner_options
            options=declared_fixed_runner_options(sealed_configuration.payload)
        else:
            options = declared_ladder_runner_options(sealed_configuration.payload)
    else:
        options = ({'entry_spread_risk': True}
               if getattr(contract, 'entry_spread_risk_policy', None) is not None else {})
    flag = ('automatic_ladder_profile' if 'automatic_ladder' in options
            else 'entry_spread_risk_profile' if options else None)
    matches = (flag is not None and getattr(client, flag, False) is True
               and (flag != 'automatic_ladder_profile' or getattr(client, 'ladder_geometry_policy', None)
                    == options.get('ladder_geometry_policy')))
    if 'confirmed_original_risk_policy' in options:
        flag='confirmed_original_risk_policy'
        actual=getattr(client,flag,None)
        if actual is not None and actual!=options[flag]:
            raise ValueError('Saved original-risk reader carries a foreign typed policy')
        from src.trading_runtime.original_risk_diagnostic_profile import validate_original_risk_profile
        validate_original_risk_profile(getattr(client,'automatic_ladder_profile',False),
            getattr(client,'entry_spread_risk_profile',False),actual)
        matches=actual==options[flag]
    if flag is not None and not matches:
        if _DECLARED_READ_SCOPE.get() is not None:
            raise _DeclaredReadProfileRequired(options)
    return options if flag is not None and not matches else {}


def declared_saved_read_operation(operation):
    """Keep the selected SELECT-only reader alive through the complete read."""
    from contextlib import closing
    from functools import wraps
    from inspect import signature
    parameters = signature(operation)
    client_name = next(iter(parameters.parameters))

    @wraps(operation)
    def selected(*args, **kwargs):
        bound = parameters.bind(*args, **kwargs)
        client = bound.arguments[client_name]
        raw_run_id = str(bound.arguments['run_id'])
        try:
            scope_run_id = str(UUID(raw_run_id))
        except ValueError:
            # Leave input rejection to the original operation.
            scope_run_id = raw_run_id
        scope = (id(client), scope_run_id)
        if _DECLARED_READ_SCOPE.get() == scope:
            return operation(*args, **kwargs)
        token = _DECLARED_READ_SCOPE.set(scope)
        try:
            try:
                return operation(*args, **kwargs)
            except _DeclaredReadProfileRequired as required:
                from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env, _v4_preflight
                with closing(backtest_v4_operator_client_from_env(**required.options)) as reader:
                    _v4_preflight(reader)
                    wrap = getattr(client, 'declared_read_wrapper', None)
                    bound.arguments[client_name] = wrap(reader) if callable(wrap) else reader
                    selected_token = _DECLARED_READ_SCOPE.set((id(bound.arguments[client_name]), scope[1]))
                    try:
                        return operation(*bound.args, **bound.kwargs)
                    finally:
                        _DECLARED_READ_SCOPE.reset(selected_token)
        finally:
            _DECLARED_READ_SCOPE.reset(token)
    return selected



def _saved_twenty_price_source(client, run_id: str, context: dict, release, *, fixed_lot_session=False):
    """Rebuild native entry authority from the fenced definition and market seals.

    Journal entry values never supply native prices. This work runs only on a
    cold attestation; the bounded cache retains scalar financial evidence only.
    """
    from contextlib import closing
    from datetime import date, time, timedelta
    from src.trading_runtime.arte_backtest_definition import (
        load_backtest_definition, reconstruct_saved_review_definition_from_arte,
    )
    from src.backend.backtest_saved_source_authority import certify_saved_review_source
    from src.backend.replay_run_service import backtest_preflight
    from src.backend.backtest_market_data import (
        certified_market_plan_from_arte, configuration_tickers,
        readonly_clickhouse_client,
    )
    from src.backend.backtest_liquidity_price import certify_price_level_plan
    from src.backend.backtest_strategy_one_plan import certify_strategy_one_fixed_plans
    from src.backend.backtest_strategy_one_static_gate import compile_static_entry_gate
    from src.backend.backtest_strategy_rising_momentum import load_rising_momentum_plan
    from src.backend.backtest_strategy_initial_momentum_growth import compile_initial_momentum_growth_plan
    from src.backend.backtest_strategy_first_price_source import load_first_price_source
    from src.backend.backtest_strategy_certified_price_break import (
        CertifiedPriceReadbackAuthority, compile_certified_price_break_plan,
    )
    from src.backend.backtest_strategy_one_candidate_store import project_candidate_plan

    def local_clock(value):
        if type(value) is not int or not 0 <= value < 86_400_000:
            raise ValueError("Saved native source has an invalid session clock")
        hour, remainder = divmod(value, 3_600_000)
        minute, remainder = divmod(remainder, 60_000)
        second, remainder = divmod(remainder, 1_000)
        return time(hour, minute, second, remainder * 1_000)

    saved = load_backtest_definition(client, run_id, run_context=context)
    parent = saved["definition"]
    revision = release.revision()
    source_authority = certify_saved_review_source(context, revision)
    preflight = backtest_preflight(
        anchor_date=date.fromisoformat(context["session_date"]) + timedelta(days=1),
        session_count=1, start_time=local_clock(parent["start_local_ms"]),
        end_time=local_clock(parent["end_local_ms"]),
        initial_cash=float(parent["initial_cash"]),
        tickers=tuple(row["ticker"] for row in saved["tickers"]),
        configuration_revision=revision,
        experimental_structure_book=parent["structure_book"],
        _saved_review_authority=source_authority,
    )
    definition = reconstruct_saved_review_definition_from_arte(
        saved, context, revision, preflight, source_authority=source_authority)
    pins = definition.market_data_plan
    market = certified_market_plan_from_arte(
        sessions=[date.fromisoformat(value) for value in pins["sessions"]],
        tickers=configuration_tickers(release.payload, definition.tickers),
        configuration=release.payload,
    )
    if market.token != pins["token"]:
        raise ValueError("Saved native market plan differs from its fenced definition")

    def reader():
        return readonly_clickhouse_client(market_stream=True, v3_read_principal=True)

    with closing(reader()) as source_client:
        prices = certify_price_level_plan(
            market, source_client, read_client_factory=reader)
    if prices.token != pins["price_level_plan_token"]:
        raise ValueError("Saved native passive-fill plan changed")
    fixed = certify_strategy_one_fixed_plans(
        market, prices, market_pins=pins,
        v7_pins=definition.causal_v7_plan, client_factory=reader)
    if fixed_lot_session:
        from .backtest_fixed_structural_lot_configuration import declared_fixed_structural_lot_contract
        if declared_fixed_structural_lot_contract(release.strategy_number) is None:
            raise ValueError('Saved fixed-lot session requires its declared contract')
        from .backtest_fixed_structural_lot_execution_v11 import prepare_fixed_structural_lot_session
        return prepare_fixed_structural_lot_session(plans=fixed, number=release.strategy_number,
            run_id=run_id, session_date=date.fromisoformat(context['session_date']),
            market=market, candidates=fixed.candidates, entry=fixed.entry, seeds=fixed.seeds,
            through_boundary_ms=parent['end_local_ms'] - 14_400_000, client_factory=reader)
    visible = project_candidate_plan(
        fixed.candidates, through_boundary_ms=parent["end_local_ms"] - 14_400_000)
    if not visible.prepared:
        # Preserve the execution runner's sealed empty-prefix behavior.
        # Prefix verification will reject any entry in this empty horizon.
        return None
    base_gate = compile_static_entry_gate(visible, fixed.entry, strategy_number=12)
    with closing(reader()) as source_client:
        momentum = load_rising_momentum_plan(
            market, visible, client=source_client,
            candidate_indices=base_gate.eligible_indices)
        if (release.strategy_number in (26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(release.strategy_number, 'strategy-twenty-six-premarket-first-setup-ten-second-growth-10pct-v1')):
            from src.backend.backtest_strategy_initial_ten_percent import compile_initial_ten_percent_plan
            from src.trading_runtime.entry_momentum_growth import declared_momentum_policy
            policy = declared_momentum_policy(release.strategy_number)
            if policy is not None:
                from src.backend.backtest_declared_initial_momentum import compile_declared_initial_momentum_plan
                initial = compile_declared_initial_momentum_plan(visible, fixed.entry, momentum, policy)
            else:
                initial = compile_initial_ten_percent_plan(visible, fixed.entry, momentum)
        else:
            initial = compile_initial_momentum_growth_plan(visible, fixed.entry, momentum)
        source = load_first_price_source(market, initial, client=source_client)
    plan = compile_certified_price_break_plan(source)
    activity = None
    if (release.strategy_number in (36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(release.strategy_number, 'strategy-thirty-six-completed-entry-activity-fade-v1')):
        from src.backend.backtest_strategy_entry_activity_source import (
            load_entry_activity_plan, EntryActivityReadbackAuthority,
        )
        # Rebuild from the sealed native bars, never saved strategy claims.
        with closing(reader()) as source_client:
            activity_plan = load_entry_activity_plan(market, plan, client=source_client)
        if (release.strategy_number in (37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(release.strategy_number, 'strategy-thirty-seven-confirmed-episode-activity-veto-v1')):
            from src.backend.backtest_strategy_episode_activity_gate import compile_episode_activity_static_gate
            from src.backend.backtest_strategy_episode_activity_source import EpisodeActivityReadbackAuthority
            # Reconstruct the full original prefix from certified native inputs.
            activity = EpisodeActivityReadbackAuthority(run_id,
                compile_episode_activity_static_gate(activity_plan), release.strategy_number)
        else:
            activity = EntryActivityReadbackAuthority(run_id, activity_plan)
    from src.trading_runtime.numbered_fixed_strategy import numbered_fixed_strategy
    cost_policy = numbered_fixed_strategy(release.strategy_number).entry_spread_risk_policy
    cost_authority = None
    if cost_policy is not None:
        from src.backend.backtest_declared_entry_quote_source import load_declared_entry_spread_risk_plan, declared_entry_spread_risk_authority
        with closing(reader()) as source_client:
            cost_plan = load_declared_entry_spread_risk_plan(market, activity.gate, cost_policy, source_contract=numbered_fixed_strategy(release.strategy_number).entry_spread_risk_quote_source_contract, client=source_client)
        cost_authority = declared_entry_spread_risk_authority(run_id, cost_plan, release.strategy_number)
    return CertifiedPriceReadbackAuthority(run_id, plan, activity, cost_authority)


def _terminal_financial_accounts(client, prefix, account_ids: tuple[str, ...]) -> dict:
    table = "trading_backtest_account_snapshot_v2"
    columns = ",".join(name for name, _ in _CONTRACTS[table].columns)
    rows = _rows(client,
        f"SELECT {columns} FROM arte.{table} "
        f"WHERE run_id={_literal(prefix.run_id)} "
        f"AND batch_id=toUUID({_literal(prefix.last_batch_id)}) "
        f"LIMIT {len(account_ids) + 1} FORMAT JSONEachRow")
    # JSONEachRow renders an integral Float64 (notably 0.0) as JSON 0. Its
    # Python representation is int even though the column authority is Float64.
    float_fields = tuple(name for name, kind in _CONTRACTS[table].columns
                         if kind == "Float64")
    canonical_rows = []
    for row in rows:
        if any(type(row.get(name)) not in (int, float) for name in float_fields):
            raise RuntimeError("Saved review financial Float64 wire value is invalid")
        canonical_rows.append({**row, **{
            name: float(row[name]) for name in float_fields
        }})
    verified = _verify_rows(table, tuple(canonical_rows))
    if (len(verified) != len(account_ids)
            or {row["account_id"] for row in verified} != set(account_ids)
            or any(row["run_id"] != prefix.run_id
                   or row["batch_id"] != prefix.last_batch_id for row in verified)):
        raise RuntimeError("Saved review terminal financial accounts differ from run")
    return {row["account_id"]: {
        key: row[key] for key in (
            "source_timestamp_ms", "currency", "net_liquidation",
            "total_cash_value", "buying_power", "gross_position_value",
            "available_funds", "excess_liquidity", "expected_position_count",
        )
    } for row in verified}


def _terminal_attestation(client, normalized: str,
                          cache: AuditedSessionCache | None) -> dict:
    """Share one cold-audited V4 terminal head across bounded Canvas reads."""
    selected_cache = cache if cache is not None else _V4_CACHE
    context = load_typed_run_context(client, normalized)
    if (context["mode"] != "backtest"
            or not is_numbered_fixed_strategy(context["strategy_id"], int(context["strategy_revision"]))
            or context["evaluation_interval_ms"] != 100):
        raise ValueError("Saved review accepts only installed immutable numbered strategies at 100 ms")
    release = None
    if int(context["strategy_revision"]) != 1:
        from contextlib import closing
        from src.backend.backtest_market_data import readonly_clickhouse_client
        from src.backend.backtest_strategy_one_configuration import certify_numbered_configuration
        with closing(readonly_clickhouse_client(v3_read_principal=True)) as market:
            release = certify_numbered_configuration(market, int(context["strategy_revision"]))
        if release.payload_hash != context["configuration_hash"]:
            raise ValueError("Saved numbered configuration differs from its sealed release")
    options = _require_declared_read_profile(client, context, sealed_configuration=release)
    if options:
        from contextlib import closing
        from src.trading_runtime.arte_journal_writer import backtest_v4_operator_client_from_env, _v4_preflight
        with closing(backtest_v4_operator_client_from_env(**options)) as reader:
            _v4_preflight(reader)
            return _terminal_attestation(reader, normalized, cache)
    attestation = None
    for key in selected_cache.candidate_keys(_client_scope(client), normalized):
        candidate = selected_cache.get(key)
        if (candidate is not None and "initial_cash" in candidate
                and candidate["context"] == context
                and _head_matches(client, normalized, candidate["prefix"])):
            attestation = candidate
            break
    if attestation is None:
        from src.backend.backtest_ladder_source_authority import (
            declared_ladder_policy, DeclaredLadderSourceAuthority,
        )
        ladder = (declared_ladder_policy(release)
                  if int(context['strategy_revision']) != 1 else None)
        if getattr(client, 'fixed_structural_lot_profile', None) is not None:
            from .backtest_fixed_structural_lot_saved_runtime_source import load_fixed_lot_saved_prefix
            prefix = load_fixed_lot_saved_prefix(client, normalized, context, release)
        elif ladder is not None:
            sources = DeclaredLadderSourceAuthority.from_run(client, normalized)
            prefix = load_verified_v4_prefix(client, normalized, automatic_ladder_sources=sources)
        elif (int(context["strategy_revision"]) in (20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 46, 47, 48, 50, 52, 53, 54, 55, 56, 57, 58, 59, 60, 61) or declared_fixed_rule(int(context["strategy_revision"]), 'strategy-twenty-premarket-first-completed-one-second-price-break-v1')):
            source = _saved_twenty_price_source(client, normalized, context, release)
            prefix = load_verified_v4_prefix(
                client, normalized, first_price_source=source)
        else:
            prefix = load_verified_v4_prefix(client, normalized)
        if prefix is None or prefix.status not in {"completed", "stopped", "failed"}:
            raise ValueError("Saved review requires a cold-verified terminal V4 run")
        if ladder is not None:
            sources.verify_immutable_prefix(prefix)
        accounts = {
            account_id: load_terminal_backtest_snapshot(
                client, prefix, account_id=account_id)
            for account_id in context["account_ids"]
        }
        cursor = load_latest_backtest_cursor(client, prefix)
        if (cursor is None and prefix.source_cursor != "start"
                or cursor is not None and (
                    str(cursor["session_date"]) != str(context["session_date"])
                    or prefix.source_cursor !=
                    f"{cursor['session_date']}:{int(cursor['boundary_ms'])}")):
            raise RuntimeError("Saved review market cursor differs from terminal run")
        if not _head_matches(client, normalized, prefix):
            raise RuntimeError("Saved review terminal head changed during audit")
        attestation = {
            "context": context, "prefix": prefix, "cursor": cursor,
            "initial_cash": load_committed_initial_cash(
                client, normalized, run_context=context),
            "financial_accounts": _terminal_financial_accounts(
                client, prefix, tuple(context["account_ids"])),
            "accounts": {
                account_id: {
                    "state_hash": snapshot["state_hash"],
                    "state_revision": snapshot["state_revision"],
                    "snapshot_at": snapshot["snapshot_at"],
                }
                for account_id, snapshot in accounts.items()
            },
        }
        selected_cache.put(_cache_key(client, normalized, context, prefix),
                           attestation)
    return attestation


@declared_saved_read_operation
def load_v4_terminal_review_page(client, run_id: str, *,
                                 after_sequence: int = 0,
                                 limit: int = 250,
                                 metadata_only: bool = False,
                                 cache: AuditedSessionCache | None = None) -> dict:
    """Cold-audit once; recheck the terminal head before each bounded page."""
    try:
        normalized = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Strategy 1 review requires a UUID run id") from exc
    if (type(after_sequence) is not int or after_sequence < 0
            or type(limit) is not int or not 1 <= limit <= 1000
            or cache is not None and not isinstance(cache, AuditedSessionCache)):
        raise ValueError("Strategy 1 review page bounds are invalid")
    attestation = _terminal_attestation(client, normalized, cache)
    context = attestation["context"]
    prefix = attestation["prefix"]
    cursor = attestation["cursor"]
    if after_sequence > prefix.last_sequence:
        raise ValueError("Saved review cursor exceeds the verified journal")
    page = (() if metadata_only else load_typed_event_page(
        client, prefix, after_sequence=after_sequence, limit=limit))
    if not _head_matches(client, normalized, prefix):
        raise RuntimeError("Saved review terminal head changed during page read")
    next_sequence = int(page[-1].event["sequence"]) if page else after_sequence
    return {
        "schema_version": "strategy-one-v4-terminal-review-page-v1",
        "run": {**context, "initial_cash": attestation["initial_cash"]},
        "status": prefix.status,
        "verified_sequence": prefix.last_sequence,
        "market_cursor": cursor,
        "market_cursor_verified": cursor is not None,
        "limitations": (["This archived V4 run has no persisted market-boundary cursor; "
                         "its exact processed-through clock is unavailable."]
                        if cursor is None else []),
        "accounts": attestation["accounts"],
        "financial_accounts": attestation["financial_accounts"],
        "events": tuple({
            "event": row.event,
            "detail_family": row.detail_family,
            "detail": row.detail,
        } for row in page),
        "next_sequence": next_sequence,
        "complete": next_sequence == prefix.last_sequence,
        "resume_supported": False,
    }


@declared_saved_read_operation
def load_v4_trade_history_page(client, run_id: str, *,
                               after_fill_sequence: int = 0,
                               after_commission_sequence: int = 0,
                               limit: int = 250,
                               cache: AuditedSessionCache | None = None) -> dict:
    """Read real fills and fee revisions for the certified Canvas, never legacy state."""
    try:
        normalized = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Strategy 1 trade history requires a UUID run id") from exc
    if (type(after_fill_sequence) is not int or after_fill_sequence < 0
            or type(after_commission_sequence) is not int
            or after_commission_sequence < 0
            or type(limit) is not int or not 1 <= limit <= 1000
            or cache is not None and not isinstance(cache, AuditedSessionCache)):
        raise ValueError("Strategy 1 trade history page bounds are invalid")
    attestation = _terminal_attestation(client, normalized, cache)
    prefix = attestation["prefix"]
    if max(after_fill_sequence, after_commission_sequence) > prefix.last_sequence:
        raise ValueError("Trade history cursor exceeds the verified journal")
    fills = load_committed_execution_page(
        client, prefix, after_sequence=after_fill_sequence, limit=limit)
    commissions = load_committed_commission_page(
        client, prefix, after_sequence=after_commission_sequence, limit=limit)
    if not _head_matches(client, normalized, prefix):
        raise RuntimeError("Trade history terminal head changed during page read")
    return {
        "schema_version": "strategy-one-v4-trade-history-page-v1",
        "run_id": normalized,
        "status": prefix.status,
        "verified_sequence": prefix.last_sequence,
        "fills": fills,
        "commissions": commissions,
        "next_fill_sequence": (int(fills[-1]["sequence"])
                               if fills else after_fill_sequence),
        "next_commission_sequence": (int(commissions[-1]["sequence"])
                                     if commissions else after_commission_sequence),
        "complete": len(fills) < limit and len(commissions) < limit,
    }


@declared_saved_read_operation
def load_v4_order_history_page(client, run_id: str, *,
                               after_command_sequence: int = 0,
                               after_transition_sequence: int = 0,
                               limit: int = 250,
                               cache: AuditedSessionCache | None = None) -> dict:
    """Read committed order commands and transitions without inventing state."""
    try:
        normalized = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Strategy 1 order history requires a UUID run id") from exc
    if (type(after_command_sequence) is not int or after_command_sequence < 0
            or type(after_transition_sequence) is not int
            or after_transition_sequence < 0
            or type(limit) is not int or not 1 <= limit <= 1000):
        raise ValueError("Strategy 1 order history page bounds are invalid")
    prefix = _terminal_attestation(client, normalized, cache)["prefix"]
    if max(after_command_sequence, after_transition_sequence) > prefix.last_sequence:
        raise ValueError("Order history cursor exceeds the verified journal")
    commands = load_committed_order_command_page(
        client, prefix, after_sequence=after_command_sequence, limit=limit)
    transitions = load_committed_order_transition_page(
        client, prefix, after_sequence=after_transition_sequence, limit=limit)
    if not _head_matches(client, normalized, prefix):
        raise RuntimeError("Order history terminal head changed during page read")
    return {
        "schema_version": "strategy-one-v4-order-history-page-v1",
        "run_id": normalized,
        "verified_sequence": prefix.last_sequence,
        "commands": commands,
        "transitions": transitions,
        "next_command_sequence": (int(commands[-1]["sequence"])
                                  if commands else after_command_sequence),
        "next_transition_sequence": (int(transitions[-1]["sequence"])
                                     if transitions else after_transition_sequence),
        "complete": len(commands) < limit and len(transitions) < limit,
    }


def _utc_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def _complete_detail_rows(loader, client, prefix, *, maximum: int = 100_000) -> tuple[dict, ...]:
    """Bound every read, and refuse to present a partial performance report."""
    rows: list[dict] = []
    after = 0
    while True:
        page = loader(client, prefix, after_sequence=after, limit=1000)
        if len(rows) + len(page) > maximum:
            raise RuntimeError("Saved Canvas performance exceeds the bounded read limit")
        rows.extend(page)
        if len(page) < 1000:
            return tuple(rows)
        next_after = int(page[-1]["sequence"])
        if next_after <= after:
            raise RuntimeError("Saved Canvas performance cursor did not advance")
        after = next_after


def _saved_protection_events(client, prefix, *, maximum: int = 20_000) -> list[dict]:
    """Read only committed, sealed protection facts for chart presentation.

    Unlike a whole-journal scan, this projects the normalized protection family
    directly. It is never a broker/recovery authority and never writes ARTE.
    """
    from src.backend.backtest_protection_change_v3 import recover_protection_change_payload

    def fetch(table: str, predicate: str, bound: int) -> tuple[dict, ...]:
        columns = ",".join(name for name, _ in _CONTRACTS[table].columns)
        raw = _rows(client, f"SELECT {columns} FROM arte.{table} "
                    f"WHERE run_id={_literal(prefix.run_id)} {predicate} "
                    f"{_committed_batch_filter(prefix)}"
                    f"LIMIT {bound + 1} FORMAT JSONEachRow")
        if len(raw) > bound:
            raise RuntimeError("Saved chart protection evidence exceeds its bound")
        return _verify_v1_rows(table, tuple(raw))

    details = fetch("trading_protection_change_v3", "", maximum)
    if not details:
        return []
    child_count = sum(int(row["entry_order_count"]) for row in details)
    if child_count > maximum * 16:
        raise RuntimeError("Saved chart protection child evidence exceeds its bound")
    children: list[dict] = []
    parents: list[dict] = []
    # Bound the SQL text as well as returned rows; a long all-ticker session
    # must not create a single unbounded IN expression on the read path.
    for start in range(0, len(details), 500):
        group = details[start:start + 500]
        ids = ",".join(f"toUUID({_literal(str(UUID(row['record_id'])))})" for row in group)
        predicate = f"AND record_id IN ({ids})"
        children.extend(fetch("trading_protection_entry_order_v3", predicate,
                              sum(int(row["entry_order_count"]) for row in group)))
        parents.extend(fetch("trading_event_v1", predicate, len(group)))
    if len(parents) != len(details) or len(children) != child_count:
        raise RuntimeError("Saved chart protection evidence is incomplete")
    parent_by_id = {str(UUID(row["record_id"])): row for row in parents}
    if len(parent_by_id) != len(parents):
        raise RuntimeError("Saved chart protection parent repeats")
    children_by_id: dict[str, list[dict]] = {}
    for row in children:
        children_by_id.setdefault(str(UUID(row["record_id"])), []).append(row)
    events = []
    for detail in details:
        identity = str(UUID(detail["record_id"]))
        parent = parent_by_id.pop(identity, None)
        if parent is None or int(parent["sequence"]) > prefix.last_sequence:
            raise RuntimeError("Saved chart protection parent is uncommitted")
        ordered = sorted(children_by_id.pop(identity, ()), key=lambda row: int(row["ordinal"]))
        payload = recover_protection_change_payload(parent, detail, ordered)
        if (detail["batch_id"] != parent["batch_id"]
                or detail["event_month"] != parent["event_month"]
                or detail["account_id"] != parent["account_id"]):
            raise RuntimeError("Saved chart protection identity differs")
        events.append({**payload, "account_id": parent["account_id"],
                       "event_time": _utc_timestamp(parent["event_time"]).isoformat(),
                       "sequence": int(parent["sequence"])})
    if parent_by_id or children_by_id:
        raise RuntimeError("Saved chart protection has orphan evidence")
    return events


@declared_saved_read_operation
def load_v4_performance_report(client, run_id: str, *,
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
    prefix = _terminal_attestation(client, normalized, cache)["prefix"]
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


@declared_saved_read_operation
def load_cached_v4_performance_report(client, run_id: str, *,
                                      cache: AuditedSessionCache | None = None) -> dict:
    """Reuse only a fully verified terminal projection, never its authority.

    The attestation rechecks the ClickHouse run context and committed head on
    every call. The bounded cache stores presentation data only in process
    memory; a changed head cannot authorize an old report.
    """
    try:
        normalized = str(UUID(run_id))
    except (TypeError, ValueError) as exc:
        raise ValueError("Strategy 1 performance requires a UUID run id") from exc
    selected_cache = cache if cache is not None else _V4_PERFORMANCE_CACHE
    if not isinstance(selected_cache, AuditedSessionCache):
        raise ValueError("Strategy 1 performance cache is invalid")
    attestation = _terminal_attestation(client, normalized, None)
    prefix = attestation["prefix"]
    key = _cache_key(client, normalized, {**attestation["context"],
        "performance_projection": "strategy-one-v4-performance-report-v2"}, prefix)
    cached = selected_cache.get(key)
    if cached is not None and _head_matches(client, normalized, prefix):
        return cached["report"]
    report = load_v4_performance_report(client, normalized)
    if (report["run_id"] != normalized
            or int(report["verified_sequence"]) != prefix.last_sequence
            or not _head_matches(client, normalized, prefix)):
        raise RuntimeError("Saved performance head changed during projection")
    try:
        selected_cache.put(key, {"report": report})
    except ValueError:
        # A large but valid report remains readable without growing the cache.
        pass
    return report


@declared_saved_read_operation
def load_v4_chart_trades(client, run_id: str, ticker: str) -> dict:
    """Bounded ticker projection of the verified terminal performance report.

    A chart needs only position markers and effective protection rails, not
    every execution, fee, episode, and journal-derived diagnostic for the run.
    Keep the full-prefix/head check in the shared report reader before pruning.
    """
    symbol = ticker.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.-]{1,24}", symbol):
        raise ValueError("Saved chart trade ticker is invalid")
    page = load_cached_v4_performance_report(client, run_id)
    from src.trading_runtime.arte_intent_projection import (
        load_committed_strategy_intent_page,
    )
    attestation = _terminal_attestation(client, str(UUID(run_id)), None)
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
