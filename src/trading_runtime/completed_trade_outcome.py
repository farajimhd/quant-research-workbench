"""Causal completed-position evidence from the canonical execution ledger."""
from .performance import derive_trade_episodes


def completed_trade_outcome(executions, *, account_id, conid, run_id,
                            entry_at, observed_at, confirmation_seconds):
    if observed_at.tzinfo is None:
        raise ValueError('Completed outcome requires an aware observation clock')
    unavailable = dict(status='unavailable', entry_at=entry_at,
        observed_at=observed_at.timestamp())
    rows = [e for e in executions if e.account_id == account_id and e.instrument.conid == conid
        and e.source_event_time <= observed_at]
    if not rows:
        return dict(unavailable, reason='no_canonical_fills')
    if len({e.execution_id for e in rows}) != len(rows):
        raise ValueError('Duplicate canonical execution identity')
    episodes = [e for e in derive_trade_episodes(rows)
        if entry_at <= e.opened_at.timestamp() < entry_at+confirmation_seconds
        and e.closed_at <= observed_at]
    if len(episodes) != 1:
        return dict(unavailable, reason='completed_episode_not_uniquely_matched')
    episode = episodes[0]
    selected = [e for e in rows if e.execution_id in episode.execution_ids]
    if not selected or any(e.run_id != run_id for e in selected):
        return dict(unavailable, reason='execution_run_identity_mismatch')
    if any(e.commission_status != 'final' or e.commission is None or not e.commission.is_finite()
           or e.commission_currency != e.instrument.currency for e in selected):
        return dict(unavailable, reason='complete_same_currency_fees_unavailable')
    return dict(status='verified', entry_at=entry_at, observed_at=observed_at.timestamp(),
        episode_id=episode.episode_id, opened_at=episode.opened_at.timestamp(),
        closed_at=episode.closed_at.timestamp(), gross_pnl=float(episode.gross_pnl),
        fees=float(episode.fees), net_pnl=float(episode.net_pnl),
        execution_ids=list(episode.execution_ids), authority='canonical_flat_to_flat_executions')
