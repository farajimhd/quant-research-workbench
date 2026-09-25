"""Inactive terminal V3 publication under cold prefix and durable operations."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from typing import Any, Mapping

from src.backend.backtest_squeeze_v3_keeper import attested_squeeze_v3_barrier
from src.backend.backtest_terminal_v2_publication import (
    _insert_missing, _publish_portfolio_anchors,
)
from src.backend.backtest_terminal_v2_keeper import FixedTerminalKeeperAuthority
from src.backend.backtest_terminal_v3_dispatch import TerminalV3DispatchClient
from src.backend.backtest_terminal_v3_fence import (
    TERMINAL_COMMIT_V3, load_terminal_v3_commit, project_terminal_v3_commit,
)
from src.backend.backtest_terminal_v3_keeper import (
    attest_terminal_v3, load_terminal_v3_receipt, operation_inventory_hash,
)
from src.trading_runtime.arte_backtest_snapshot_anchor import _stored as _stored_anchors
from src.trading_runtime.arte_journal_schema import storage_preflight
from src.trading_runtime.arte_journal_writer import _literal, _rows
from src.trading_runtime.arte_portfolio_snapshot import (
    CapturedPortfolioSnapshot, _SNAPSHOT_COMMIT, _SNAPSHOT_FAMILIES,
    _stored_rows as _stored_portfolio_rows, load_portfolio_snapshot,
)
from src.trading_runtime.arte_portfolio_policy import (
    _policy_rows, load_attested_portfolio_policy,
)
from src.trading_runtime.arte_typed_insert_dispatch import TypedInsertDispatch
from src.trading_runtime.journal_contract import canonical_json


def _account_states(client: Any, *, run_id: str, account_ids: tuple[str, ...],
                    batch_id: str, last_sequence: int) -> dict[str, dict[str, Any]]:
    states = {}
    for account_id in account_ids:
        anchors = _stored_anchors(client, run_id, account_id)
        if (len(anchors) != 1 or anchors[0]["batch_id"] != batch_id
                or int(anchors[0]["last_sequence"]) != last_sequence):
            raise RuntimeError("V3 terminal portfolio anchor is absent or ambiguous")
        state = load_portfolio_snapshot(
            client, run_id=run_id, account_id=account_id,
            state_revision=last_sequence)
        if state is None or state["state_hash"] != anchors[0]["snapshot_hash"]:
            raise RuntimeError("V3 terminal portfolio state differs from anchor")
        states[account_id] = state
    return states


def _required_operation_tables(client: Any, *, run_id: str,
                               account_ids: tuple[str, ...],
                               last_sequence: int,
                               events: tuple[Mapping[str, Any], ...],
                               transitions: tuple[Mapping[str, Any], ...],
                               accounts: tuple[Mapping[str, Any], ...],
                               positions: tuple[Mapping[str, Any], ...],
                               ) -> tuple[set[str], dict[str, set[str]]]:
    required = {"trading_backtest_terminal_commit_v3",
                "trading_backtest_snapshot_anchor_v1"}
    for table, rows in (("trading_event_v1", events),
                        ("trading_run_transition_v1", transitions),
                        ("trading_backtest_account_snapshot_v2", accounts),
                        ("trading_backtest_position_snapshot_v2", positions)):
        if rows:
            required.add(table)
    per_account = {}
    for account_id in account_ids:
        account_tables = {"trading_backtest_snapshot_anchor_v1"}
        for table, _ in _SNAPSHOT_FAMILIES:
            if _stored_portfolio_rows(client, table, run_id, account_id,
                                      last_sequence):
                required.add(table)
                account_tables.add(table)
        if _stored_portfolio_rows(client, _SNAPSHOT_COMMIT, run_id,
                                  account_id, last_sequence):
            required.add(_SNAPSHOT_COMMIT)
            account_tables.add(_SNAPSHOT_COMMIT)
        per_account[account_id] = account_tables
    return required, per_account


def _require_prepublished_policies(
    read_client: Any, dispatch: TypedInsertDispatch,
    captures: tuple[CapturedPortfolioSnapshot, ...],
) -> None:
    """A terminal writer may reference, but never create, global policy rows."""
    for capture in captures:
        policy = capture.selected_policy
        if policy is None:
            continue
        policy_hash = _policy_rows(policy)[0]
        if load_attested_portfolio_policy(read_client, dispatch, policy_hash) != policy:
            raise RuntimeError("V3 selected portfolio policy is not prepublished and attested")


def publish_terminal_v3_suffix(
    read_client: Any, authority: FixedTerminalKeeperAuthority,
    dispatch: TypedInsertDispatch, *, run_id: str, account_ids: tuple[str, ...],
    expected_market_plan_token: str, expected_query_sha256: str,
    attempt_id: str, batch_id: str, source_cursor: str, status: str,
    committed_at: datetime, events: tuple[Mapping[str, Any], ...],
    transitions: tuple[Mapping[str, Any], ...],
    accounts: tuple[Mapping[str, Any], ...],
    positions: tuple[Mapping[str, Any], ...],
    portfolio_captures: tuple[CapturedPortfolioSnapshot, ...],
) -> dict[str, Any]:
    """Worker-only facts, anchors, V3 seal last, then account/operation CAS."""
    if not isinstance(authority, FixedTerminalKeeperAuthority):
        raise TypeError("V3 terminal publication requires held account Keeper authority")
    if not isinstance(dispatch, TypedInsertDispatch):
        raise TypeError("V3 terminal publication requires durable typed dispatch")
    if (not portfolio_captures or
            any(type(capture) is not CapturedPortfolioSnapshot
                for capture in portfolio_captures)):
        raise ValueError("V3 terminal captures are invalid")
    authority.assert_current(run_id, account_ids)
    if read_client is authority.client._client:
        raise ValueError("V3 terminal read and writer clients must be distinct")
    _require_prepublished_policies(read_client, dispatch, portfolio_captures)
    storage_preflight(authority.client, tables=(TERMINAL_COMMIT_V3,))
    with attested_squeeze_v3_barrier(
        read_client, dispatch, run_id=run_id,
        expected_market_plan_token=expected_market_plan_token,
        expected_query_sha256=expected_query_sha256) as fence:
        prefix = fence.prefix
        expected = project_terminal_v3_commit(
            prefix, account_ids=account_ids, attempt_id=attempt_id,
            batch_id=batch_id, source_cursor=source_cursor, status=status,
            committed_at=committed_at, events=events, transitions=transitions,
            accounts=accounts, positions=positions)
        client = TerminalV3DispatchClient(
            authority.client, dispatch, fence, run_id=run_id,
            batch_id=batch_id, last_sequence=int(expected["last_sequence"]))
        for table, rows in (("trading_event_v1", events),
                            ("trading_run_transition_v1", transitions),
                            ("trading_backtest_account_snapshot_v2", accounts),
                            ("trading_backtest_position_snapshot_v2", positions)):
            _insert_missing(client, table, run_id=run_id, batch_id=batch_id,
                            expected=rows)
        _publish_portfolio_anchors(
            client, prefix=prefix, seal=expected, account_ids=account_ids,
            events=events, captures=portfolio_captures)
        prior = _rows(client,
            "SELECT * FROM arte.trading_backtest_terminal_commit_v3 "
            f"WHERE run_id={_literal(run_id)} FORMAT JSONEachRow")
        if prior and (len(prior) != 1 or prior[0] != expected):
            raise RuntimeError("V3 terminal seal conflicts with existing publication")
        if not prior:
            columns = ",".join(name for name, _ in TERMINAL_COMMIT_V3.columns)
            client.execute(
                f"INSERT INTO arte.trading_backtest_terminal_commit_v3 ({columns}) "
                "SETTINGS async_insert=1,wait_for_async_insert=1,insert_deduplicate=1,"
                f"insert_deduplication_token={_literal('terminal-v3:' + batch_id + ':commit')} "
                f"FORMAT JSONEachRow\n{canonical_json(expected)}")
        seal = load_terminal_v3_commit(client, prefix, account_ids=account_ids)
        states = _account_states(client, run_id=run_id, account_ids=account_ids,
                                 batch_id=batch_id,
                                 last_sequence=int(seal["last_sequence"]))
        operations = client.acknowledged_operations()
        required_tables, account_tables = _required_operation_tables(
            client, run_id=run_id, account_ids=account_ids,
            last_sequence=int(seal["last_sequence"]), events=events,
            transitions=transitions, accounts=accounts, positions=positions)
        client.assert_covers(required_tables)
        client.assert_account_covers(account_tables)
        authority.assert_current(run_id, account_ids)
        attest_terminal_v3(
            authority.keeper,
            tuple(zip(authority.account_ids, authority._leases, strict=True)),
            run_id=run_id, seal=seal, accounts=states,
            operations=operations)
        authority.assert_current(run_id, account_ids)
        return seal


def load_attested_terminal_v3_state(
    client: Any, dispatch: TypedInsertDispatch, keeper: Any, *, run_id: str,
    account_ids: tuple[str, ...], expected_market_plan_token: str,
    expected_query_sha256: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Cold audit binds exact CH seal/accounts to acknowledged Keeper operations."""
    storage_preflight(client, tables=(TERMINAL_COMMIT_V3,))
    with attested_squeeze_v3_barrier(
        client, dispatch, run_id=run_id,
        expected_market_plan_token=expected_market_plan_token,
        expected_query_sha256=expected_query_sha256) as fence:
        seal = load_terminal_v3_commit(client, fence.prefix,
                                       account_ids=account_ids)
        batch_id = str(seal["batch_id"])
        states = _account_states(client, run_id=run_id, account_ids=account_ids,
                                 batch_id=batch_id,
                                 last_sequence=int(seal["last_sequence"]))
        operation_client = TerminalV3DispatchClient(
            client, dispatch, fence, run_id=run_id, batch_id=batch_id,
            last_sequence=int(seal["last_sequence"]))
        operations = operation_client.acknowledged_operations()
        # The cold loader knows the V3 seal's family counts; it must also
        # reject any preexisting terminal rows not backed by a durable op.
        required = {"trading_backtest_terminal_commit_v3",
                    "trading_backtest_snapshot_anchor_v1",
                    "trading_event_v1", "trading_run_transition_v1",
                    "trading_backtest_account_snapshot_v2"}
        if int(seal["position_count"]):
            required.add("trading_backtest_position_snapshot_v2")
        account_tables = {}
        for account_id in account_ids:
            present = {"trading_backtest_snapshot_anchor_v1"}
            for table, _ in _SNAPSHOT_FAMILIES:
                if _stored_portfolio_rows(client, table, run_id, account_id,
                                          int(seal["last_sequence"])):
                    required.add(table)
                    present.add(table)
            if _stored_portfolio_rows(client, _SNAPSHOT_COMMIT, run_id,
                                      account_id, int(seal["last_sequence"])):
                required.add(_SNAPSHOT_COMMIT)
                present.add(_SNAPSHOT_COMMIT)
            account_tables[account_id] = present
        operation_client.assert_covers(required)
        operation_client.assert_account_covers(account_tables)
        proof = load_terminal_v3_receipt(keeper, run_id=run_id,
                                         batch_id=batch_id)
        seal_hash = sha256(canonical_json(seal).encode()).hexdigest()
        accounts_hash = sha256(canonical_json(sorted(
            (account, states[account]["state_hash"]) for account in states
        )).encode()).hexdigest()
        if proof is None:
            raise RuntimeError("V3 terminal seal lacks Keeper proof")
        parts = proof.decode("ascii").split("\n")
        if (parts[1:6] != [run_id, batch_id, seal_hash, accounts_hash,
                           operation_inventory_hash(operations)]
                or parts[7::3] != sorted(account_ids)):
            raise RuntimeError("V3 terminal Keeper proof differs from cold state")
        return seal, states
