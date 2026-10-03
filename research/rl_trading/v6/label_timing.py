"""Supervised target alignment; hindsight targets are never live observations."""
VERSION = 'v6-close-target-strict-prior-features-v1'
CONTRACT = {
    'version': VERSION,
    'target_timestamp': 'candle close_us (not candle open)',
    'feature_cutoff': 'source candle close_us < target close_us; exclude target candle',
    'target_availability': 'retrospective hindsight, uses subsequent session prices',
}
