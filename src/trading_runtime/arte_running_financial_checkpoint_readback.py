"""SELECT-only reconstruction of a durable running financial clock link.

This proves referenced checkpoint products, never order admission, OMS lineage
or strategy-manager recovery. Native callers must verify those independently.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType

from .arte_running_financial_checkpoint import (ROOT, ACCOUNT,
    RunningFinancialCheckpointRows, project_running_financial_checkpoint)
from .arte_journal_commit_v4 import (V4CommittedPrefix, load_verified_v4_prefix,
    load_verified_commit_v4, verified_batch_predecessor)
from .arte_journal_projection import load_latest_backtest_cursor
from .arte_journal_writer import _literal, _rows, load_typed_run_context
from .arte_portfolio_snapshot import load_portfolio_snapshot, prepare_portfolio_snapshot
from .strategy_one_broker_match_snapshot import load_unattested_broker_match_snapshot, verify_broker_match_snapshot


@dataclass(frozen=True, slots=True)
class ReconstructedRunningFinancialCheckpoint:
    prefix: V4CommittedPrefix
    cursor: dict
    rows: object
    portfolios: tuple
    broker: object


def reconstruct_running_financial_products(client, *, run_id, batch_id, checkpoint_sequence,
                                     first_price_source=None, declared_native_contexts=()):
    """Reload the fenced chain and exact products; no latest-head substitution."""
    if type(checkpoint_sequence) is not int or not 0 < checkpoint_sequence < 2**64:
        raise ValueError('Running financial readback needs an exact sequence')
    full = load_verified_v4_prefix(client, run_id, first_price_source=first_price_source,
                                  declared_native_contexts=declared_native_contexts)
    if full is None or batch_id not in full.batch_ids:
        raise RuntimeError('Running checkpoint batch is outside the verified chain')
    contexts = {c.unit.base.batch_id: c for c in declared_native_contexts}
    preceding = verified_batch_predecessor(client, full, batch_id)
    commit, _ = load_verified_commit_v4(client, run_id=run_id, batch_id=batch_id,
        first_price_source=first_price_source,
        **({'verified_prior_prefix': preceding} if preceding is not None else {}),
        **({'declared_native_context': contexts[batch_id]} if batch_id in contexts else {}))
    if (type(commit['last_sequence']) is not int
            or commit['last_sequence'] != checkpoint_sequence or commit['status'] != 'running'):
        raise RuntimeError('Running checkpoint differs from its committed sequence')
    prefix = V4CommittedPrefix(run_id, checkpoint_sequence, batch_id,
        commit['source_cursor'], 'running', full.batch_ids[:full.batch_ids.index(batch_id)+1])
    cursor = load_latest_backtest_cursor(client, prefix)
    context = load_typed_run_context(client, run_id)
    if context['mode'] != 'backtest' or cursor is None:
        raise RuntimeError('Running checkpoint lacks its Backtest context and cursor')
    broker = verify_broker_match_snapshot(load_unattested_broker_match_snapshot(
        client, run_id=run_id, checkpoint_sequence=checkpoint_sequence))
    ids = tuple(sorted(context['account_ids']))
    if not ids or len(ids) > 4096 or len(set(ids)) != len(ids):
        raise RuntimeError('Running checkpoint account roster is empty, duplicate or unbounded')
    portfolios = []
    portfolio_hashes = []
    for account_id in ids:
        stored = load_portfolio_snapshot(client, run_id=run_id,
            account_id=account_id, state_revision=checkpoint_sequence)
        if stored is None:
            raise RuntimeError('Running checkpoint Portfolio product differs')
        portfolio_hashes.append(stored['state_hash'])
        portfolios.append(prepare_portfolio_snapshot(run_id=run_id, account_id=account_id,
            state_revision=checkpoint_sequence, snapshot_at=datetime.fromisoformat(stored['snapshot_at']),
            state=stored['state']))
    expected = project_running_financial_checkpoint(prefix=prefix, cursor=cursor,
        configuration_hash=context['configuration_hash'], portfolios=tuple(portfolios), broker=broker)
    if portfolio_hashes != [row['state_hash'] for row in expected.accounts]:
        raise RuntimeError('Running checkpoint reconstructed Portfolio hash differs')
    return ReconstructedRunningFinancialCheckpoint(prefix, cursor, expected, tuple(portfolios), broker)


def load_running_financial_checkpoint(client, *, run_id, batch_id, checkpoint_sequence,
                                     first_price_source=None, declared_native_contexts=()):
    result = reconstruct_running_financial_products(client, run_id=run_id, batch_id=batch_id,
        checkpoint_sequence=checkpoint_sequence, first_price_source=first_price_source,
        declared_native_contexts=declared_native_contexts)
    filters = f'WHERE run_id={_literal(run_id)} AND last_sequence={checkpoint_sequence} '
    roots = _rows(client, f"SELECT {','.join(n for n,_ in ROOT.columns)} FROM arte.{ROOT.name} "
                  f'{filters}LIMIT 2 FORMAT JSONEachRow')
    if len(roots) != 1 or type(roots[0]['account_count']) is not int or not 1 <= roots[0]['account_count'] <= 4096:
        raise RuntimeError('Running checkpoint root is missing, ambiguous or unbounded')
    root = roots[0]
    accounts = _rows(client, f"SELECT {','.join(n for n,_ in ACCOUNT.columns)} FROM arte.{ACCOUNT.name} "
                     f"{filters}ORDER BY ordinal LIMIT {root['account_count']+1} FORMAT JSONEachRow")
    if len(accounts) != root['account_count']:
        raise RuntimeError('Running checkpoint account inventory is incomplete')
    for row in accounts:
        at = datetime.fromisoformat(str(row['snapshot_at']).replace('Z', '+00:00'))
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)  # Declared database UTC column.
        row['snapshot_at'] = at.astimezone(timezone.utc).isoformat(timespec='microseconds')
    stored_rows = RunningFinancialCheckpointRows(MappingProxyType(dict(root)),
        tuple(MappingProxyType(dict(row)) for row in accounts))
    if stored_rows != result.rows:
        raise RuntimeError('Running checkpoint graph differs from reconstructed committed products')
    return result
