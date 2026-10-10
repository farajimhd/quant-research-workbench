"""Declared columnar quote confirmation; never Portfolio/OMS exit authority."""
from dataclasses import dataclass
from math import isfinite

import numpy as np
import polars as pl

from .native_channel_qualification import _require_policy_json
from .journal_contract import canonical_json


@dataclass(frozen=True, slots=True)
class NativeChannelQuoteExitPolicy:
    quote_max_age_us: int
    threshold_mode: str
    original_risk_fraction: float | None
    max_rows: int

    def __post_init__(self):
        if (type(self.quote_max_age_us) is not int or self.quote_max_age_us < 0
                or type(self.max_rows) is not int or not 1 <= self.max_rows <= 2**32-1
                or self.threshold_mode not in ('below_reference_close', 'below_original_risk_threshold')):
            raise ValueError('Explicit bounded quote exit declaration required')
        fraction = self.original_risk_fraction
        if self.threshold_mode == 'below_reference_close':
            if fraction is not None:
                raise ValueError('Reference-close confirmation cannot declare original risk fraction')
        elif type(fraction) is not float or not isfinite(fraction) or not 0 <= fraction <= 1:
            raise ValueError('Explicit finite original risk fraction required')

    def payload(self):
        self.__post_init__()
        return dict(rule='native-channel-quote-exit-confirmation@1',
            quote_max_age_us=self.quote_max_age_us, threshold_mode=self.threshold_mode,
            original_risk_fraction=self.original_risk_fraction, max_rows=self.max_rows,
            quote_clock='microseconds since source-day midnight',
            comparison='bid strictly below declared threshold', missing='no_new_exit')


def parse_native_channel_quote_exit_policy(value):
    """Require complete immutable semantics, without implicit execution defaults."""
    _require_policy_json(value)
    required={'rule','quote_max_age_us','threshold_mode','original_risk_fraction',
              'max_rows','quote_clock','comparison','missing'}
    if type(value) is not dict or set(value)!=required:
        raise ValueError('Complete channel quote exit declaration required')
    policy=NativeChannelQuoteExitPolicy(value['quote_max_age_us'],value['threshold_mode'],
        value['original_risk_fraction'],value['max_rows'])
    if canonical_json(value)!=canonical_json(policy.payload()):
        raise ValueError('Channel quote clock, comparison or missing semantics differ')
    return policy


def qualify_native_channel_quote_exits(rows, *, policy):
    """Intersect completed evidence with current ownership and causal quotes.

    The native owner must bind each row to certified features, exact fills and
    current financial state. This pure mask grants no checkpoint or order admission.
    Canonical price integers use the shared 1/10000-dollar representation.
    """
    if type(policy) is not NativeChannelQuoteExitPolicy:
        raise ValueError('Exact declared channel quote policy required')
    policy.__post_init__()
    schema = dict(qualified=pl.Boolean, held=pl.Boolean, pending_exit=pl.Boolean,
        decision_day_ms=pl.Int64, quote_day_us=pl.Int64, bid_int=pl.UInt64, ask_int=pl.UInt64)
    if policy.threshold_mode == 'below_reference_close':
        schema['reference_close_int'] = pl.UInt64
        threshold = pl.col('reference_close_int')
        threshold_valid = pl.col('reference_close_int') > 0
    else:
        schema.update(original_ask=pl.Float64, initial_stop=pl.Float64)
        ask, stop = pl.col('original_ask'), pl.col('initial_stop')
        threshold = (ask - policy.original_risk_fraction * (ask-stop)) * 10_000
        threshold_valid = ask.is_finite() & stop.is_finite() & (stop>0) & (ask>stop)
    if (type(rows) is not pl.DataFrame or rows.height > policy.max_rows
            or any(rows.schema.get(k) != v for k,v in schema.items())):
        raise ValueError('Bounded exact quote and financial evidence schema required')
    age = pl.col('decision_day_ms')*1000 - pl.col('quote_day_us')
    valid = (pl.col('qualified') & pl.col('held') & ~pl.col('pending_exit')
        & pl.col('decision_day_ms').is_between(0,86_400_000)
        & (pl.col('quote_day_us')>=0) & age.is_between(0,policy.quote_max_age_us)
        & (pl.col('bid_int')>0) & (pl.col('ask_int')>=pl.col('bid_int'))
        & threshold_valid & (pl.col('bid_int')<threshold)).fill_null(False)
    result = rows.select(valid.alias('exit_eligible'))['exit_eligible'].to_numpy()
    return np.frombuffer(result.tobytes(),dtype=bool)
