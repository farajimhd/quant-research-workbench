"""Fixed, execution-eligible 1b opportunities and pair-level missed-entry cost."""
import json
from pathlib import Path
import numpy as np
import polars as pl
from .distributed_assets import sha
from .runtime import windows_path
from .authority import MAXIMUM_SPREAD_BPS, INITIAL_STOP_BID_MARGIN, FIXED_ENTRY_DOLLARS
from .supervision import SETTINGS

MISSED_COST = SETTINGS['missed_pair_penalty']


def entries(item):
    """Eligibility is independent of candidate requests, inventory and prices."""
    if hasattr(item, 'opportunity_entries'):
        return item.opportunity_entries
    actions, pairs, _ = item.supervision
    host = item.host_tensors
    def array(name):
        value = host[name]
        return value.numpy() if hasattr(value, 'numpy') else value
    decision_window=np.arange(len(actions))[:,None] < len(actions)-1
    clock, listing = np.nonzero((actions == 2) & (pairs >= 0) & array('membership') & decision_window)
    table = pl.DataFrame(dict(clock=clock, listing=listing, pair=pairs[clock, listing]))
    bid, ask = array('bid')[clock, listing], array('ask')[clock, listing]
    possible = (array('quote_valid')[clock, listing] & (bid > INITIAL_STOP_BID_MARGIN)
                & (ask <= FIXED_ENTRY_DOLLARS) & (ask-bid <= MAXIMUM_SPREAD_BPS/10000.*bid)
                & (array('ask_size')[clock, listing] >= 1))
    eligible = table.filter(pl.Series(possible)).select('listing', 'pair').unique()
    # Keep the full ENTRY window of each eligible pair. Submission can precede
    # the first available displayed share; the actual first fill must align too.
    result = table.join(eligible, on=['listing', 'pair'], how='semi')
    item.opportunity_entries = result
    return result


def captured(orders, targets):
    buys = orders.filter((pl.col('kind') == 1) & (pl.col('filled_quantity') > 0))
    buys = buys.with_columns(pl.col('submitted_clock', 'first_fill_clock', 'listing').cast(pl.Int64))
    return (buys.join(targets.rename({'clock':'submitted_clock', 'pair':'submitted_pair'}),
                      on=['submitted_clock', 'listing'], how='inner', validate='m:1')
            .join(targets.rename({'clock':'first_fill_clock'}),
                  on=['first_fill_clock', 'listing'], how='inner', validate='m:1')
            .filter(pl.col('pair') == pl.col('submitted_pair'))
            .select('candidate', 'listing', 'pair').unique())


def counts(refs, targets, listings):
    """Scan each shared journal once, preserving candidate-index bindings."""
    result = np.zeros((len(refs), listings), np.int64)
    groups = {}
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict):
            from .order_journal import COLUMNS
            if ref:
                frame = pl.DataFrame(ref, schema=list(COLUMNS), orient='row').with_columns(pl.lit(index).alias('candidate'))
                matched = captured(frame, targets).group_by('listing').len()
                result[index, matched['listing'].to_numpy()] = matched['len'].to_numpy()
            continue
        groups.setdefault(ref['receipt'], []).append((index, ref))
    for name, candidates in groups.items():
        path = Path(name); journal = json.loads(windows_path(path).read_bytes()); seen = []
        for _, ref in candidates:
            local = ref['candidate_index']
            if journal['candidate_sha256'][local] != ref['sha256'] or journal['counts'][local] != ref['orders']:
                raise ValueError('Order journal candidate binding changed')
        for page in journal['pages']:
            file = path.parent/page['file']
            if sha(windows_path(file)) != page['sha256']:
                raise ValueError('Order journal page changed')
            frame = pl.read_parquet(windows_path(file))
            if frame.height != page['rows']:
                raise ValueError('Order journal row count changed')
            seen.append(captured(frame, targets))
        if seen:
            totals = pl.concat(seen).unique().group_by('candidate', 'listing').len()
            lookup = {}
            for local, listing, value in totals.iter_rows():
                lookup.setdefault(local, []).append((listing, value))
            for index, ref in candidates:
                for listing, value in lookup.get(ref['candidate_index'], []):
                    result[index, listing] = value
    return result


def apply(metrics, item):
    if 'label_captured' in metrics:
        raise ValueError('Opportunity costs must be applied exactly once')
    targets = entries(item); u = len(item.listing_ids); b = len(metrics['net_pnl'])
    unique = targets.select('listing', 'pair').unique()
    opportunities = np.bincount(unique['listing'].to_numpy(), minlength=u)
    caught = counts(metrics['execution_orders'], targets, u)
    missed = opportunities[None, :] - caught
    if caught.shape != (b, u) or np.any(missed < 0):
        raise ValueError('Captured entries exceed the fixed opportunity set')
    metrics.update(label_opportunities=[int(opportunities.sum())]*b,
                   label_captured=caught.sum(1).tolist(), label_missed=missed.sum(1).tolist(),
                   label_missed_penalty=(MISSED_COST*missed.sum(1)).tolist(),
                   label_capture_bonus=(MISSED_COST*caught.sum(1)).tolist(),
                   label_ticker_capture_bonus=(MISSED_COST*caught).tolist())
    return metrics
