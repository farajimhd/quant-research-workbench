"""Versioned decision/fill time contract, carried in tape and search provenance.

At decision boundary t, row t contains the completed interval [t-1s, t).
In candle-OPEN indexing that is candle t-1, never the candle beginning at t.
Full interval execution evidence belongs only to the broker processing orders
submitted at earlier boundaries. This contract does not assert intrabar fills.
"""

import json
from hashlib import sha256

TIMING_CONTRACT = {
    "version": "completed-boundary-v3-1",
    "clock_seconds": 1,
    "assumed_publication_latency_seconds": 0,
    "clock_label": "interval_end_utc_seconds",
    "source_interval": "[t-1s,t)",
    "decision_features": "completed_interval_end<=decision_t",
    "open_indexed_feature_cutoff": "candle_t_minus_1",
    "new_order_first_execution_interval": "[decision_t,decision_t+1s)",
    "ledger_timestamp": "filled_interval_end_not_intrabar_time",
    "protection_amendments": "effective_only_after_current_interval_processing",
}


def timing_fingerprint():
    """Stable integrity seal for the exact declared clock convention."""
    return sha256(json.dumps(TIMING_CONTRACT, sort_keys=True).encode()).hexdigest()
