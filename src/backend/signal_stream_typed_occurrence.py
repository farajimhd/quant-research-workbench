"""Inactive normalized typed projection of Python Signal Stream occurrences.

Only occurrences passing the pinned closed field-instance catalog are accepted.
No ClickHouse connection, DDL, or INSERT occurs in this module.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from hashlib import sha256
from math import isfinite
from typing import Any
from zoneinfo import ZoneInfo

from src.backend.signal_stream_typed_catalog import (
    SignalFieldCatalog, _scalar, validate_typed_occurrence_evidence,
)
from src.backend.signal_stream_typed_cursor import TypedTable
from src.trading_runtime.journal_contract import canonical_json


_TEXT = (
    "signal_stream_name", "company_name", "issuer_name", "country", "logo_url",
    "live_news_recency", "sec_recency", "sec_labels", "sec_synthesis_direction",
    "sec_review_status", "sec_review_fundamental_direction", "signal_state",
    "trigger_policy",
)
_UINT = ("schema_version", "configured_revision", "live_news_count",
         "today_news_count", "sec_count", "sec_synthesis_count", "conid")
_OPTIONAL_FLOAT = (
    "news_synthesis", "news_ai_review", "news_ai_positive_probability",
    "news_ai_negative_probability", "news_deepfm_probability",
    "news_ai_reaction", "news_ai_reaction_confidence",
    "news_ai_reaction_up_probability", "news_ai_reaction_down_probability",
)
_OPTIONAL_TEXT = (
    "latest_news_id", "latest_news_title", "news_synthesis_class",
    "news_synthesis_purpose", "news_synthesis_origin", "news_synthesis_direction",
    "news_synthesis_event", "news_synthesis_text", "news_ai_review_state",
    "news_ai_eligibility", "news_ai_sentiment", "news_deepfm_eligibility",
    "news_ai_reaction_state", "news_ai_reaction_regime",
)
_OPTIONAL = {**dict.fromkeys(_OPTIONAL_FLOAT, "Float64"),
             **dict.fromkeys(_OPTIONAL_TEXT, "String")}
_TIME = ("event_time", "effective_at", "available_at")
_BASE = frozenset(_TEXT) | frozenset(_UINT) | frozenset(_OPTIONAL) | {
    "event_id", "signal_id", "signal_stream_id", "definition_revision", "ticker",
    *_TIME, "matched_rule_set_ids", "evidence", "field_evidence",
    "evidence_null_reasons",
}
_ID = (("session_key", "Date"), ("event_id", "FixedString(64)"))
_VALUE = (("value_type", "LowCardinality(String)"), ("value_is_null", "Bool"),
          ("value_float", "Nullable(Float64)"), ("value_int", "Nullable(Int64)"),
          ("value_bool", "Nullable(Bool)"), ("value_text", "Nullable(String)"),
          ("value_time", "Nullable(DateTime64(6, 'UTC'))"))
PARENT = TypedTable(
    "signal_stream_python_occurrence_v1",
    _ID + (("configuration_revision", "String"), ("catalog_hash", "FixedString(64)"),
           ("signal_stream_id", "String"), ("definition_revision", "String"),
           ("ticker", "String"),)
    + tuple((key, "String") for key in _TEXT)
    + tuple((key, "UInt64") for key in _UINT)
    + tuple((key, "DateTime64(6, 'UTC')") for key in _TIME)
    + tuple((f"{key}_present", "Bool") for key in _OPTIONAL)
    + tuple((f"{key}_is_int", "Bool") for key in _OPTIONAL_FLOAT)
    + tuple((f"{key}_float", "Nullable(Float64)") for key in _OPTIONAL_FLOAT)
    + tuple((f"{key}_int", "Nullable(Int64)") for key in _OPTIONAL_FLOAT)
    + tuple((key, "Nullable(String)") for key in _OPTIONAL_TEXT)
    + (("rule_count", "UInt32"), ("rule_hash", "FixedString(64)"),
       ("column_count", "UInt32"), ("column_hash", "FixedString(64)"),
       ("field_count", "UInt32"), ("field_hash", "FixedString(64)"),
       ("content_hash", "FixedString(64)")),
    "event_id",
)
RULE = TypedTable(
    "signal_stream_python_rule_v1",
    _ID + (("ordinal", "UInt32"), ("rule_set_id", "String"),
           ("content_hash", "FixedString(64)")),
    "event_id, ordinal",
)
COLUMN = TypedTable(
    "signal_stream_python_column_evidence_v1",
    _ID + (("ordinal", "UInt32"), ("column_id", "String"),) + _VALUE
    + (("null_reason", "Nullable(String)"), ("content_hash", "FixedString(64)")),
    "event_id, ordinal",
)
FIELD = TypedTable(
    "signal_stream_python_field_evidence_v1",
    _ID + (("ordinal", "UInt32"), ("field_instance_ref", "String"),
           ("field_ref", "String"), ("interval", "String"),
           ("aggregation", "String")) + _VALUE
    + (("available_at", "Nullable(DateTime64(6, 'UTC'))"),
       ("null_reason", "Nullable(String)"), ("content_hash", "FixedString(64)")),
    "event_id, ordinal",
)
TABLES = (PARENT, RULE, COLUMN, FIELD)


def _hash(value: Any) -> str:
    return sha256(canonical_json(value).encode()).hexdigest()


def _seal(row: dict[str, Any]) -> dict[str, Any]:
    return {**row, "content_hash": _hash(row)}


def _clock(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("typed occurrence clock must be canonical ISO text")
    parsed = datetime.fromisoformat(value)
    if (parsed.tzinfo is None or parsed.utcoffset() != timedelta(0)
            or parsed.isoformat() != value):
        raise ValueError("typed occurrence clock must be canonical aware ISO text")
    return value


def _session(value: str) -> str:
    return datetime.fromisoformat(value).astimezone(
        ZoneInfo("America/New_York")).date().isoformat()


def _typed_value(value: Any, kind: str) -> dict[str, Any]:
    if not _scalar(value, kind):
        raise ValueError("typed occurrence evidence has unmodeled value")
    columns = dict(value_float=None, value_int=None, value_bool=None,
                   value_text=None, value_time=None)
    actual_kind = "number_int" if kind == "number" and type(value) is int else kind
    if value is not None:
        target = {"number": "value_float", "number_int": "value_int", "integer": "value_int",
                  "boolean": "value_bool", "text": "value_text",
                  "timestamp": "value_time"}[actual_kind]
        columns[target] = _clock(value) if kind == "timestamp" else value
    return dict(value_type=actual_kind, value_is_null=value is None, **columns)


def _restore_value(row: Mapping[str, Any], kind: str) -> Any:
    keys = ("value_float", "value_int", "value_bool", "value_text", "value_time")
    actual_kind = row["value_type"]
    target = {"number": "value_float", "number_int": "value_int", "integer": "value_int",
              "boolean": "value_bool", "text": "value_text",
              "timestamp": "value_time"}.get(actual_kind)
    if (target is None or actual_kind not in ({"number", "number_int"}
                                             if kind == "number" else {kind})
            or type(row["value_is_null"]) is not bool):
        raise ValueError("typed occurrence evidence discriminator differs")
    if row["value_is_null"]:
        if any(row[key] is not None for key in keys):
            raise ValueError("typed occurrence null evidence contains value")
        return None
    if row[target] is None or any(row[key] is not None for key in keys if key != target):
        raise ValueError("typed occurrence evidence value columns differ")
    value = row[target]
    if (not _scalar(value, kind)
            or actual_kind == "number_int" and type(value) is not int
            or actual_kind == "number" and type(value) is not float):
        raise ValueError("typed occurrence evidence type differs")
    return value


def project_typed_occurrence(
    occurrence: Mapping[str, Any], catalog: SignalFieldCatalog,
    stream: Mapping[str, Any], columns: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Closed exact projection; no arbitrary map or payload column is stored."""
    validate_typed_occurrence_evidence(occurrence, catalog, stream, columns)
    required = (_BASE - set(_OPTIONAL)) | {binding.column_id for binding in catalog.bindings}
    if not required <= set(occurrence) or set(occurrence) - (required | set(_OPTIONAL)):
        raise ValueError("typed occurrence has missing or unmodeled parent fields")
    event_id = occurrence["event_id"]
    if (not isinstance(event_id, str) or len(event_id) != 64
            or any(char not in "0123456789abcdef" for char in event_id)
            or occurrence["signal_id"] != event_id
            or occurrence["schema_version"] != 1
            or occurrence["signal_stream_id"] != catalog.signal_stream_id
            or not isinstance(occurrence["definition_revision"], str)
            or not occurrence["definition_revision"]
            or not isinstance(occurrence["ticker"], str)
            or not occurrence["ticker"] or occurrence["ticker"] != occurrence["ticker"].upper()):
        raise ValueError("typed occurrence identity differs")
    for key in _TEXT:
        if not isinstance(occurrence[key], str):
            raise ValueError(f"typed occurrence {key} must be text")
    for key in _UINT:
        if type(occurrence[key]) is not int or occurrence[key] < 0:
            raise ValueError(f"typed occurrence {key} must be unsigned integer")
    for key in _TIME:
        _clock(occurrence[key])
    if (datetime.fromisoformat(occurrence["available_at"])
            > datetime.fromisoformat(occurrence["event_time"])):
        raise ValueError("typed occurrence is not causal")
    expected_event_id = sha256(
        (f"{occurrence['signal_stream_id']}|{occurrence['definition_revision']}|"
         f"{occurrence['ticker']}|{occurrence['event_time']}").encode()).hexdigest()
    if event_id != expected_event_id:
        raise ValueError("typed occurrence event ID differs from producer")
    session_key = _session(occurrence["event_time"])
    if (not isinstance(occurrence["matched_rule_set_ids"], list)
            or any(not isinstance(value, str) or not value
                   for value in occurrence["matched_rule_set_ids"])):
        raise ValueError("typed occurrence rule IDs must be ordered text")
    rules = [_seal(dict(session_key=session_key, event_id=event_id,
                        ordinal=ordinal, rule_set_id=value))
             for ordinal, value in enumerate(occurrence["matched_rule_set_ids"])]
    column_rows = []
    field_rows = []
    for ordinal, binding in enumerate(catalog.bindings):
        value = occurrence["evidence"][binding.column_id]
        column_rows.append(_seal(dict(
            session_key=session_key, event_id=event_id,
            ordinal=ordinal, column_id=binding.column_id,
            **_typed_value(value, binding.value_type),
            null_reason=occurrence["evidence_null_reasons"].get(binding.column_id),
        )))
        if binding.instance_ref:
            evidence = occurrence["field_evidence"][binding.instance_ref]
            field_rows.append(_seal(dict(
                session_key=session_key, event_id=event_id, ordinal=ordinal,
                field_instance_ref=binding.instance_ref,
                field_ref=binding.field_ref, interval=binding.interval,
                aggregation=binding.aggregation,
                **_typed_value(evidence["value"], binding.value_type),
                available_at=_clock(evidence["available_at"])
                if evidence["available_at"] is not None else None,
                null_reason=evidence["null_reason"],
            )))
    for key in _OPTIONAL_TEXT:
        if key in occurrence and type(occurrence[key]) is not str:
            raise ValueError(f"typed occurrence optional {key} has wrong type")
    for key in _OPTIONAL_FLOAT:
        value = occurrence.get(key)
        if key in occurrence and not (
            type(value) is int and -(2**63) <= value < 2**63
            or type(value) is float and isfinite(value)
        ):
            raise ValueError(f"typed occurrence optional {key} has wrong type")
    parent = _seal(dict(
        session_key=session_key, event_id=event_id,
        configuration_revision=catalog.configuration_revision,
        catalog_hash=catalog.content_hash,
        signal_stream_id=occurrence["signal_stream_id"],
        definition_revision=occurrence["definition_revision"],
        ticker=occurrence["ticker"],
        **{key: occurrence[key] for key in _TEXT + _UINT + _TIME},
        **{f"{key}_present": key in occurrence for key in _OPTIONAL},
        **{f"{key}_is_int": type(occurrence.get(key)) is int for key in _OPTIONAL_FLOAT},
        **{f"{key}_float": occurrence[key] if type(occurrence.get(key)) is float else None
           for key in _OPTIONAL_FLOAT},
        **{f"{key}_int": occurrence[key] if type(occurrence.get(key)) is int else None
           for key in _OPTIONAL_FLOAT},
        **{key: occurrence.get(key) for key in _OPTIONAL_TEXT},
        rule_count=len(rules), rule_hash=_hash(rules),
        column_count=len(column_rows), column_hash=_hash(column_rows),
        field_count=len(field_rows), field_hash=_hash(field_rows),
    ))
    return dict(parent=parent, rules=rules, columns=column_rows, fields=field_rows)


def restore_typed_occurrence(
    rows: Mapping[str, Any], catalog: SignalFieldCatalog,
    stream: Mapping[str, Any], columns: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Cold readback; reject partial, duplicate, reordered, or mixed rows."""
    if not isinstance(rows, Mapping) or set(rows) != {"parent", "rules", "columns", "fields"}:
        raise ValueError("typed occurrence families incomplete")
    parent, rules, column_rows, field_rows = (
        rows["parent"], rows["rules"], rows["columns"], rows["fields"])
    if not isinstance(parent, Mapping) or any(not isinstance(group, list)
                                              for group in (rules, column_rows, field_rows)):
        raise ValueError("typed occurrence rows invalid")
    if (set(parent) != {name for name, _ in PARENT.columns}
            or parent != _seal({key: value for key, value in parent.items()
                                if key != "content_hash"})
            or parent["catalog_hash"] != catalog.content_hash
            or parent["configuration_revision"] != catalog.configuration_revision
            or parent["rule_count"] != len(rules) or parent["rule_hash"] != _hash(rules)
            or parent["column_count"] != len(column_rows)
            or parent["column_hash"] != _hash(column_rows)
            or parent["field_count"] != len(field_rows)
            or parent["field_hash"] != _hash(field_rows)):
        raise ValueError("typed occurrence parent or child fence differs")
    event_id = parent["event_id"]
    for family, group in ((RULE, rules), (COLUMN, column_rows), (FIELD, field_rows)):
        for row in group:
            if (not isinstance(row, Mapping)
                    or set(row) != {name for name, _ in family.columns}
                    or row["event_id"] != event_id
                    or row["session_key"] != parent["session_key"]
                    or row != _seal({key: value for key, value in row.items()
                                    if key != "content_hash"})):
                raise ValueError("typed occurrence child row differs")
    if (any(row["ordinal"] != ordinal for ordinal, row in enumerate(rules))
            or len(column_rows) != len(catalog.bindings)
            or any(row["ordinal"] != ordinal or row["column_id"] != binding.column_id
                   for ordinal, (row, binding) in enumerate(zip(column_rows, catalog.bindings)))):
        raise ValueError("typed occurrence child order or catalog differs")
    evidence, field_evidence, null_reasons = {}, {}, {}
    for binding, row in zip(catalog.bindings, column_rows):
        value = _restore_value(row, binding.value_type)
        evidence[binding.column_id] = value
        if row["null_reason"] is not None:
            null_reasons[binding.column_id] = row["null_reason"]
    expected_fields = [binding for binding in catalog.bindings if binding.instance_ref]
    if len(field_rows) != len(expected_fields):
        raise ValueError("typed occurrence field evidence count differs")
    for binding, row in zip(expected_fields, field_rows):
        if (row["field_instance_ref"] != binding.instance_ref
                or row["field_ref"] != binding.field_ref
                or row["interval"] != binding.interval
                or row["aggregation"] != binding.aggregation
                or row["ordinal"] != catalog.bindings.index(binding)):
            raise ValueError("typed occurrence field binding differs")
        field_evidence[binding.instance_ref] = dict(
            field_ref=binding.field_ref, interval=binding.interval,
            aggregation=binding.aggregation,
            value=_restore_value(row, binding.value_type),
            available_at=row["available_at"], null_reason=row["null_reason"])
    occurrence = {
        "event_id": event_id, "signal_id": event_id,
        "signal_stream_id": parent["signal_stream_id"],
        "definition_revision": parent["definition_revision"],
        "ticker": parent["ticker"],
        **{key: parent[key] for key in _TEXT + _UINT + _TIME},
        **{key: parent[key] for key in _OPTIONAL_TEXT if parent[f"{key}_present"]},
        **{key: (parent[f"{key}_int"] if parent[f"{key}_is_int"]
                 else parent[f"{key}_float"])
           for key in _OPTIONAL_FLOAT if parent[f"{key}_present"]},
        "matched_rule_set_ids": [row["rule_set_id"] for row in rules],
        "evidence": evidence, "field_evidence": field_evidence,
        "evidence_null_reasons": null_reasons, **evidence,
    }
    if project_typed_occurrence(occurrence, catalog, stream, columns) != rows:
        raise ValueError("typed occurrence cold readback differs")
    return occurrence
