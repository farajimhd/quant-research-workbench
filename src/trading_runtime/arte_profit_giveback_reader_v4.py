"""Bounded cold profit-witness read; no order admission or writer authority."""
from hashlib import sha256
from uuid import UUID

from .arte_journal_commit_v4 import (
    V4CommittedPrefix, load_verified_commit_v4, verified_batch_predecessor,
)
from .arte_journal_writer import _canonical_typed_content, _literal, _rows, canonical_json
from .arte_profit_giveback_v4 import PROFIT_GIVEBACK, restore_profit_giveback


def load_committed_profit_giveback(client, prefix, exit_record_id, *, first_price_source):
    """Verify the whole source batch before returning its exact scalar child.

    The caller supplies an independently verified prefix and certified native
    entry source. The predecessor excludes the current batch, preventing profit
    checkpoint verification from recursively reading its own commit.
    """
    from src.backend.backtest_strategy_certified_price_break import CertifiedPriceReadbackAuthority

    identity = str(UUID(exit_record_id))
    if (type(prefix) is not V4CommittedPrefix
            or type(first_price_source) is not CertifiedPriceReadbackAuthority
            or first_price_source.run_id != prefix.run_id):
        raise ValueError('Profit recovery requires verified native source authority')
    columns = ','.join(name for name, _ in PROFIT_GIVEBACK.columns)
    rows = _rows(client,
        f'SELECT {columns} FROM arte.{PROFIT_GIVEBACK.name} '
        f'WHERE run_id={_literal(prefix.run_id)} '
        f'AND parent_record_id IN (toUUID({_literal(identity)})) '
        'LIMIT 2 FORMAT JSONEachRow')
    if len(rows) != 1:
        raise RuntimeError('Profit recovery requires one unique committed witness')
    row = rows[0]
    batch_id = str(UUID(str(row['batch_id'])))
    if batch_id not in prefix.batch_ids:
        raise RuntimeError('Profit witness is outside the verified prefix')
    predecessor = verified_batch_predecessor(client, prefix, batch_id)
    if predecessor is None:
        raise RuntimeError('Profit witness lacks a preceding checkpoint prefix')
    _, families = load_verified_commit_v4(client, run_id=prefix.run_id,
        batch_id=batch_id, first_price_source=first_price_source,
        verified_prior_prefix=predecessor)
    if not any(family['family_name'] == PROFIT_GIVEBACK.name for family in families):
        raise RuntimeError('Profit witness family is absent from its committed inventory')
    content = {key: value for key, value in row.items() if key != 'content_hash'}
    canonical = _canonical_typed_content(PROFIT_GIVEBACK.name, content, stored_utc=True)
    digest = sha256(canonical_json(canonical).encode()).hexdigest()
    if (digest != row['content_hash'] or row['run_id'] != prefix.run_id
            or str(UUID(str(row['parent_record_id']))) != identity):
        raise RuntimeError('Profit witness differs from its committed typed hash')
    # ClickHouse may quote UInt64 values in JSON. Restore their declared types
    # only after the canonical content has passed its persisted hash check.
    normalized = {**canonical, 'content_hash': row['content_hash']}
    restore_profit_giveback(normalized)
    return normalized
