"""Frozen AAPL-first causal level-reaction experiment."""
VERSION = 'ticker-level-reaction-v1'
CONTRACT = dict(version=VERSION, cadence_seconds=1, horizon_seconds=60,
                price_max_age_seconds=5, future_max_gap_seconds=5,
                warmup_seconds=60, rejection_range_multiple=2.,
                min_rejection_ticks=2, breakout_hold_closes=2,
                labels=['not_reached','rejected','broken','unresolved'],
                classifier=dict(max_iter=60,max_leaf_nodes=15,min_samples_leaf=200,
                                learning_rate=.08,l2_regularization=10.,random_state=17,
                                early_stopping=False),
                calibration='multinomial_logistic_on_log_probabilities',
                clock='completed SIP-second end; no execution-clock latency simulation')
