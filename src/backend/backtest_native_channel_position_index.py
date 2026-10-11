"""Columnar per-acquisition feature screening; no quote, cash or order authority."""
from dataclasses import dataclass,field

import numpy as np
import polars as pl

from .backtest_native_channel_sequences import PreparedNativeChannelSequence
from src.trading_runtime.native_acquisition_clock import NativeAcquisitionClock


def _acquisition_key(clock):
    if type(clock) is not NativeAcquisitionClock:
        raise ValueError('Native feature index requires an exact acquisition clock')
    clock.__post_init__()
    return (clock.session_date,clock.account_id,clock.assignment_id,clock.ticker,
        clock.source_entry_intent_id,clock.source_group_id,clock.first_execution_id,
        clock.acquisition_day_us,clock.first_execution_time)


@dataclass(frozen=True,slots=True)
class NativeChannelPositionIndex:
    """Immutable feature predicate indexed by exact declared decision boundary.

    Additional fills preserve the original acquisition key. Reentry requires a
    new index. No future financial state, quote or portfolio admission is cached.
    """
    acquisition_key: tuple
    boundaries_bytes: bytes
    qualified_bytes: bytes
    prepared: PreparedNativeChannelSequence = field(repr=False,compare=False)

    def __post_init__(self):
        if (type(self.prepared) is not PreparedNativeChannelSequence
                or type(self.acquisition_key) is not tuple or len(self.acquisition_key)!=9
                or type(self.boundaries_bytes) is not bytes or type(self.qualified_bytes) is not bytes
                or not self.qualified_bytes or len(self.boundaries_bytes)!=8*len(self.qualified_bytes)):
            raise ValueError('Native feature index requires complete immutable boundary and predicate storage')
        boundaries=np.frombuffer(self.boundaries_bytes,dtype=np.int64)
        if np.any(boundaries<0) or np.any(boundaries[1:]<=boundaries[:-1]) or any(
                value not in (0,1) for value in self.qualified_bytes):
            raise ValueError('Native feature index boundaries or predicates are invalid')

    def candidate_at(self,decision_day_ms,*,acquisition,prepared):
        if (type(decision_day_ms) is not int or decision_day_ms<0
                or prepared is not self.prepared
                or _acquisition_key(acquisition)!=self.acquisition_key):
            raise ValueError('Native feature lookup differs from original position acquisition')
        boundaries=np.frombuffer(self.boundaries_bytes,dtype=np.int64)
        offset=int(np.searchsorted(boundaries,decision_day_ms))
        if offset==len(boundaries) or int(boundaries[offset])!=decision_day_ms:
            raise ValueError('Native feature lookup is outside its exact declared decision schedule')
        return bool(np.frombuffer(self.qualified_bytes,dtype=bool)[offset])


def prepare_native_channel_position_index(prepared,decisions,*,acquisition):
    """Compile once with causal availability checks for every decision row.

    The held mask here selects feature screening only; actual holdings and
    pending exits must be independently checked at each native decision.
    """
    key=_acquisition_key(acquisition)
    if (type(prepared) is not PreparedNativeChannelSequence or type(decisions) is not pl.DataFrame
            or not 0<decisions.height<=prepared.policy.max_decisions
            or decisions.schema.get('ticker')!=pl.String
            or decisions.schema.get('session_date')!=pl.String
            or decisions.schema.get('decision_day_ms')!=pl.Int64
            or decisions.filter((pl.col('ticker')!=acquisition.ticker)
                | (pl.col('session_date')!=acquisition.session_date.isoformat())).height):
        raise ValueError('Native position feature index requires one exact bounded acquisition scope')
    ordered=decisions.sort('decision_day_ms')
    if ordered['decision_day_ms'].n_unique()!=ordered.height:
        raise ValueError('Native position feature schedule repeats a decision boundary')
    evidence=prepared.decision_evidence(ordered,np.ones(ordered.height,dtype=bool),
        np.full(ordered.height,acquisition.acquisition_day_us,dtype=np.int64))
    return NativeChannelPositionIndex(key,ordered['decision_day_ms'].to_numpy().tobytes(),
        evidence['qualified'].to_numpy().tobytes(),prepared)


def prepare_native_channel_acquisition_index(prepared, *, acquisition, through_day_boundary_ms):
    """Compile the declared decision grid once, starting at the actual first fill.

    The execution horizon may be shorter than the certified source horizon.
    Full source provenance is preserved; each feature predicate remains as-of
    its own completed decision boundary.
    """
    _acquisition_key(acquisition)
    if (type(prepared) is not PreparedNativeChannelSequence
            or type(through_day_boundary_ms) is not int
            or not 0 <= through_day_boundary_ms <= prepared.packet.request.through_day_boundary_ms):
        raise ValueError('Acquisition schedule requires its bounded certified source horizon')
    interval = prepared.policy.inputs.decision_interval_ms
    first = ((acquisition.acquisition_day_us + interval * 1000 - 1)
             // (interval * 1000)) * interval
    count = (through_day_boundary_ms - first) // interval + 1
    if not 0 < count <= prepared.policy.max_decisions:
        raise ValueError('Acquisition schedule is empty or exceeds its declared decision bound')
    scope = prepared.scope.filter(
        (pl.col('ticker') == acquisition.ticker)
        & (pl.col('session_date') == acquisition.session_date.isoformat()))
    if scope.height != 1:
        raise ValueError('Acquisition schedule lacks one exact installed source identity')
    decisions = scope.join(pl.DataFrame({'decision_day_ms': pl.Series(
        range(first, first + count * interval, interval), dtype=pl.Int64)}), how='cross')
    return prepare_native_channel_position_index(prepared, decisions, acquisition=acquisition)
