"""Lossless full-state V7 boundary payloads; ClickHouse supplies compression.

Same-day restore never invokes the prior-day constructor/rollover. The envelope
binds the causal engine state to source eligibility and the exact input prefix.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from hashlib import sha256
from math import isfinite
from zoneinfo import ZoneInfo
import re

import msgpack

from src.market_engine.streaming_level_book import StreamingLevelBook
from .contracts import TRADE_POLICY

CODEC_VERSION = 1
MAX_PAYLOAD_BYTES = 64 * 1024**2
NY = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class CheckpointBinding:
    dataset_hash: str
    input_prefix_hash: str
    predecessor_hash: str
    ticker: str
    session: date
    trade_policy: str = TRADE_POLICY

    def __post_init__(self):
        if (any(not re.fullmatch(r"[0-9a-f]{64}", v) for v in
                (self.dataset_hash,self.input_prefix_hash,self.predecessor_hash))
                or not re.fullmatch(r"[A-Z0-9._-]{1,32}",self.ticker)
                or type(self.session) is not date or self.trade_policy != TRADE_POLICY):
            raise ValueError("Invalid strict-source checkpoint binding")


def boundaries(day: date, regular_close: datetime | None = None):
    """Caller supplies pinned-calendar regular close, including early closes."""
    if type(day) is not date:
        raise ValueError("Session must be a date")
    pm = datetime.combine(day,time(9,30),NY)
    ah = datetime.combine(day,time(20),NY)
    close = regular_close or datetime.combine(day,time(16),NY)
    if close.tzinfo is None or close.astimezone(NY).date() != day or not pm < close <= ah:
        raise ValueError("Invalid timezone-aware calendar close")
    return {"PM":pm.astimezone(timezone.utc), "REGULAR":close.astimezone(timezone.utc),
            "AH":ah.astimezone(timezone.utc)}


def encode(engine: StreamingLevelBook, binding: CheckpointBinding, boundary: datetime,
           *, maximum_bytes=MAX_PAYLOAD_BYTES):
    if (boundary.tzinfo is None or not isfinite(boundary.timestamp())
            or engine.ticker != binding.ticker or engine.session != binding.session.isoformat()
            or not engine.start <= engine.as_of <= boundary.timestamp() <= engine.end):
        raise ValueError("Checkpoint contains future input or incompatible session")
    if engine.seed_input_policy not in (engine.input_policy,):
        raise ValueError("Legacy-seed state cannot be published as a causal checkpoint")
    envelope = dict(codec_version=CODEC_VERSION, binding=dict(vars(binding),session=binding.session.isoformat()),
                    boundary_us=round(boundary.timestamp()*1_000_000), state=engine.checkpoint())
    payload = msgpack.packb(envelope,use_bin_type=True)
    if not 0 < len(payload) <= maximum_bytes:
        raise ValueError("Checkpoint payload exceeds the explicit bound")
    return payload, sha256(payload).hexdigest()


def restore(payload: bytes, payload_hash: str, binding: CheckpointBinding, *, maximum_bytes=MAX_PAYLOAD_BYTES,
            expected_boundary_us=None):
    if (not isinstance(payload,bytes) or not 0 < len(payload) <= maximum_bytes
            or sha256(payload).hexdigest() != payload_hash):
        raise ValueError("Checkpoint payload size/hash mismatch")
    value = msgpack.unpackb(payload,raw=False,strict_map_key=True,
                           max_str_len=maximum_bytes,max_bin_len=maximum_bytes,
                           max_array_len=maximum_bytes//8,max_map_len=maximum_bytes//8)
    expected = dict(vars(binding),session=binding.session.isoformat())
    if value.get('codec_version') != CODEC_VERSION or value.get('binding') != expected:
        raise ValueError("Checkpoint source/policy/identity binding mismatch")
    if expected_boundary_us is not None and value.get('boundary_us')!=expected_boundary_us:
        raise ValueError('Checkpoint encoded boundary differs from publication header')
    engine = StreamingLevelBook.restore(value['state'])
    # MessagePack arrays decode to lists. These two engine coordinates are
    # typed pairs; restore their tuple representation without changing state.
    for name in ('high','low'):
        point=getattr(engine,name)
        if point is not None:
            if not isinstance(point,(list,tuple)) or len(point)!=2:
                raise ValueError('Checkpoint has malformed turning-extreme state')
            setattr(engine,name,tuple(point))
    if (engine.ticker != binding.ticker or engine.session != binding.session.isoformat()
            or engine.as_of*1_000_000 > value['boundary_us']
            or not engine.start <= value['boundary_us']/1_000_000 <= engine.end):
        raise ValueError("Checkpoint boundary/state mismatch")
    return engine
