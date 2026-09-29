"""Read-only audit of normalized simulator executions at a V4 checkpoint.

The run may be terminal while the selected broker checkpoint is historical.
No result from this script grants interrupted-run resume admission.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import os
from pathlib import Path
import platform
import re
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

from src.backend.backtest_market_data import (
    _MarketCertificateReader, market_day_boundary, project_market_day_plan,
)
from src.backend.backtest_journal_memory import BacktestMemoryJournal
from src.backend.backtest_strategy_one_candidate_store import certify_candidate_plan
from src.backend.backtest_strategy_one_evidence import StrategyOneCausalEvidence
from src.backend.backtest_strategy_one_hod_store import certify_hod_plan
from src.backend.backtest_strategy_one_pivot_store import certify_pivot_plan
from src.backend.backtest_strategy_one_preparation import strategy_one_v7_tickers
from src.backend.backtest_strategy_one_v7_interval_store import certify_v7_interval_plan
from src.backend.structural_v7_seed import certified_seed_plan
from src.backend.backtest_strategy_one_configuration import selected_strategy_one_revision
from src.backend.backtest_v3_clients import v3_client
from src.backend.backtest_v4_run_context import historical_strategy_one_portfolio_profiles
from src.backend.backtest_v4_broker_quote_restore import load_completed_broker_quotes
from src.backend.backtest_v4_broker_state_restore import reconstruct_broker_match_state
from src.backend.backtest_v4_execution_restore import load_v4_broker_executions
from src.backend.backtest_strategy_one_management import StrategyOneManagementRunner
from src.trading_runtime.arte_market_day_cold_preflight import sealed_certified_market_day_plan
from src.trading_runtime.arte_market_day_keeper import MarketDayKeeperReader
from src.trading_runtime.arte_journal_commit_v4 import (
    V4CommittedPrefix, load_verified_v4_prefix,
)
from src.trading_runtime.arte_backtest_definition import load_backtest_definition
from src.trading_runtime.arte_journal_writer import (
    _literal, _rows, backtest_v4_operator_client_from_env,
    load_typed_run_context,
)
from src.trading_runtime.arte_journal_reader import load_complete_typed_protection_history
from src.trading_runtime.arte_oms_actor_restore import (
    install_typed_oms_actor_image, reconstruct_typed_oms_actor_image,
    verify_typed_oms_broker_open_orders,
)
from src.trading_runtime.order_management import OrderManagementEngine
from src.trading_runtime.arte_oms_projection import (
    load_recovered_strategy_one_oms_lineage,
)
from src.trading_runtime.arte_portfolio_recovery import recover_portfolio_engine_state
from src.trading_runtime.canonical_session import CanonicalBrokerSession
from src.trading_runtime.domain import BrokerProvider, TradingMode
from src.trading_runtime.keeper_session import open_workstation_keeper_session
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter
from src.trading_runtime.portfolio import PortfolioManagementEngine
from src.trading_runtime.strategy_one_broker_match_snapshot import (
    load_unattested_broker_match_snapshot, project_broker_match_snapshot,
)
from src.trading_runtime.strategy_one_evidence_snapshot import (
    ManagedEvidenceSnapshotHeadReader,
    load_unattested_evidence_snapshot_rows, restore_evidence_snapshot,
)
from src.trading_runtime.strategy_one_campaign_snapshot import (
    ManagedCampaignSnapshotHeadReader, load_campaign_snapshot,
)
from src.trading_runtime.strategy_one_contract import STRATEGY_ID, STRATEGY_NUMBER
from src.trading_runtime.strategy_one_candidate_schema import RULE_DIGEST
from src.trading_runtime.strategy_one_management_snapshot import (
    load_unattested_manager_snapshot_rows, restore_manager_snapshot,
)


def audit(*, run_id: str, build_id: str, session: date,
          checkpoint_sequence: int) -> tuple[int, int]:
    if (platform.node().upper() != "DESKTOP-SAAI85T"
            or str(UUID(run_id)) != run_id
            or re.fullmatch(r"[0-9a-f]{64}(?:-[0-9a-f]{12})?", build_id) is None
            or not isinstance(session, date)
            or type(checkpoint_sequence) is not int or checkpoint_sequence < 1):
        raise ValueError("Audit needs exact workstation run, build, session, checkpoint")
    credential = Path(r"D:\TradingML\secrets\backtest_v4_runner.env")
    market_credential = Path(r"D:\TradingML\secrets\backtest_v3_read.env")
    if not credential.is_file() or not market_credential.is_file():
        raise RuntimeError("Managed audit credentials are unavailable")
    os.environ["BACKTEST_V4_RUNNER_CREDENTIAL_FILE"] = str(credential)
    os.environ["BACKTEST_V3_READ_CREDENTIAL_FILE"] = str(market_credential)
    with closing(backtest_v4_operator_client_from_env()) as client, closing(
            v3_client("read")) as market_http, closing(
            open_workstation_keeper_session()) as keeper_session:
        market = _MarketCertificateReader(market_http)
        plan = sealed_certified_market_day_plan(
            market, MarketDayKeeperReader(keeper_session.client), build_id,
            sessions=(session.isoformat(),), tickers=(),
            configuration={"strategy": {"strategy_number": 1,
                                         "execution_interval": "100ms"}},
            read_client_factory=lambda: _MarketCertificateReader(v3_client("read")))
        context = load_typed_run_context(client, run_id)
        definition = load_backtest_definition(client, run_id, run_context=context)
        if (context["mode"] != "backtest"
                or context["market_plan_token"] != plan.token):
            raise RuntimeError("Broker audit differs from pinned Backtest market plan")
        revision = selected_strategy_one_revision(client=market_http)
        if revision["content_hash"] != context["configuration_hash"]:
            raise RuntimeError("Cold portfolio configuration differs from V4 run")
        terminal_prefix = load_verified_v4_prefix(client, run_id)
        if terminal_prefix is None or checkpoint_sequence > terminal_prefix.last_sequence:
            raise RuntimeError("Broker execution audit lacks verified V4 prefix")
        matches = _rows(client,
            "SELECT batch_id,source_cursor,status FROM arte.trading_commit_v4 "
            f"WHERE run_id={_literal(run_id)} "
            f"AND last_sequence={checkpoint_sequence} "
            "LIMIT 2 FORMAT JSONEachRow")
        if len(matches) != 1 or matches[0]["batch_id"] not in terminal_prefix.batch_ids:
            raise RuntimeError("Broker checkpoint is not an exact V4 commit boundary")
        position = terminal_prefix.batch_ids.index(matches[0]["batch_id"])
        prefix = V4CommittedPrefix(
            run_id, checkpoint_sequence, matches[0]["batch_id"],
            matches[0]["source_cursor"], matches[0]["status"],
            terminal_prefix.batch_ids[:position + 1])
        broker = load_unattested_broker_match_snapshot(
            client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)
        accounts = frozenset(row["account_id"] for row in broker.accounts)
        lineages = load_recovered_strategy_one_oms_lineage(
            client, prefix, allowed_accounts=accounts)
        root = broker.snapshot
        boundary = market_day_boundary(date.fromisoformat(root["session_date"]), 0)
        boundary += timedelta(milliseconds=int(root["boundary_ms"]))
        campaign = load_campaign_snapshot(
            client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)
        campaign_root = campaign.snapshot
        if (campaign_root["session_date"] != session.isoformat()
                or campaign_root["boundary_ms"] != root["boundary_ms"]
                or campaign_root["journal_batch_id"] != prefix.last_batch_id):
            raise RuntimeError("Cold campaign image differs from broker checkpoint")
        campaign_head = ManagedCampaignSnapshotHeadReader(keeper_session).read_head(
            run_id=run_id)
        if (campaign_head.checkpoint_sequence < checkpoint_sequence
                or (campaign_head.checkpoint_sequence == checkpoint_sequence
                    and (campaign_head.journal_batch_id != prefix.last_batch_id
                         or campaign_head.snapshot_hash != campaign_root["content_hash"]))):
            raise RuntimeError("Cold campaign Keeper head differs from selected rows")
        campaign_journal = BacktestMemoryJournal(
            run_id=run_id, initial_sequence=checkpoint_sequence)
        campaign_journal.restore_verified_campaign_ownership(campaign)
        expected_owners = tuple({name: row[name] for name in (
            "resource_id", "session_key", "owner_id", "state", "epoch")}
            for row in campaign.owners)
        if campaign_journal.campaign_ownership_snapshot() != expected_owners:
            raise RuntimeError("Cold campaign actor differs after restoration")
        profiles, groups = historical_strategy_one_portfolio_profiles(
            revision["payload"])
        if tuple(profile.account_id for profile in profiles) != tuple(
                row["account_id"] for row in broker.accounts):
            raise RuntimeError("Cold portfolio accounts differ from broker")
        portfolio_recovery = recover_portfolio_engine_state(
            client, run_id=run_id, profiles=profiles,
            state_revisions={profile.account_id: checkpoint_sequence
                             for profile in profiles}, cutoff_at=boundary)
        portfolio = PortfolioManagementEngine(
            profiles, journal=campaign_journal, run_id=run_id,
            strategy_id=STRATEGY_ID, strategy_revision=STRATEGY_NUMBER,
            groups=groups, typed_recovery=portfolio_recovery,
            event_clock=lambda: boundary)
        if (set(portfolio.states) != {row["account_id"] for row in broker.accounts}
                or portfolio.reservations != portfolio_recovery.reservations
                or portfolio.allocations != portfolio_recovery.allocations
                or set(campaign_journal.portfolio_states()) != set(portfolio.states)):
            raise RuntimeError("Cold portfolio actor differs from normalized state")
        manager_rows = load_unattested_manager_snapshot_rows(
            client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)
        if (manager_rows.snapshot["session_date"] != session.isoformat()
                or manager_rows.snapshot["boundary_ms"] != root["boundary_ms"]):
            raise RuntimeError("Cold manager image differs from broker boundary")
        manager_state = restore_manager_snapshot(manager_rows)
        inert = lambda *_a, **_k: None
        manager = StrategyOneManagementRunner(
            runtime=SimpleNamespace(submit_strategy_one_proposal=inert,
                                    submit_strategy_one_protection=inert),
            evidence=SimpleNamespace(management_evidence=inert),
            tick_for_ticker=lambda _ticker: 0.01)
        manager.restore_state(manager_state)
        if manager.capture_state(boundary_ms=int(root["boundary_ms"])) != manager_state:
            raise RuntimeError("Cold manager actor differs after restoration")
        evidence_rows = load_unattested_evidence_snapshot_rows(
            client, run_id=run_id, checkpoint_sequence=checkpoint_sequence)
        evidence_state = restore_evidence_snapshot(evidence_rows)
        if (evidence_rows.snapshot["session_date"] != session.isoformat()
                or evidence_state.boundary_ms != root["boundary_ms"]):
            raise RuntimeError("Cold evidence state differs from broker boundary")
        evidence_head = ManagedEvidenceSnapshotHeadReader(keeper_session).read_head(
            run_id=run_id)
        if evidence_head.checkpoint_sequence == checkpoint_sequence and (
                evidence_head.journal_batch_id != prefix.last_batch_id
                or evidence_head.snapshot_hash != evidence_rows.snapshot["content_hash"]):
            raise RuntimeError("Cold evidence Keeper head differs from selected rows")
        protection = load_complete_typed_protection_history(client, prefix)
        oms_image = reconstruct_typed_oms_actor_image(
            lineages, protection, run_id=run_id, strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_NUMBER,
            through_sequence=prefix.last_sequence, cutoff_at=boundary)
        verify_typed_oms_broker_open_orders(oms_image, broker)
        oms_actor = OrderManagementEngine(
            broker=MagicMock(), planner=MagicMock(), risk=MagicMock(),
            journal=MagicMock(), run_id=run_id, strategy_id=STRATEGY_ID,
            strategy_revision=STRATEGY_NUMBER)
        install_typed_oms_actor_image(oms_actor, oms_image)
        if (set(oms_actor._groups) != set(oms_image.groups)
                or oms_actor._group_by_client_id != oms_image.group_by_client_id
                or oms_actor._group_by_broker_id != oms_image.group_by_broker_id
                or oms_actor._protection_versions != oms_image.protection_versions):
            raise RuntimeError("Cold OMS actor differs after installation")
        requests = {}
        broker_ids = {}
        for lineage in lineages:
            for request in lineage.orders:
                if request.cOID in requests:
                    raise RuntimeError("Audit repeats OMS client order")
                requests[request.cOID] = request
            for binding in lineage.state.broker_bindings:
                index = binding["request_index"]
                if index is None:
                    continue
                if type(index) is not int or not 0 <= index < len(lineage.orders):
                    raise RuntimeError("Audit has invalid OMS broker binding")
                broker_id = binding["broker_order_id"]
                if broker_id in broker_ids:
                    raise RuntimeError("Audit repeats OMS broker order")
                broker_ids[broker_id] = lineage.orders[index].cOID
        executions = load_v4_broker_executions(
            client, prefix, requests_by_coid=requests,
            coid_by_broker_id=broker_ids,
            next_execution_id=int(broker.snapshot["next_execution_id"]))
        quotes = load_completed_broker_quotes(market, plan=plan, broker=broker)
        open_requests = {
            row["broker_order_id"]: requests[row["client_order_id"]]
            for row in broker.open_orders
        }
        image = reconstruct_broker_match_state(
            broker, requests_by_broker_id=open_requests, quotes=quotes)
        image["executions"] = executions
        restored = SimulatedBrokerAdapter(
            [row["account_id"] for row in broker.accounts],
            mode=TradingMode.BACKTEST)
        restored.restore_checkpoint_state(image)
        if project_broker_match_snapshot(
                run_id=run_id, session_date=session,
                checkpoint_sequence=checkpoint_sequence,
                boundary_ms=int(broker.snapshot["boundary_ms"]),
                state=restored.broker_match_snapshot_state()) != broker:
            raise RuntimeError("Restored broker differs from normalized checkpoint")
        canonical = CanonicalBrokerSession(
            restored, mode=TradingMode.BACKTEST,
            provider=BrokerProvider.SIMULATED)
        asyncio.run(canonical.bootstrap())
        portfolio.reconcile_recovered_backtest_canonical(
            canonical.projector.snapshot(), completed_at=boundary)
        candidates = certify_candidate_plan(
            plan, candidate_rule_digest=RULE_DIGEST,
            through_boundary_ms=57_600_000, client=market_http)
        selected = strategy_one_v7_tickers(candidates.prepared)
        execution = project_market_day_plan(plan, selected)
        seeds = certified_seed_plan(execution, market_http)
        if seeds.token != definition["definition"]["causal_v7_plan_token"]:
            raise RuntimeError("Cold V7 seed differs from saved Backtest definition")
        pivots = certify_pivot_plan(
            plan, session_date=session.isoformat(),
            candidate_tickers=selected, client=market_http)
        hod = certify_hod_plan(plan, candidates, seeds, client=market_http)
        intervals = certify_v7_interval_plan(
            execution, seeds, session_date=session.isoformat(),
            candidate_tickers=selected, client=market_http)
        evidence = StrategyOneCausalEvidence(
            market_plan=execution, seed_plan=seeds, pivot_plan=pivots,
            hod_plan=hod, session=session, client=market_http,
            interval_plan=intervals, precomputed_entry_facts=True)
        asyncio.run(evidence.restore_recovery_state(
            evidence_state,
            financially_active_tickers=restored.financially_active_tickers()))
        if evidence.capture_recovery_state() != evidence_state:
            raise RuntimeError("Cold evidence actor differs after restoration")
        if any(datetime.fromisoformat(row["trade_time"]).astimezone(timezone.utc)
               > boundary for row in executions):
            raise RuntimeError("Audit found a fill after the broker checkpoint")
        if load_verified_v4_prefix(client, run_id) != terminal_prefix:
            raise RuntimeError("Audit V4 prefix moved across reads")
        return len(executions), len(broker.open_orders)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--session", required=True, type=date.fromisoformat)
    parser.add_argument("--checkpoint-sequence", required=True,
                        help="positive committed sequence, or latest campaign checkpoint")
    args = parser.parse_args()
    if args.checkpoint_sequence == "latest":
        credential = Path(r"D:\TradingML\secrets\backtest_v4_runner.env")
        if platform.node().upper() != "DESKTOP-SAAI85T" or not credential.is_file():
            raise RuntimeError("Latest checkpoint lookup needs the managed workstation")
        os.environ["BACKTEST_V4_RUNNER_CREDENTIAL_FILE"] = str(credential)
        with closing(backtest_v4_operator_client_from_env()) as client:
            selected = _rows(client,
                "SELECT checkpoint_sequence FROM "
                "arte.trading_strategy_one_campaign_snapshot_v1 "
                f"WHERE run_id={_literal(args.run_id)} "
                "ORDER BY checkpoint_sequence DESC LIMIT 1 FORMAT JSONEachRow")
        if len(selected) != 1:
            raise RuntimeError("Run has no unique latest campaign checkpoint")
        checkpoint_sequence = int(selected[0]["checkpoint_sequence"])
    else:
        checkpoint_sequence = int(args.checkpoint_sequence)
    fills, orders = audit(run_id=args.run_id, build_id=args.build_id,
                          session=args.session,
                          checkpoint_sequence=checkpoint_sequence)
    print(f"V4 cold broker image audit passed: fills={fills} "
          f"open_orders={orders} checkpoint_sequence={checkpoint_sequence} writes=0")


if __name__ == "__main__":
    main()
