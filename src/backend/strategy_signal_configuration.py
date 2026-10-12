"""Restore defined stream choices into the existing app resolver/catalogue.

This is pure reconstruction of normalized rows. The enclosing configuration
seal and storage placement must be verified by the configuration reader.
"""
from src.trading_runtime import strategy_signal_configuration_schema as schema
from src.trading_runtime.strategy_configuration_selection import _row, _ordinals
from .signal_stream_typed_catalog import build_signal_field_catalog
from .signal_stream_typed_publication import SourceCatalog
from .data_field_contracts import build_data_field_catalog, data_field_output_index

_COMPARATORS = {"is_true", "equals", "not_equals", "above_by_bps", "greater_or_equal",
                "greater_than", "less_or_equal", "less_than"}
_LITERALS = {"number": "number_value", "integer": "integer_value",
             "boolean": "boolean_value", "text": "text_value"}


def restore_signal_configuration(source, *, configuration_id, configuration_revision):
    if set(source) != {table.name for table in schema.TABLES}:
        raise ValueError("Signal configuration requires every defined relation")
    rows = {}
    for table in schema.TABLES:
        if type(source[table.name]) not in (list, tuple) or len(source[table.name]) > 4096:
            raise ValueError("Signal configuration exceeds its bounded row budget")
        rows[table.name] = tuple(_row(table, row) for row in source[table.name])
        if any(row["configuration_id"] != configuration_id for row in rows[table.name]):
            raise ValueError("Signal configuration relation has another owner")
    streams = sorted(rows[schema.STREAM.name], key=lambda row: row["ordinal"])
    rules = sorted(rows[schema.RULE_SET.name], key=lambda row: row["ordinal"])
    _ordinals(streams)
    _ordinals(rules)
    stream_ids = {row["signal_stream_id"] for row in streams}
    rule_ids = {row["rule_set_id"] for row in rules}
    if (len(stream_ids) != len(streams) or "" in stream_ids
            or len(rule_ids) != len(rules) or "" in rule_ids):
        raise ValueError("Signal configuration contains duplicate or empty identities")
    fields = rows[schema.FIELD.name]
    field_keys = {(row["signal_stream_id"], row["column_id"]) for row in fields}
    if (len(field_keys) != len(fields) or any(stream not in stream_ids or not column for stream, column in field_keys)):
        raise ValueError("Signal configuration field binding is duplicate or orphaned")
    if any(row["source_kind"] == "rule_set" and row["source_id"] not in rule_ids for row in fields):
        raise ValueError("Signal configuration field references an absent rule set")
    relations = []
    for table, target in ((schema.STREAM_FIELD, "column_id"), (schema.STREAM_RULE, "rule_set_id")):
        family = sorted(rows[table.name], key=lambda row: (row["signal_stream_id"], row["ordinal"]))
        _ordinals(family, "signal_stream_id")
        keys = {(row["signal_stream_id"], row[target]) for row in family}
        if (len(keys) != len(family) or any(row["signal_stream_id"] not in stream_ids for row in family)
                or target == "column_id" and keys != field_keys
                or target == "rule_set_id" and any(row[target] not in rule_ids for row in family)):
            raise ValueError("Signal configuration selection is duplicate, orphaned or incomplete")
        relations.append(family)
    conditions = sorted(rows[schema.CONDITION.name], key=lambda row: (row["rule_set_id"], row["ordinal"]))
    _ordinals(conditions, "rule_set_id")
    if any(row["rule_set_id"] not in rule_ids for row in conditions):
        raise ValueError("Signal configuration condition has no rule set")
    decoded_rules = []
    for row in rules:
        rule = {key: value for key, value in row.items() if key not in {"configuration_id", "ordinal"}}
        if rule["operator"] not in {"all", "any"}:
            raise ValueError("Signal configuration rule operator is unsupported")
        rule["enabled"] = bool(rule["enabled"])
        decoded = []
        for condition in conditions:
            if condition["rule_set_id"] != row["rule_set_id"]:
                continue
            kind = condition["value_type"]
            expected = _LITERALS.get(kind)
            populated = {name for name in _LITERALS.values() if condition[name] is not None}
            if (kind not in {*_LITERALS, "none"}
                    or populated != ({expected} if expected else set())
                    or condition["comparator"] not in _COMPARATORS
                    or not (condition["left_field_ref"] or condition["left_source_id"])
                    or condition["comparator"] != "is_true" and not (
                        condition["right_field_ref"] or condition["right_source_id"] or expected)
                    or condition["comparator"] == "above_by_bps" and (
                        kind not in {"number", "integer"}
                        or not (condition["right_field_ref"] or condition["right_source_id"]))
                    or any(condition[side + "_value_selection"] != "latest" for side in ("left", "right"))):
                raise ValueError("Signal configuration condition literal or operand is invalid")
            value = condition[expected] if expected else None
            if kind == "boolean": value = bool(value)
            item = {key: value for key, value in condition.items()
                    if key not in {"configuration_id", "rule_set_id", "ordinal", "value_type", *_LITERALS.values()}}
            item["enabled"], item["value"] = bool(item["enabled"]), value
            decoded.append(item)
        if not decoded:
            raise ValueError("Signal configuration rule set has no conditions")
        rule["conditions"] = decoded
        decoded_rules.append(rule)
    decoded_streams, catalogs, global_columns = [], {}, {}
    for row in streams:
        identity = row["signal_stream_id"]
        stream = {key: value for key, value in row.items() if key not in {"configuration_id", "ordinal"}}
        if stream["revision"] < 1 or stream["inclusion_operator"] not in {"all", "any"}:
            raise ValueError("Signal configuration stream revision or operator is invalid")
        stream["enabled"] = bool(stream["enabled"])
        stream["columns"] = [row["column_id"] for row in relations[0] if row["signal_stream_id"] == identity]
        stream["inclusion_rule_sets"] = [row["rule_set_id"] for row in relations[1] if row["signal_stream_id"] == identity]
        if not stream["columns"] or not stream["inclusion_rule_sets"]:
            raise ValueError("Signal configuration stream needs selected fields and rules")
        bound = {row["column_id"]: row for row in fields if row["signal_stream_id"] == identity}
        stream["column_intervals"] = {key: row["interval"] for key, row in bound.items()}
        stream["column_aggregations"] = {key: row["aggregation"] for key, row in bound.items()}
        columns = {}
        for key, field in bound.items():
            column = {name: value for name, value in field.items()
                      if name not in {"configuration_id", "signal_stream_id", "instance_ref", "interval", "aggregation", "available_at_contract"}}
            column["available_at"] = field["available_at_contract"]
            if key in global_columns and global_columns[key] != column:
                raise ValueError("Signal configuration global column definitions disagree")
            global_columns[key] = columns[key] = column
        catalog = build_signal_field_catalog(stream, columns, configuration_revision=configuration_revision)
        if any(binding.instance_ref != bound[binding.column_id]["instance_ref"] for binding in catalog.bindings):
            raise ValueError("Signal configuration field instance differs from the native catalogue")
        catalogs[identity] = SourceCatalog(catalog, stream, columns)
        decoded_streams.append(stream)
    data_fields = build_data_field_catalog([], [column for column in global_columns.values()
                                              if column["source_kind"] == "data_field"])
    outputs = data_field_output_index(data_fields)
    for column in global_columns.values():
        if column["source_kind"] == "data_field":
            output = outputs.get(column["field_ref"])
            if output is None or output["field_ref"] != column["field_ref"] or output["source_id"] != column["source_id"]:
                raise ValueError("Signal configuration field differs from the native Data Field output identity")
    return {"signal_streams": decoded_streams, "column_catalog": list(global_columns.values()),
            "rule_sets": decoded_rules, "data_fields": data_fields}, catalogs
