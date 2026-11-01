"""Ranked inference slots and execution-only continuation; no data acquisition."""
import numpy as np

TOP_K = 5


def selected_columns(top_indices, listing_ids):
    """Preserve saved volume ranking, including tie order and empty slots."""
    top = np.asarray(top_indices)
    union = np.asarray(listing_ids)
    if top.ndim != 2 or top.shape[1] < TOP_K:
        raise ValueError('Saved ranked list must contain at least five slots')
    if union.ndim != 1 or not len(union) or np.any(np.diff(union) <= 0):
        raise ValueError('Sorted unique session listing identities required')
    selected = top[:, :TOP_K]
    columns = np.searchsorted(union, selected)
    known = selected >= 0
    safe = columns.clip(0, len(union)-1)
    if np.any(known & (union[safe] != selected)):
        raise ValueError('Ranked ticker missing from certified session union')
    if any(len(np.unique(row[row >= 0])) != np.count_nonzero(row >= 0) for row in selected):
        raise ValueError('Duplicate ticker in ranked slots')
    return np.where(known, columns, -1).astype(np.int32)


def execution_columns(top, quantities, order_kinds):
    """CPU oracle for the dynamic execution union; never truncate obligations."""
    top = np.asarray(top)
    q, orders = np.asarray(quantities), np.asarray(order_kinds)
    if q.shape != orders.shape or q.ndim != 2:
        raise ValueError('Candidate by ticker account state required')
    active = ((q > 1e-12) | (orders != 0)).any(axis=0)
    valid = top[top >= 0]
    if np.any(valid >= q.shape[1]):
        raise ValueError('Ranked ticker outside execution state')
    active[valid] = True
    return np.flatnonzero(active)
