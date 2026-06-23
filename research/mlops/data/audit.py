from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from research.mlops.data.contracts import EventChunk, MultiModalTemporalBatch


@dataclass(frozen=True, slots=True)
class AuditResult:
    ok: bool
    checks: dict[str, bool]
    details: dict[str, str]


def audit_event_chunk(chunk: EventChunk, *, events_per_chunk: int = 128, header_bytes: int = 14, event_bytes: int = 16) -> AuditResult:
    checks = {
        "header_shape": tuple(chunk.header_uint8.shape) == (header_bytes,),
        "events_shape": tuple(chunk.events_uint8.shape) == (events_per_chunk, event_bytes),
        "source_event_count": not chunk.source_events or len(chunk.source_events) == events_per_chunk,
        "origin_matches_source": not chunk.source_events or int(chunk.source_events[-1].sip_timestamp_us) == int(chunk.origin_timestamp_us),
    }
    return AuditResult(ok=all(checks.values()), checks=checks, details={})


def audit_temporal_batch(batch: MultiModalTemporalBatch) -> AuditResult:
    checks = {
        "batch_nonempty": len(batch.samples) > 0,
        "market_rank": batch.market_embeddings.ndim == 3,
        "market_mask_rank": batch.market_mask.ndim == 2,
        "sample_count_matches": batch.market_embeddings.shape[0] == len(batch.samples) == batch.market_mask.shape[0],
        "finite_market_embeddings": bool(np.isfinite(batch.market_embeddings).all()),
    }
    for name, values in batch.labels.items():
        checks[f"label_{name}_sample_count"] = values.shape[0] == len(batch.samples)
    for name, mask in batch.label_masks.items():
        checks[f"label_mask_{name}_sample_count"] = mask.shape[0] == len(batch.samples)
    return AuditResult(ok=all(checks.values()), checks=checks, details={})

