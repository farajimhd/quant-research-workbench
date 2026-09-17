"""Post-run comparison only. Never imported by the strategy decision path."""
from math import isfinite


def compare_positions(labels, positions):
    """Compare normalized completed long fills with their containing MACD episodes.

    Both inputs use UTC epoch seconds and entry/exit prices. Positions optionally
    carry total fees and quantity; missing fees are unavailable, never zero by
    assumption. Caller must bind identical ticker/session/source provenance.
    Open positions must be reconciled before calling; report them separately.
    """
    labels = sorted((dict(p) for p in labels if p['direction'] == 'long'),
                    key=lambda p: p['macd_open'])
    for rows in (labels, positions):
        for p in rows:
            if not all(isfinite(p[k]) for k in ('entry_time','exit_time','entry_price','exit_price')):
                raise ValueError('Comparison requires finite timestamps and prices')
            if p['entry_time'] > p['exit_time'] or min(p['entry_price'],p['exit_price']) <= 0:
                raise ValueError('Comparison requires completed chronological positions')
    if any(p['macd_close'] <= p['macd_open'] for p in labels):
        raise ValueError('Invalid hindsight MACD interval')
    if any(a['macd_close'] > b['macd_open'] for a,b in zip(labels,labels[1:])):
        raise ValueError('Ambiguous overlapping hindsight episodes')
    matches = {p['macd_open']: [] for p in labels}
    unmatched = []
    for position in positions:
        label = next((p for p in labels if p['macd_open'] <= position['entry_time'] < p['macd_close']), None)
        if label is None:
            unmatched.append(dict(position))
        else:
            matches[label['macd_open']].append(position)
    comparisons = []
    for label in labels:
        group = matches[label['macd_open']]
        rows = []
        for p in group:
            gross = p['exit_price']-p['entry_price']
            target = label['exit_price']-label['entry_price']
            quantity = p.get('quantity')
            fees = p.get('fees')
            if quantity is not None and (not isfinite(quantity) or quantity <= 0):
                raise ValueError('Position quantity must be positive')
            if fees is not None and (not isfinite(fees) or fees < 0):
                raise ValueError('Position fees must be nonnegative')
            rows.append(dict(entry_delay_seconds=p['entry_time']-label['entry_time'],
                exit_delay_seconds=p['exit_time']-label['exit_time'],
                entry_slippage_bps=(p['entry_price']/label['entry_price']-1)*10000,
                exit_giveback_bps=(1-p['exit_price']/label['exit_price'])*10000,
                gross_per_share=gross, gross_capture_fraction=gross/target if target > 0 else None,
                net_pnl=gross*quantity-fees if fees is not None and quantity is not None else None))
        comparisons.append(dict(label=label, missed=not group, attempts=rows))
    return dict(hindsight_long_count=len(labels), completed_position_count=len(positions),
        matched_episode_count=sum(bool(v) for v in matches.values()),
        missed_episode_count=sum(not v for v in matches.values()),
        unmatched_position_count=len(unmatched), unmatched_positions=unmatched, comparisons=comparisons,
        limitation='Post-run gross price-label comparison; not an executable upper bound or holdout acceptance.')
