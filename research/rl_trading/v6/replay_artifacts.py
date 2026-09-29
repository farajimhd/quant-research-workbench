"""Immutable V6 model replay ledgers and W&B-ready summary metrics."""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

from research.rl_trading.v1.common import digest, exclusive, file_hash
from research.rl_trading.v6.replay_metrics import ReplayJournal
from research.rl_trading.v6.session_data import PackedSession


VERSION = 'rl-trading-v6-quote-bracket-model-replay-v1'


def _save_rows(path: Path, rows: list[dict],
               schema: dict[str, pl.DataType]) -> str:
    frame = pl.DataFrame(rows, strict=False) if rows else pl.DataFrame(schema=schema)
    temporary = path.with_suffix('.parquet.tmp')
    frame.write_parquet(temporary, compression='zstd')
    temporary.replace(path)
    return file_hash(path)


def save_replay(journal: ReplayJournal, session: PackedSession,
                checkpoint: Path, *, runtime_root: Path,
                source_commit: str,
                quote_evidence_certificate: Path,
                selection_certificate: Path | None = None) -> tuple[Path, dict]:
    """Persist the model's quote-bound scenario with explicit split gates.

    A sealed test requires an already selected, hash-bound checkpoint. This
    function never selects a checkpoint using sealed test metrics.
    """
    runtime = Path(runtime_root).resolve()
    checkpoint = Path(checkpoint).resolve()
    quote_evidence_certificate = Path(quote_evidence_certificate).resolve()
    if (not runtime.is_dir() or not checkpoint.is_file() or
            not checkpoint.is_relative_to(runtime) or
            not quote_evidence_certificate.is_file() or
            not quote_evidence_certificate.is_relative_to(runtime) or
            len(source_commit) < 8):
        raise ValueError('Replay requires runtime checkpoint and quote proof')
    quote_certificate = json.loads(quote_evidence_certificate.read_text())
    if (quote_certificate.get('status') != 'complete' or
            quote_certificate.get('day') != str(session.day)):
        raise ValueError('Quote evidence is not certified for replay day')
    selected_hash = None
    if session.role == 'sealed_test':
        if selection_certificate is None:
            raise ValueError('Sealed replay requires prior checkpoint selection')
        selection_certificate = Path(selection_certificate).resolve()
        if not selection_certificate.is_relative_to(runtime):
            raise ValueError('Selection certificate must be in runtime')
        selection = json.loads(selection_certificate.read_text())
        if (selection.get('status') != 'selected_on_development' or
                selection.get('checkpoint_sha256') != file_hash(checkpoint)):
            raise ValueError('Sealed replay checkpoint differs from selection')
        selected_hash = file_hash(selection_certificate)
    elif session.role not in ('train', 'development'):
        raise ValueError('Context-only candles cannot be replayed as a model')
    split = ('train_diagnostic' if session.role == 'train' else
             'development' if session.role == 'development' else 'test_sealed')
    metrics = journal.summary()
    plan = {'version': VERSION, 'day': str(session.day), 'split': split,
            'bank_certificate_sha256': session.source_certificate_sha256,
            'checkpoint': str(checkpoint),
            'checkpoint_sha256': file_hash(checkpoint),
            'quote_evidence_certificate_sha256':
                file_hash(quote_evidence_certificate),
            'selection_certificate_sha256': selected_hash,
            'source_commit': source_commit,
            'execution_scenario': metrics['execution_scenario']}
    plan['hash'] = digest(plan)
    root = runtime / 'rl-trading-v6-replay' / str(session.day) / plan['hash'][:20]
    root.mkdir(parents=True, exist_ok=True)
    plan_path = root / 'plan.json'
    with exclusive(root / 'run.lock'):
        if plan_path.exists():
            if json.loads(plan_path.read_text()) != plan:
                raise ValueError('Replay root belongs to another model/source')
        else:
            plan_path.write_text(json.dumps(plan, sort_keys=True),
                                 encoding='utf-8')
        complete = root / 'complete.json'
        if complete.exists():
            saved = json.loads(complete.read_text())
            if (saved.get('plan_hash') != plan['hash'] or
                    any(file_hash(root / name) != value for name, value in
                        saved['files_sha256'].items())):
                raise ValueError('Certified replay artifacts changed')
            return root, saved['metrics']
        if any((root / name).exists() for name in
               ('orders.parquet', 'positions.parquet', 'equity.parquet')):
            raise ValueError('Uncertified replay files already exist')
        files = {}
        files['orders.parquet'] = _save_rows(root / 'orders.parquet',
            journal.account.orders,
            {'action': pl.String, 'ticker': pl.String,
             'bucket_end_us': pl.Int64, 'filled_shares': pl.Int64})
        files['positions.parquet'] = _save_rows(root / 'positions.parquet',
            journal.account.closed,
            {'ticker': pl.String, 'entry_us': pl.Int64,
             'exit_us': pl.Int64, 'net_pnl': pl.Float64})
        files['equity.parquet'] = _save_rows(root / 'equity.parquet',
            journal.equity_marks,
            {'clock_us': pl.Int64, 'equity': pl.Float64,
             'period': pl.String})
        metrics_path = root / 'metrics.json'
        metrics_path.write_text(json.dumps(metrics, sort_keys=True),
                                encoding='utf-8')
        files['metrics.json'] = file_hash(metrics_path)
        certificate = {'version': VERSION, 'plan_hash': plan['hash'],
            'files_sha256': files, 'metrics': metrics,
            'status': 'modeled_quote_replay_not_broker_fills'}
        temporary = root / 'complete.json.tmp'
        temporary.write_text(json.dumps(certificate, sort_keys=True),
                             encoding='utf-8')
        temporary.replace(complete)
        return root, metrics
