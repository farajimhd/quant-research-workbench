from __future__ import annotations

import json
import math
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from types import MethodType
from typing import Any, Callable, Mapping

import torch


def dataclass_or_mapping_to_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    return dict(vars(value))


def parameter_rows(model: torch.nn.Module) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, param in model.named_parameters():
        rows.append(
            {
                "name": name,
                "shape": list(param.shape),
                "numel": int(param.numel()),
                "trainable": bool(param.requires_grad),
                "dtype": str(param.dtype).replace("torch.", ""),
            }
        )
    return rows


def parameter_summary(model: torch.nn.Module) -> dict[str, Any]:
    rows = parameter_rows(model)
    total = sum(int(row["numel"]) for row in rows)
    trainable = sum(int(row["numel"]) for row in rows if bool(row["trainable"]))
    by_top_module: dict[str, int] = {}
    for row in rows:
        top = str(row["name"]).split(".", 1)[0]
        by_top_module[top] = by_top_module.get(top, 0) + int(row["numel"])
    return {
        "total_parameters": int(total),
        "trainable_parameters": int(trainable),
        "frozen_parameters": int(total - trainable),
        "trainable_fraction": float(trainable / total) if total else 0.0,
        "by_top_module": by_top_module,
    }


def write_model_artifacts(
    *,
    model: torch.nn.Module,
    artifact_dir: Path,
    model_config: Any,
    input_contract: Mapping[str, Any],
    output_contract: Mapping[str, Any],
    architecture_mermaid: str,
    summary_notes: str,
    dummy_input_factory: Callable[[], tuple[tuple[Any, ...], dict[str, Any]]] | None = None,
    wandb_run: Any | None = None,
) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    details = {
        "model_class": model.__class__.__name__,
        "model_config": dataclass_or_mapping_to_dict(model_config),
        "parameters": parameter_summary(model),
        "input_contract": dict(input_contract),
        "output_contract": dict(output_contract),
        "summary_notes": summary_notes,
    }
    (artifact_dir / "model_details.json").write_text(json.dumps(details, indent=2, default=str), encoding="utf-8")
    with (artifact_dir / "model_parameters.jsonl").open("w", encoding="utf-8") as handle:
        for row in parameter_rows(model):
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    (artifact_dir / "model_summary.txt").write_text(_summary_text(details), encoding="utf-8")
    (artifact_dir / "model_architecture.mmd").write_text(architecture_mermaid, encoding="utf-8")
    (artifact_dir / "model_architecture.md").write_text("```mermaid\n" + architecture_mermaid + "\n```\n", encoding="utf-8")
    _try_torchinfo(model, artifact_dir, dummy_input_factory)
    _try_torchview(model, artifact_dir, dummy_input_factory)
    if wandb_run is not None:
        for path in artifact_dir.iterdir():
            if path.is_file():
                try:
                    wandb_run.save(str(path), base_path=str(artifact_dir))
                except Exception:  # noqa: BLE001
                    pass


def write_model_card(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")


def append_checkpoint_model_card(manifest_path: Path, payload: Mapping[str, Any]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), sort_keys=True, default=str) + "\n")


def _summary_text(details: Mapping[str, Any]) -> str:
    params = details.get("parameters", {})
    lines = [
        f"Model: {details.get('model_class', '')}",
        f"Total parameters: {int(params.get('total_parameters', 0)):,}",
        f"Trainable parameters: {int(params.get('trainable_parameters', 0)):,}",
        f"Frozen parameters: {int(params.get('frozen_parameters', 0)):,}",
        "",
        "Parameters by top module:",
    ]
    for name, count in sorted(dict(params.get("by_top_module", {})).items()):
        lines.append(f"- {name}: {int(count):,}")
    lines.extend(["", "Input contract:", json.dumps(details.get("input_contract", {}), indent=2, default=str)])
    lines.extend(["", "Output contract:", json.dumps(details.get("output_contract", {}), indent=2, default=str)])
    notes = str(details.get("summary_notes") or "")
    if notes:
        lines.extend(["", notes])
    return "\n".join(lines) + "\n"


def _try_torchinfo(
    model: torch.nn.Module,
    artifact_dir: Path,
    dummy_input_factory: Callable[[], tuple[tuple[Any, ...], dict[str, Any]]] | None,
) -> None:
    summary_path = artifact_dir / "model_summary_torchinfo.txt"
    training_summary_path = artifact_dir / "model_summary_training_torchinfo.txt"
    error_path = artifact_dir / "model_summary_torchinfo_error.txt"
    if dummy_input_factory is None:
        error_path.write_text("No dummy_input_factory supplied.\n", encoding="utf-8")
        return
    try:
        from torchinfo import summary  # type: ignore

        args, kwargs = dummy_input_factory()
        args = tuple(item for value in args if (item := _sanitize_torchinfo_input(value)) is not None)
        kwargs = dict(_sanitize_torchinfo_input(kwargs))
        input_data = args if args else (kwargs,)
        original_forward = model.forward

        def _summary_forward(self: torch.nn.Module, *forward_args: Any, **forward_kwargs: Any) -> torch.Tensor:
            patched_args = tuple(_inject_summary_metadata(self, value) for value in forward_args)
            output = original_forward(*patched_args, **forward_kwargs)
            tensor = _first_tensor(output)
            if tensor is not None:
                return tensor
            return torch.zeros((), device=_first_model_device(self))

        try:
            model.forward = MethodType(_summary_forward, model)  # type: ignore[method-assign]
            text = str(summary(model, input_data=input_data, verbose=0, depth=4))
        finally:
            model.forward = original_forward  # type: ignore[method-assign]
        summary_path.write_text(text + "\n", encoding="utf-8")
        training_summary_path.write_text(text + "\n", encoding="utf-8")
        try:
            error_path.unlink()
        except FileNotFoundError:
            pass
    except Exception as exc:  # noqa: BLE001
        error_path.write_text(repr(exc) + "\n", encoding="utf-8")


def _try_torchview(
    model: torch.nn.Module,
    artifact_dir: Path,
    dummy_input_factory: Callable[[], tuple[tuple[Any, ...], dict[str, Any]]] | None,
) -> None:
    error_path = artifact_dir / "model_architecture_torchview_error.txt"
    if dummy_input_factory is None:
        error_path.write_text("No dummy_input_factory supplied.\n", encoding="utf-8")
        return
    try:
        from torchview import draw_graph  # type: ignore

        args, kwargs = dummy_input_factory()
        input_data = args if args else (kwargs,)
        graph = draw_graph(model, input_data=input_data, expand_nested=True)
        graph.visual_graph.render(str(artifact_dir / "model_architecture_torchview"), format="png", cleanup=True)
        graph.visual_graph.render(str(artifact_dir / "model_architecture_torchview"), format="svg", cleanup=True)
    except Exception as exc:  # noqa: BLE001
        error_path.write_text(repr(exc) + "\n", encoding="utf-8")


def finite_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    return out if math.isfinite(out) else float(default)


def _first_tensor(value: Any) -> torch.Tensor | None:
    if torch.is_tensor(value):
        return value
    if is_dataclass(value):
        for field in fields(value):
            field_value = getattr(value, field.name)
            tensor = _first_tensor(field_value)
            if tensor is not None:
                return tensor
        return None
    if isinstance(value, Mapping):
        for field_value in value.values():
            tensor = _first_tensor(field_value)
            if tensor is not None:
                return tensor
        return None
    if isinstance(value, (list, tuple)):
        for field_value in value:
            tensor = _first_tensor(field_value)
            if tensor is not None:
                return tensor
    return None


def _inject_summary_metadata(model: torch.nn.Module, value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    out = dict(value)
    if "raw_event_stream" in out and "event_feature_names" not in out:
        config = getattr(model, "config", None)
        names = getattr(config, "event_feature_names", None)
        if names is not None:
            out["event_feature_names"] = tuple(str(name) for name in names)
    return out


def _first_model_device(model: torch.nn.Module) -> torch.device:
    try:
        return next(model.parameters()).device
    except StopIteration:
        return torch.device("cpu")


def _sanitize_torchinfo_input(value: Any) -> Any:
    if torch.is_tensor(value):
        return value
    if isinstance(value, Mapping):
        return {
            key: item
            for key, raw_item in value.items()
            if (item := _sanitize_torchinfo_input(raw_item)) is not None
        }
    if isinstance(value, tuple):
        items = tuple(item for raw_item in value if (item := _sanitize_torchinfo_input(raw_item)) is not None)
        return items if items else None
    if isinstance(value, list):
        items = [item for raw_item in value if (item := _sanitize_torchinfo_input(raw_item)) is not None]
        return items if items else None
    return None
