"""Point-in-time reference snapshot alignment; no source or trading authority."""
import polars as pl

CLOCKS = ('published_at_ms', 'recorded_at_ms', 'inserted_at_ms',
          'identity_recorded_at_ms')
PROVENANCE = ('snapshot_id', 'source_hash')


def reference_channels(decisions, snapshots, *, value_columns, max_age_ms,
                       max_rows):
    """Align upstream reference snapshots to UTC epoch-millisecond decisions.

    Each snapshot is a complete upstream-selected view, not an individual XBRL
    fact. Period/unit selection and source certification remain upstream. All
    four knowledge clocks must pass; future-effective events may be known but
    do not become effective or adjust candles through this projection.
    """
    if (type(value_columns) is not tuple or not value_columns or
            any(type(c) is not str or not c for c in value_columns) or
            len(set(value_columns)) != len(value_columns) or
            set(value_columns) & set((*CLOCKS, *PROVENANCE, 'symbol_id',
                                     'decision_at_ms', 'available_at_ms', '_order',
                                     'reference_available', 'reference_age_ms')) or
            type(max_age_ms) is not int or max_age_ms < 0 or
            type(max_rows) is not int or max_rows < 1):
        raise ValueError('Explicit distinct channels and integer bounds required')
    if decisions.height > max_rows or snapshots.height > max_rows:
        raise ValueError('Reference packet exceeds declared bound')
    left = decisions.select('symbol_id', 'decision_at_ms')
    right = snapshots.select('symbol_id', *CLOCKS, *PROVENANCE, *value_columns)
    if (left.schema['symbol_id'] != pl.String or any(
            right.schema[c] != pl.String for c in ('symbol_id', *PROVENANCE))):
        raise ValueError('String reference identity and provenance required')
    for frame, columns in ((left, left.columns),
                           (right, ('symbol_id', *CLOCKS, *PROVENANCE))):
        if any(frame[c].null_count() for c in columns):
            raise ValueError('Reference identity, provenance or clock is missing')
    if left.schema['decision_at_ms'] != pl.Int64 or any(
            right.schema[c] != pl.Int64 for c in CLOCKS):
        raise ValueError('UTC epoch millisecond Int64 clocks required')
    if left.n_unique() != left.height:
        raise ValueError('Duplicate reference decision identity')
    if left.filter(pl.col('symbol_id').str.len_chars() == 0).height or right.filter(pl.col('symbol_id').str.len_chars() == 0).height or any(
            right.filter(pl.col(c).str.len_chars() == 0).height for c in PROVENANCE):
        raise ValueError('Empty reference identity or provenance')
    right = right.with_columns(pl.max_horizontal(CLOCKS).alias('available_at_ms'))
    if right.select('symbol_id', 'available_at_ms').n_unique() != right.height:
        raise ValueError('Ambiguous reference snapshots at one availability clock')
    joined = left.with_row_index('_order').sort('decision_at_ms').join_asof(
        right.sort('available_at_ms'), left_on='decision_at_ms',
        right_on='available_at_ms', by='symbol_id', strategy='backward',
        check_sortedness=False,
    )
    age = pl.col('decision_at_ms') - pl.col('available_at_ms')
    known = (age.is_not_null() & (age >= 0) & (age <= max_age_ms)).fill_null(False)
    return joined.with_columns(
        known.alias('reference_available'),
        pl.when(known).then(age).alias('reference_age_ms'),
        *[pl.when(known).then(pl.col(c)).otherwise(None).alias(c)
          for c in (*CLOCKS, *PROVENANCE, 'available_at_ms', *value_columns)],
    ).sort('_order').drop('_order')
