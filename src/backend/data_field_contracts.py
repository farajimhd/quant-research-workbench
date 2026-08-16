from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Iterable

from src.backend.application_registry import DISCOVERY_RUNTIME_FIELDS, FIELD_DEFINITIONS


DATA_FIELD_CONTRACT_VERSION = 1


def field_output_ref(data_field_id: str, revision: int, output_id: str) -> str:
    return f"{data_field_id}@{max(1, int(revision))}:{output_id}"


def atomic_field_catalog(extra_inputs: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Return every source-owned application field plus declared producer inputs.

    Atomic means source-authoritative at the application boundary. A gateway may
    normalize a wire value or publish canonical market-clock state internally;
    users still consume it as a non-configurable Atomic Field.
    """

    rows: dict[str, dict[str, Any]] = {}
    for field in FIELD_DEFINITIONS:
        if field.provenance in {"derived", "model"}:
            continue
        rows[field.field_id] = {
            "atomic_field_id": field.field_id,
            "schema_version": field.schema_version,
            "name": field.presentation_label or field.label,
            "description": field.source_summary or f"Source-owned {field.group} observation.",
            "group": field.group,
            "owner": field.owner,
            "source_path": field.source_path,
            "query_plan_id": field.query_plan_id,
            "value_type": field.value_type,
            "unit": field.unit,
            "entity_grain": field.entity_grain,
            "event_at": field.event_at,
            "available_at": field.available_at,
            "update_cadence": field.publication_cadence,
            "historical_support": field.historical_support,
            "modes": list(field.modes),
            "freshness_policy": field.freshness_policy,
            "null_reasons": list(field.null_reasons),
            "status": field.status,
            "configurable": False,
            "provenance": field.provenance,
        }
    for raw in extra_inputs:
        atomic_field_id = str(raw).strip()
        if (
            not atomic_field_id
            or atomic_field_id in rows
            or re.fullmatch(r"[A-Za-z][A-Za-z0-9_.:-]*", atomic_field_id) is None
        ):
            continue
        rows[atomic_field_id] = {
            "atomic_field_id": atomic_field_id,
            "schema_version": 1,
            "name": _readable(atomic_field_id),
            "description": "Producer-declared atomic input. Exact source semantics remain owned by its registered producer.",
            "group": "producer_input",
            "owner": "qmd_gateway",
            "source_path": "qmd://definition-catalog",
            "query_plan_id": "qmd.runtime-capability-catalog",
            "value_type": "producer_defined",
            "unit": "producer_defined",
            "entity_grain": "producer_defined",
            "event_at": "producer event clock",
            "available_at": "QMD acceptance clock",
            "update_cadence": "producer cadence",
            "historical_support": "producer_declared",
            "modes": ["live", "paper", "replay", "backtest", "backtest_debug"],
            "freshness_policy": "producer declared",
            "null_reasons": ["source_unavailable", "not_observed", "outside_coverage"],
            "status": "implemented",
            "configurable": False,
            "provenance": "source",
        }
    return [rows[key] for key in sorted(rows)]


def build_data_field_catalog(
    calculation_rows: list[dict[str, Any]],
    field_catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build user-facing configurable Data Fields and exact output identities."""

    fields_by_source = {
        str(row.get("source_id") or row.get("field_id") or ""): dict(row)
        for row in field_catalog
        if str(row.get("source_id") or row.get("field_id") or "")
    }
    covered: set[str] = set()
    result: list[dict[str, Any]] = []
    for calculation in calculation_rows:
        capability_id = str(calculation.get("capability_id") or "").strip()
        if not capability_id:
            continue
        sources = [str(value) for value in calculation.get("fields") or [] if str(value)]
        if not sources:
            sources = [capability_id]
        revision = int(calculation.get("implementation_version") or 1)
        covered.update(sources)
        timeframes = [
            str(value)
            for value in calculation.get("selected_timeframes") or calculation.get("timeframes") or []
            if str(value)
        ]
        contexts: list[str | None] = list(dict.fromkeys(timeframes)) or [None]
        preferred_context = next(
            (value for value in ("1s", "session", "event", "1d", "settlement", "filing") if value in contexts),
            contexts[0],
        )
        contexts = [preferred_context, *(value for value in contexts if value != preferred_context)]
        for context_index, timeframe in enumerate(contexts):
            data_field_id = f"data.{capability_id}" + (f".{_slug(timeframe)}" if timeframe else "")
            context_timeframes = [timeframe] if timeframe else []
            outputs = [
                _data_field_output(
                    data_field_id,
                    revision,
                    source_id,
                    fields_by_source.get(source_id, {}),
                    timeframe=timeframe,
                    qualified_presentation=len(contexts) > 1 and context_index > 0,
                )
                for source_id in dict.fromkeys(sources)
            ]
            result.append({
            "data_field_id": data_field_id,
            "revision": revision,
            "name": f"{str(calculation.get('name') or _readable(capability_id))}{f' · {timeframe}' if timeframe else ''}",
            "description": str(calculation.get("calculation") or calculation.get("description") or "Registered calculation."),
            "category": str(calculation.get("category") or calculation.get("capability_type") or "Data Field"),
            "recipe_id": str(calculation.get("capability_key") or capability_id),
            "recipe_version": revision,
            "owner": str(calculation.get("owner") or calculation.get("provider") or "qmd_gateway"),
            "inputs": [str(value) for value in calculation.get("inputs") or [] if str(value)],
            "context": {
                "timeframes": context_timeframes,
                "update_cadence": str(calculation.get("cadence") or "producer cadence"),
                "execution_scope": str(calculation.get("execution_scope") or calculation.get("tier") or "focused"),
                "allowed_scopes": [str(value) for value in calculation.get("allowed_scopes") or [] if str(value)],
            },
            "parameters": {"timeframe": timeframe} if timeframe else {},
            "policies": {
                "warm_up_bars": calculation.get("warm_up_bars"),
                "missing": "unavailable",
                "gaps": "preserve",
                "late_events": "producer_watermark",
            },
            "outputs": outputs,
            "enabled": bool(calculation.get("enabled", True)),
            "configurable": bool(calculation.get("configurable")),
            "system_required": bool(calculation.get("system_required")),
            "implementation_status": str(calculation.get("implementation_status") or calculation.get("availability") or "unknown"),
            "live_support": str(calculation.get("implementation_status") or calculation.get("availability") or "unknown") not in {"offline_only", "planned"},
            "historical_support": True,
            "cost_class": str(calculation.get("cost_class") or "unknown"),
            "stateful": bool(calculation.get("stateful")),
            "contract_version": DATA_FIELD_CONTRACT_VERSION,
            })

    # Every registered field remains usable even when it is a direct source
    # projection rather than an output of a multi-output QMD capability.
    for source_id, field in sorted(fields_by_source.items()):
        if source_id in covered:
            continue
        data_field_id = f"data.projection.{source_id}"
        result.append({
            "data_field_id": data_field_id,
            "revision": 1,
            "name": str(field.get("name") or _readable(source_id)),
            "description": str(field.get("description") or f"Direct projection of {source_id}."),
            "category": str(field.get("semantic_type") or "Projection"),
            "recipe_id": "registered_projection",
            "recipe_version": 1,
            "owner": str(field.get("source") or "application_registry"),
            "inputs": [str(field.get("field_id") or source_id)],
            "context": {
                "timeframes": [str(value) for value in field.get("timeframes") or [] if str(value)],
                "update_cadence": "source cadence",
                "execution_scope": "consumer_selected",
                "allowed_scopes": ["core_scan", "watchlist", "strategy_run", "request", "offline"],
            },
            "parameters": {},
            "policies": {"missing": "unavailable", "gaps": "preserve", "late_events": "source_policy"},
            "outputs": [_data_field_output(data_field_id, 1, source_id, field)],
            "enabled": str(field.get("implementation_status") or "implemented") in {"implemented", "live_only"},
            "configurable": False,
            "system_required": False,
            "implementation_status": str(field.get("implementation_status") or "implemented"),
            "live_support": str(field.get("implementation_status") or "implemented") != "offline_only",
            "historical_support": str(field.get("implementation_status") or "implemented") != "live_only",
            "cost_class": "projection",
            "stateful": False,
            "contract_version": DATA_FIELD_CONTRACT_VERSION,
        })
    validate_data_field_catalog(result)
    return sorted(result, key=lambda row: str(row["data_field_id"]))


def data_field_output_index(data_fields: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for data_field in data_fields:
        for output in data_field.get("outputs") or []:
            field_ref = str(output.get("field_ref") or "")
            source_id = str(output.get("source_id") or "")
            if field_ref:
                index[field_ref] = output
            if source_id:
                index.setdefault(source_id, output)
                timeframe = str(output.get("context_timeframe") or "")
                if timeframe:
                    index[f"{source_id}@@{timeframe}"] = output
    return index


def project_data_field_outputs(
    rows: Iterable[dict[str, Any]], data_fields: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Attach exact Data Field output identities to producer rows.

    This is a projection only; multi-row calculations remain owned by QMD Live
    or the historical vectorized executor.
    """

    outputs = [
        dict(output)
        for data_field in data_fields
        for output in data_field.get("outputs") or []
        if str(output.get("field_ref") or "")
    ]
    projected: list[dict[str, Any]] = []
    for row in rows:
        result = dict(row)
        for output in outputs:
            field_ref = str(output["field_ref"])
            runtime_field = str(output.get("runtime_field") or output.get("source_id") or "")
            source_id = str(output.get("source_id") or "")
            value_found = runtime_field in row or source_id in row
            value = row.get(runtime_field) if runtime_field in row else row.get(source_id)
            context_timeframe = str(output.get("context_timeframe") or "")
            observed_timeframe = str(row.get("indicator_timeframe") or row.get("working_timeframe") or "")
            if context_timeframe and observed_timeframe and context_timeframe != observed_timeframe:
                value_found = False
                value = None
            result[field_ref] = value
            # Canvas tables address configured presentations by column id while
            # rules address the immutable Data Field output reference. Publish
            # both names from the same resolved value so presentation never has
            # to reconstruct a producer-specific runtime key.
            for presentation in output.get("column_presentations") or []:
                presentation_id = str(presentation.get("presentation_id") or "")
                if presentation_id:
                    result[presentation_id] = value
            if not value_found:
                result[f"{field_ref}__null_reason"] = "producer_output_missing"
            for suffix in ("_available_at", "_published_at", "_source_date", "_null_reason"):
                key = f"{runtime_field}{suffix}"
                if key in row:
                    result[f"{field_ref}__{suffix.removeprefix('_')}"] = row.get(key)
        projected.append(result)
    return projected


def migrate_rule_set_field_refs(
    rule_sets: list[dict[str, Any]], data_fields: list[dict[str, Any]]
) -> None:
    index = data_field_output_index(data_fields)
    for rule_set in rule_sets:
        rule_set.pop("atomic", None)
        rule_set.setdefault("origin", "system")
        rule_set.setdefault("protected", str(rule_set.get("origin")) == "system")
        rule_set.setdefault("editable", str(rule_set.get("origin")) != "system")
        for condition in rule_set.get("conditions") or []:
            for side in ("left", "right"):
                legacy_id = str(condition.get(f"{side}_source_id") or "")
                existing_ref = str(condition.get(f"{side}_field_ref") or "")
                legacy_timeframe = str(condition.get(f"{side}_timeframe") or "")
                existing_output = index.get(existing_ref)
                if existing_output is not None and legacy_id and str(existing_output.get("source_id") or "") != legacy_id:
                    existing_output = None
                output = existing_output or index.get(f"{legacy_id}@@{legacy_timeframe}") or index.get(legacy_id)
                if output is not None:
                    condition[f"{side}_field_ref"] = str(output["field_ref"])
                    condition[f"{side}_source_id"] = str(output.get("source_id") or legacy_id)
                elif legacy_id:
                    condition[f"{side}_field_ref"] = legacy_id
                condition.pop(f"{side}_timeframe", None)


def build_column_catalog(
    data_fields: list[dict[str, Any]], rule_sets: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    columns: list[dict[str, Any]] = []
    seen: set[str] = set()
    for data_field in data_fields:
        for output in data_field.get("outputs") or []:
            for presentation in output.get("column_presentations") or []:
                column_id = str(presentation.get("presentation_id") or "")
                if not column_id or column_id in seen:
                    continue
                seen.add(column_id)
                columns.append({
                    "column_id": column_id,
                    "field_ref": str(output.get("field_ref") or ""),
                    "field_id": str(output.get("field_id") or ""),
                    "source_kind": "data_field",
                    "source_id": str(output.get("source_id") or ""),
                    "name": str(presentation.get("label") or output.get("name") or column_id),
                    "description": str(output.get("description") or data_field.get("description") or "Data Field output."),
                    "value_type": str(output.get("value_type") or "number"),
                    "unit": str(output.get("unit") or "scalar"),
                    "default_visible": bool(presentation.get("default_visible")),
                    "filterable": bool(output.get("filterable")),
                    "filter_operators": list(output.get("filter_operators") or []),
                    "sortable": bool(output.get("sortable")),
                    "source": str(data_field.get("owner") or "data_field_registry"),
                    "source_path": f"data-field://{data_field.get('data_field_id')}@{data_field.get('revision')}",
                    "query_plan_id": "data_field.compiled_plan.v1",
                    "provenance": "computed",
                    "available_at": "field output availability clock",
                    "implementation_status": str(data_field.get("implementation_status") or "unknown"),
                    "registry_authority": "data_field_registry",
                    "semantic_type": str(data_field.get("category") or "data_field"),
                    "timeframes": list(dict(data_field.get("context") or {}).get("timeframes") or []),
                    "presentation": deepcopy(presentation),
                })
    for rule_set in rule_sets:
        rule_set_id = str(rule_set.get("rule_set_id") or "")
        if not rule_set_id:
            continue
        columns.append({
            "column_id": f"rule_set:{rule_set_id}",
            "field_ref": "",
            "field_id": "",
            "source_kind": "rule_set",
            "source_id": rule_set_id,
            "name": str(rule_set.get("name") or rule_set_id),
            "description": str(rule_set.get("description") or "Boolean Rule Set result."),
            "value_type": "boolean",
            "unit": "boolean",
            "default_visible": False,
            "filterable": True,
            "filter_operators": ["equals", "is_true"],
            "sortable": True,
            "source": "rule_set_registry",
            "source_path": f"rule-set://{rule_set_id}",
            "query_plan_id": "data_field.compiled_plan.v1",
            "provenance": "derived",
            "available_at": "candidate evaluation clock",
            "implementation_status": "implemented",
            "registry_authority": "rule_set_registry",
            "semantic_type": "rule_set",
            "timeframes": ["evaluation"],
        })
    return columns


def compile_data_field_plan(
    discovery: dict[str, Any], *, composition_ids: Iterable[str] = ()
) -> dict[str, Any]:
    """Compile exact field demand from rules, ranking, columns and evidence."""

    data_fields = list(discovery.get("data_fields") or [])
    output_index = data_field_output_index(data_fields)
    columns = {
        str(row.get("column_id") or ""): row
        for row in discovery.get("column_catalog") or []
    }
    rules = {
        str(row.get("rule_set_id") or ""): row for row in discovery.get("rule_sets") or []
    }
    compositions = [dict(discovery.get("core_scan") or {})]
    compositions.extend(dict(row) for row in discovery.get("watchlists") or [])
    compositions.extend(dict(row) for row in discovery.get("signal_streams") or [])
    requested_ids = {str(value) for value in composition_ids if str(value)}
    if requested_ids:
        compositions = [
            row
            for row in compositions
            if str(row.get("scan_id") or row.get("watchlist_id") or row.get("signal_stream_id") or "") in requested_ids
        ]
    field_refs: set[str] = set()
    rule_ids: set[str] = set()
    for composition in compositions:
        for key in ("inclusion_rule_sets", "exclusion_rule_sets"):
            rule_ids.update(str(value) for value in composition.get(key) or [] if str(value))
        ranking = str(composition.get("ranking_field_ref") or composition.get("ranking_field") or "")
        if ranking:
            output = output_index.get(ranking)
            field_refs.add(str((output or {}).get("field_ref") or ranking))
        for column_id in composition.get("columns") or []:
            column = columns.get(str(column_id), {})
            if str(column.get("field_ref") or ""):
                field_refs.add(str(column["field_ref"]))
    for rule_id in rule_ids:
        for condition in rules.get(rule_id, {}).get("conditions") or []:
            if not bool(condition.get("enabled", True)):
                continue
            for side in ("left", "right"):
                field_ref = str(condition.get(f"{side}_field_ref") or condition.get(f"{side}_source_id") or "")
                if field_ref:
                    output = output_index.get(field_ref)
                    field_refs.add(str((output or {}).get("field_ref") or field_ref))
    selected_fields = []
    atomic_inputs: set[str] = set()
    timeframes: set[str] = set()
    technical_timeframes: set[str] = set()
    for data_field in data_fields:
        outputs = {str(row.get("field_ref") or "") for row in data_field.get("outputs") or []}
        if not outputs.intersection(field_refs):
            continue
        selected_fields.append({
            "data_field_id": str(data_field.get("data_field_id") or ""),
            "revision": int(data_field.get("revision") or 1),
            "recipe_id": str(data_field.get("recipe_id") or ""),
            "output_refs": sorted(outputs.intersection(field_refs)),
            "execution_scope": str(dict(data_field.get("context") or {}).get("execution_scope") or "focused"),
            "stateful": bool(data_field.get("stateful")),
        })
        atomic_inputs.update(str(value) for value in data_field.get("inputs") or [] if str(value))
        timeframes.update(str(value) for value in dict(data_field.get("context") or {}).get("timeframes") or [] if str(value))
        matched_outputs = [
            output
            for output in data_field.get("outputs") or []
            if str(output.get("field_ref") or "") in field_refs
        ]
        if any(
            str(output.get("source_id") or "").startswith("indicator.")
            or str(output.get("source_id") or "") == "market.relative_volume"
            for output in matched_outputs
        ):
            technical_timeframes.update(
                str(value)
                for value in dict(data_field.get("context") or {}).get("timeframes") or []
                if str(value)
            )
    payload = {
        "schema_version": 1,
        "authority": "data_field_compiler",
        "field_refs": sorted(field_refs),
        "data_fields": sorted(selected_fields, key=lambda row: row["data_field_id"]),
        "atomic_inputs": sorted(atomic_inputs),
        "rule_set_ids": sorted(rule_ids),
        "timeframes": sorted(timeframes),
        "technical_timeframes": sorted(technical_timeframes),
    }
    payload["content_hash"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def validate_data_field_catalog(data_fields: list[dict[str, Any]]) -> None:
    ids: set[str] = set()
    refs: set[str] = set()
    for row in data_fields:
        data_field_id = str(row.get("data_field_id") or "")
        if not data_field_id or data_field_id in ids:
            raise ValueError(f"Invalid or duplicate Data Field id: {data_field_id or '<empty>'}")
        ids.add(data_field_id)
        outputs = list(row.get("outputs") or [])
        if not outputs:
            raise ValueError(f"Data Field {data_field_id} has no outputs")
        for output in outputs:
            field_ref = str(output.get("field_ref") or "")
            if not field_ref or field_ref in refs:
                raise ValueError(f"Invalid or duplicate Data Field output: {field_ref or '<empty>'}")
            refs.add(field_ref)
            if not list(output.get("column_presentations") or []):
                raise ValueError(f"Data Field output {field_ref} has no column presentation")


def _data_field_output(
    data_field_id: str,
    revision: int,
    source_id: str,
    field: dict[str, Any],
    *,
    timeframe: str | None = None,
    qualified_presentation: bool = False,
) -> dict[str, Any]:
    output_id = source_id
    value_type = str(field.get("value_type") or "number")
    unit = str(field.get("unit") or "scalar")
    base_column_id = str(field.get("column_id") or _generated_column_id(source_id))
    column_id = f"{base_column_id}__{_slug(timeframe)}" if timeframe and qualified_presentation else base_column_id
    field_ref = field_output_ref(data_field_id, revision, output_id)
    numeric = value_type in {
        "number", "integer", "float", "score", "ratio", "percent", "price", "bps_per_second"
    } or unit in {
        "scalar", "score", "ratio", "multiple", "percent", "basis_points", "bps_per_second", "currency", "shares"
    }
    filterable = bool(field.get("filterable")) or value_type in {
        "boolean", "number", "integer", "float", "score", "ratio", "percent", "price", "bps_per_second", "string", "date", "time", "timestamp"
    }
    filter_operators = list(field.get("filter_operators") or [])
    if filterable and not filter_operators:
        if value_type == "boolean":
            filter_operators = ["is_true", "equals"]
        elif numeric:
            filter_operators = [
                "greater_than", "greater_or_equal", "less_than", "less_or_equal", "equals", "not_equals"
            ]
        else:
            filter_operators = ["equals", "not_equals"]
    return {
        "output_id": output_id,
        "field_ref": field_ref,
        "source_id": source_id,
        "field_id": str(field.get("field_id") or source_id),
        "runtime_field": DISCOVERY_RUNTIME_FIELDS.get(source_id, column_id or source_id),
        "context_timeframe": timeframe or "",
        "name": str(field.get("name") or _readable(source_id)),
        "description": str(field.get("description") or f"Output {source_id}."),
        "value_type": value_type,
        "unit": unit,
        "entity_grain": str(field.get("entity_grain") or "security_at_market_clock"),
        "filterable": filterable,
        "filter_operators": filter_operators,
        "sortable": bool(field.get("sortable")),
        "column_presentations": [{
            "presentation_id": column_id,
            "label": str(field.get("name") or _readable(source_id)),
            "format": _column_format(value_type, unit),
            "precision": 2 if unit in {"currency", "percent", "multiple", "basis_points"} else None,
            "alignment": "right" if numeric else "left",
            "default_visible": bool(field.get("default_visible")) and not qualified_presentation,
            "null_display": "—",
        }],
        "chart_presentations": ([{
            "presentation_id": f"chart.{_slug(source_id)}",
            "label": str(field.get("name") or _readable(source_id)),
            "render_type": "line",
            "placement": "price_overlay" if unit == "currency" else "separate_pane",
            "axis_unit": unit,
            "theme_role": "primary",
            "default_visible": False,
        }] if numeric else []),
    }


def _generated_column_id(source_id: str) -> str:
    return f"field__{_slug(source_id)}"


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "__", value).strip("_").lower()


def _readable(value: str) -> str:
    return re.sub(r"[_\-.]+", " ", value).strip().title()


def _column_format(value_type: str, unit: str) -> str:
    if unit == "currency":
        return "currency"
    if unit in {"percent", "basis_points"}:
        return "percent"
    if value_type in {"number", "integer", "float"}:
        return "number"
    if unit in {"timestamp", "date", "time"}:
        return unit
    if value_type == "boolean":
        return "boolean"
    return "text"
