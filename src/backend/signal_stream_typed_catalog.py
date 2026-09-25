"""Inactive immutable typed catalog for Python Signal Stream evidence.

This closes one producer input boundary only. It does not persist occurrences
or admit the typed Signal Stream path to live trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from math import isfinite
from typing import Any, Mapping

from src.backend.data_field_contracts import field_instance_ref, interval_expression
from src.trading_runtime.journal_contract import canonical_json


CATALOG_VERSION = 1
RESERVED_PARENT_NAMES = frozenset({
    "schema_version", "event_id", "signal_id", "signal_stream_id",
    "signal_stream_name", "definition_revision", "configured_revision", "ticker",
    "company_name", "issuer_name", "country", "logo_url", "live_news_recency",
    "live_news_count", "today_news_count", "latest_news_id", "latest_news_title",
    "news_synthesis", "news_synthesis_class", "news_synthesis_purpose",
    "news_synthesis_origin", "news_synthesis_direction", "news_synthesis_event",
    "news_synthesis_text", "news_ai_review", "news_ai_review_state",
    "news_ai_eligibility", "news_ai_sentiment", "news_ai_positive_probability",
    "news_ai_negative_probability", "news_deepfm_probability", "news_deepfm_eligibility",
    "news_ai_reaction", "news_ai_reaction_state", "news_ai_reaction_confidence",
    "news_ai_reaction_up_probability", "news_ai_reaction_down_probability",
    "news_ai_reaction_regime", "sec_recency", "sec_count", "sec_labels",
    "sec_synthesis_count", "sec_synthesis_direction", "sec_review_status",
    "sec_review_fundamental_direction", "conid", "event_time", "effective_at",
    "available_at", "signal_state", "trigger_policy", "matched_rule_set_ids",
    "evidence", "field_evidence", "evidence_null_reasons",
})
_VALUE_TYPES = {
    "number": "number", "float": "number", "integer": "integer",
    "boolean": "boolean", "text": "text", "string": "text",
    "timestamp": "timestamp",
}


@dataclass(frozen=True, slots=True)
class FieldBinding:
    column_id: str
    source_kind: str
    source_id: str
    source_path: str
    provenance: str
    query_plan_id: str
    available_at_contract: str
    field_ref: str
    instance_ref: str
    value_type: str
    interval: str
    aggregation: str


@dataclass(frozen=True, slots=True)
class SignalFieldCatalog:
    schema_version: int
    configuration_revision: str
    signal_stream_id: str
    bindings: tuple[FieldBinding, ...]
    content_hash: str


def build_signal_field_catalog(
    stream: Mapping[str, Any], columns: Mapping[str, Mapping[str, Any]], *,
    configuration_revision: str,
) -> SignalFieldCatalog:
    """Pin exact selected columns, instance IDs, types, and source revision."""
    stream_id = stream.get("signal_stream_id")
    selected = stream.get("columns")
    if (not isinstance(configuration_revision, str) or not configuration_revision
            or not isinstance(stream_id, str) or not stream_id
            or not isinstance(selected, list) or not selected
            or any(not isinstance(column_id, str) for column_id in selected)
            or len(selected) > 4096 or len(selected) != len(set(selected))):
        raise ValueError("invalid typed Signal Stream catalog identity")
    bindings = []
    instances = set()
    for column_id in selected:
        if (not isinstance(column_id, str) or not column_id
                or column_id in RESERVED_PARENT_NAMES or column_id not in columns):
            raise ValueError("unknown or reserved Signal Stream column ID")
        column = columns[column_id]
        if column.get("column_id") != column_id:
            raise ValueError("Signal Stream column catalog identity differs")
        value_type = _VALUE_TYPES.get(str(column.get("value_type") or "").lower())
        if value_type is None:
            raise ValueError("Signal Stream evidence value type is not closed")
        source_id = column.get("source_id")
        source_kind = column.get("source_kind")
        field_ref = str(column.get("field_ref") or "")
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("Signal Stream source ID is missing")
        if source_kind not in {"data_field", "rule_set"}:
            raise ValueError("Signal Stream source kind is unsupported")
        if source_kind == "data_field" and not field_ref:
            raise ValueError("Data Field evidence requires a field reference")
        if source_kind == "rule_set" and (value_type != "boolean" or field_ref):
            raise ValueError("rule-set evidence must be boolean")
        provenance = tuple(column.get(key) for key in (
            "source_path", "provenance", "query_plan_id", "available_at"))
        if any(not isinstance(value, str) or not value for value in provenance):
            raise ValueError("Signal Stream provenance catalog is incomplete")
        interval = interval_expression(dict(stream.get("column_intervals") or {}).get(column_id))
        aggregation = str(dict(stream.get("column_aggregations") or {}).get(column_id) or "")
        instance = field_instance_ref(field_ref, interval, aggregation) if field_ref else ""
        if instance:
            if instance in instances:
                raise ValueError("duplicate Signal Stream field instance")
            instances.add(instance)
        bindings.append(FieldBinding(column_id, source_kind, source_id,
                                     *provenance, field_ref, instance,
                                     value_type, interval, aggregation))
    fingerprint = {
        "schema_version": CATALOG_VERSION,
        "configuration_revision": configuration_revision,
        "signal_stream_id": stream_id,
        "bindings": [dict(column_id=b.column_id, source_kind=b.source_kind,
                          source_id=b.source_id, source_path=b.source_path,
                          provenance=b.provenance, query_plan_id=b.query_plan_id,
                          available_at_contract=b.available_at_contract,
                          field_ref=b.field_ref, instance_ref=b.instance_ref,
                          value_type=b.value_type, interval=b.interval,
                          aggregation=b.aggregation) for b in bindings],
    }
    return SignalFieldCatalog(CATALOG_VERSION, configuration_revision, stream_id,
                              tuple(bindings), sha256(canonical_json(fingerprint).encode()).hexdigest())


def _scalar(value: Any, value_type: str) -> bool:
    if value is None:
        return True
    if value_type == "boolean":
        return type(value) is bool
    if value_type == "integer":
        return type(value) is int and -(2**63) <= value < 2**63
    if value_type == "number":
        if type(value) is int:
            return -(2**53) <= value <= 2**53
        return type(value) is float and isfinite(value)
    if value_type == "text":
        return isinstance(value, str)
    if value_type == "timestamp":
        try:
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
                value.replace("Z", "+00:00"))
        except (TypeError, ValueError, AttributeError):
            return False
        return parsed.tzinfo is not None
    return False


def validate_typed_occurrence_evidence(
    occurrence: Mapping[str, Any], catalog: SignalFieldCatalog,
    stream: Mapping[str, Any], columns: Mapping[str, Mapping[str, Any]],
) -> None:
    """Reject drift, non-scalars, missing evidence, or top-level collisions."""
    if build_signal_field_catalog(
        stream, columns, configuration_revision=catalog.configuration_revision) != catalog:
        raise ValueError("typed Signal Stream catalog drift")
    if occurrence.get("signal_stream_id") != catalog.signal_stream_id:
        raise ValueError("typed Signal Stream source differs")
    evidence = occurrence.get("evidence")
    field_evidence = occurrence.get("field_evidence")
    null_reasons = occurrence.get("evidence_null_reasons")
    if not all(isinstance(value, Mapping) for value in (evidence, field_evidence, null_reasons)):
        raise ValueError("typed Signal Stream evidence families are missing")
    if set(evidence) != {binding.column_id for binding in catalog.bindings}:
        raise ValueError("typed Signal Stream evidence columns differ")
    if set(field_evidence) != {binding.instance_ref for binding in catalog.bindings
                               if binding.instance_ref}:
        raise ValueError("typed Signal Stream field instances differ")
    if set(null_reasons) - set(evidence):
        raise ValueError("typed Signal Stream null reasons are unmodeled")
    for binding in catalog.bindings:
        value = evidence[binding.column_id]
        if not _scalar(value, binding.value_type) or occurrence.get(binding.column_id) != value:
            raise ValueError("typed Signal Stream evidence value is invalid")
        if binding.column_id in null_reasons:
            if value not in (None, "") or not isinstance(null_reasons[binding.column_id], str):
                raise ValueError("typed Signal Stream null reason is invalid")
        elif value in (None, ""):
            raise ValueError("typed Signal Stream null reason is missing")
        if not binding.instance_ref:
            continue
        field = field_evidence[binding.instance_ref]
        if (not isinstance(field, Mapping)
                or set(field) != {"field_ref", "interval", "aggregation", "value",
                                  "available_at", "null_reason"}
                or field["field_ref"] != binding.field_ref
                or field["interval"] != binding.interval
                or field["aggregation"] != binding.aggregation
                or not _scalar(field["value"], binding.value_type)
                or field["available_at"] is not None
                and not _scalar(field["available_at"], "timestamp")
                or field["null_reason"] is not None
                and not isinstance(field["null_reason"], str)):
            raise ValueError("typed Signal Stream field evidence differs")
