"""Bounded native scalar readback, separate from complete commit verification."""
from uuid import NAMESPACE_URL, UUID, uuid5

from .arte_liquidity_fade_failure_v4 import LIQUIDITY_FADE_FAILURE, restore_liquidity_fade_failure


def load_liquidity_fade_failure(client, prefix, exit_record_id):
    """Verify the stored hash before replaying exact integer/price scalars.

    The caller must independently verify the complete commit prefix, including
    entry, producer and held/pending financial authority. This lookup neither
    performs those checks nor grants publication, resume or live admission.
    """
    from .arte_journal_commit_v4 import V4CommittedPrefix
    from .arte_journal_writer import _literal, _rows
    from .arte_intent_projection import _verify_stored_row
    parent_id = str(UUID(str(exit_record_id)))
    if (type(prefix) is not V4CommittedPrefix or not prefix.batch_ids
            or prefix.last_batch_id != prefix.batch_ids[-1]
            or type(prefix.last_sequence) is not int or prefix.last_sequence < 1
            or prefix.status not in {'running', 'completed', 'stopped', 'failed'}):
        raise ValueError('Liquidity cold read requires an independently verified committed prefix')
    columns = ','.join(name for name, _ in LIQUIDITY_FADE_FAILURE.columns)
    rows = _rows(client, f'SELECT {columns} FROM arte.{LIQUIDITY_FADE_FAILURE.name} '
        f'WHERE run_id={_literal(prefix.run_id)} '
        f'AND parent_record_id=toUUID({_literal(parent_id)}) LIMIT 2 FORMAT JSONEachRow')
    if (len(rows) != 1 or str(rows[0]['batch_id']) not in prefix.batch_ids
            or rows[0]['run_id'] != prefix.run_id
            or str(rows[0]['parent_record_id']) != parent_id):
        raise RuntimeError('Liquidity witness lacks its unique committed prefix row')
    canonical = _verify_stored_row(LIQUIDITY_FADE_FAILURE.name, rows[0])
    expected_id = str(uuid5(NAMESPACE_URL, f'{prefix.run_id}:{parent_id}:liquidity-fade-failure'))
    if str(canonical['record_id']) != expected_id:
        raise RuntimeError('Liquidity witness differs from its deterministic child identity')
    # Canonical native UInt conversion is exact (including JSON UInt64 strings).
    # Never pass counts through Float64, infer missing zero, or round a count.
    unsigned = {name for name, kind in LIQUIDITY_FADE_FAILURE.columns if kind.startswith('UInt')}
    adapted = {key: int(value) if key in unsigned else value for key, value in canonical.items()}
    return rows[0], restore_liquidity_fade_failure(adapted)
