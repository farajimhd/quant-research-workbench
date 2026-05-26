from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any


def save_model_architecture_artifacts(
    *,
    model: Any,
    data_config: Any,
    output_dir: Path,
    version: str,
    torch_module: Any,
    wandb_run: Any = None,
    summary_batch_size: int = 1,
    summary_depth: int = 8,
    graph_depth: int = 3,
) -> dict[str, Any]:
    """Save a torchinfo-style summary and torchview graph for a training run."""

    architecture_dir = output_dir / "model_architecture"
    architecture_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = architecture_dir / "model_architecture.json"
    summary_path = architecture_dir / "model_summary.txt"
    graph_png_path = architecture_dir / "model_graph.png"
    graph_svg_path = architecture_dir / "model_graph.svg"
    graph_pdf_path = architecture_dir / "model_graph.pdf"
    graph_dot_path = architecture_dir / "model_graph.dot"

    artifact_info: dict[str, Any] = {
        "version": version,
        "architecture_dir": str(architecture_dir),
        "summary_path": str(summary_path),
        "graph_png_path": None,
        "graph_svg_path": None,
        "graph_pdf_path": None,
        "graph_dot_path": None,
        "graph_png_dpi": None,
        "input_shapes": [],
        "errors": [],
    }

    try:
        summary_inputs = _summary_inputs(
            model=model,
            data_config=data_config,
            torch_module=torch_module,
            rows=summary_batch_size,
        )
        artifact_info["input_shapes"] = [_shape_of(tensor) for tensor in summary_inputs]
        _write_summary(
            model=model,
            inputs=summary_inputs,
            path=summary_path,
            torch_module=torch_module,
            depth=summary_depth,
        )
        _write_graph(
            model=model,
            inputs=summary_inputs,
            version=version,
            png_path=graph_png_path,
            svg_path=graph_svg_path,
            pdf_path=graph_pdf_path,
            dot_path=graph_dot_path,
            torch_module=torch_module,
            depth=graph_depth,
            artifact_info=artifact_info,
        )
    except Exception as exc:
        message = f"model architecture artifact generation failed: {exc}"
        artifact_info["errors"].append(message)
        summary_path.write_text(message + "\n", encoding="utf-8")

    metadata_path.write_text(json.dumps(artifact_info, indent=2, sort_keys=True), encoding="utf-8")
    _log_architecture_to_wandb(
        wandb_run=wandb_run,
        summary_path=summary_path,
        graph_png_path=graph_png_path if artifact_info.get("graph_png_path") else None,
        graph_svg_path=graph_svg_path if artifact_info.get("graph_svg_path") else None,
        graph_pdf_path=graph_pdf_path if artifact_info.get("graph_pdf_path") else None,
        metadata_path=metadata_path,
    )
    return artifact_info


def _model_device(model: Any, torch_module: Any) -> Any:
    try:
        return next(model.parameters()).device
    except StopIteration:
        return torch_module.device("cpu")


def _shape_of(value: Any) -> Any:
    try:
        import torch

        if torch.is_tensor(value):
            return tuple(value.shape)
    except ModuleNotFoundError:
        pass
    if isinstance(value, (list, tuple)):
        return [_shape_of(item) for item in value]
    if isinstance(value, dict):
        return {key: _shape_of(item) for key, item in value.items()}
    return type(value).__name__


def _branch_time_feature_count(branch: Any, data_config: Any) -> int:
    projection = getattr(branch, "time_projection", None)
    if projection is not None:
        for module in projection.modules():
            if module.__class__.__name__ == "Linear":
                return int(module.in_features)
    return len(getattr(data_config, "time_feature_columns", ()))


def _synthetic_input_for(
    *,
    name: str,
    model: Any,
    data_config: Any,
    torch_module: Any,
    rows: int,
    device: Any,
) -> Any:
    dtype = torch_module.float32
    if name == "values":
        if hasattr(model, "feature_count"):
            return torch_module.zeros(rows, model.context_length, model.feature_count, dtype=dtype, device=device)
        return torch_module.zeros(
            rows,
            model.one_min_encoder.context_length,
            model.one_min_encoder.feature_count,
            dtype=dtype,
            device=device,
        )
    if name == "time_features":
        if hasattr(model, "time_feature_count"):
            return torch_module.zeros(
                rows,
                model.context_length,
                model.time_feature_count,
                dtype=dtype,
                device=device,
            )
        if hasattr(model, "time_projection"):
            return torch_module.zeros(
                rows,
                model.context_length,
                len(getattr(data_config, "time_feature_columns", ())),
                dtype=dtype,
                device=device,
            )
        return torch_module.zeros(
            rows,
            model.one_min_encoder.context_length,
            _branch_time_feature_count(model.one_min_encoder, data_config),
            dtype=dtype,
            device=device,
        )

    branch_by_name = {
        "macro_15m_values": "macro_15m_encoder",
        "macro_15m_time_features": "macro_15m_encoder",
        "macro_1h_values": "macro_1h_encoder",
        "macro_1h_time_features": "macro_1h_encoder",
        "macro_1d_values": "macro_1d_encoder",
        "macro_1d_time_features": "macro_1d_encoder",
        "five_min_values": "five_min_encoder",
        "five_min_time_features": "five_min_encoder",
        "thirty_min_values": "thirty_min_encoder",
        "thirty_min_time_features": "thirty_min_encoder",
        "anchor_values": "anchor_encoder",
        "anchor_time_features": "anchor_encoder",
    }
    if name in branch_by_name:
        branch = getattr(model, branch_by_name[name])
        feature_count = _branch_time_feature_count(branch, data_config) if name.endswith("time_features") else branch.feature_count
        return torch_module.zeros(rows, branch.context_length, feature_count, dtype=dtype, device=device)

    raise ValueError(f"Do not know how to build summary input for forward argument {name!r}.")


def _summary_inputs(*, model: Any, data_config: Any, torch_module: Any, rows: int) -> tuple[Any, ...]:
    device = _model_device(model, torch_module)
    forward_names = [
        name
        for name, parameter in inspect.signature(model.forward).parameters.items()
        if name != "self" and parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    ]
    return tuple(
        _synthetic_input_for(
            name=name,
            model=model,
            data_config=data_config,
            torch_module=torch_module,
            rows=rows,
            device=device,
        )
        for name in forward_names
    )


def _write_summary(*, model: Any, inputs: tuple[Any, ...], path: Path, torch_module: Any, depth: int) -> None:
    lines = [f"Forward input shapes: {[_shape_of(tensor) for tensor in inputs]}", ""]
    try:
        from torchinfo import summary

        result = summary(
            model,
            input_data=inputs,
            depth=depth,
            col_names=("input_size", "output_size", "num_params", "trainable"),
            row_settings=("var_names",),
            verbose=0,
            device=str(_model_device(model, torch_module)),
        )
        lines.append(str(result))
    except ModuleNotFoundError:
        lines.append("torchinfo is not installed; using the local hook-based summary fallback.")
        lines.extend(_fallback_summary(model, inputs, torch_module))
    except Exception as exc:
        lines.append(f"torchinfo summary failed: {exc}")
        lines.append("Using the local hook-based summary fallback.")
        lines.extend(_fallback_summary(model, inputs, torch_module))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fallback_summary(model: Any, inputs: tuple[Any, ...], torch_module: Any) -> list[str]:
    rows: list[dict[str, Any]] = []
    hooks = []
    module_names = {module: name for name, module in model.named_modules()}

    def register_hook(module: Any) -> None:
        if module is model or list(module.children()):
            return

        def hook(mod: Any, mod_inputs: Any, mod_outputs: Any) -> None:
            params = sum(parameter.numel() for parameter in mod.parameters(recurse=False))
            trainable = sum(parameter.numel() for parameter in mod.parameters(recurse=False) if parameter.requires_grad)
            rows.append(
                {
                    "layer": module_names.get(mod, ""),
                    "type": mod.__class__.__name__,
                    "input_shape": _shape_of(mod_inputs),
                    "output_shape": _shape_of(mod_outputs),
                    "params": params,
                    "trainable": trainable,
                }
            )

        hooks.append(module.register_forward_hook(hook))

    for module in model.modules():
        register_hook(module)

    was_training = model.training
    model.eval()
    with torch_module.inference_mode():
        outputs = model(*inputs)
    for hook_handle in hooks:
        hook_handle.remove()
    model.train(was_training)

    total_params = sum(parameter.numel() for parameter in model.parameters())
    trainable_params = sum(parameter.numel() for parameter in model.parameters() if parameter.requires_grad)
    lines = [
        f"Output shape: {_shape_of(outputs)}",
        f"Total params: {total_params:,}",
        f"Trainable params: {trainable_params:,}",
        "",
        "layer\ttype\tinput_shape\toutput_shape\tparams\ttrainable",
    ]
    for row in rows:
        lines.append(
            f"{row['layer']}\t{row['type']}\t{row['input_shape']}\t{row['output_shape']}\t"
            f"{row['params']}\t{row['trainable']}"
        )
    return lines


def _candidate_dot_paths() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.environ.get("GRAPHVIZ_DOT")
    if explicit:
        candidates.append(Path(explicit))

    roots = [Path(sys.prefix)]
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        roots.append(Path(conda_prefix))
    for root in roots:
        candidates.append(root / "Library" / "bin" / "dot.exe")
        candidates.append(root / "bin" / "dot.exe")

    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if not base:
            continue
        base_path = Path(base)
        candidates.append(base_path / "Graphviz" / "bin" / "dot.exe")
        candidates.extend(base_path.glob("Graphviz*\\bin\\dot.exe"))
    return candidates


def _ensure_graphviz_dot_on_path() -> tuple[bool, str | None]:
    current = shutil.which("dot")
    if current:
        return True, current
    for candidate in _candidate_dot_paths():
        if candidate and candidate.exists():
            os.environ["PATH"] = str(candidate.parent) + os.pathsep + os.environ.get("PATH", "")
            os.environ["GRAPHVIZ_DOT"] = str(candidate)
            return True, str(candidate)
    return False, None


def _write_graph(
    *,
    model: Any,
    inputs: tuple[Any, ...],
    version: str,
    png_path: Path,
    svg_path: Path,
    pdf_path: Path,
    dot_path: Path,
    torch_module: Any,
    depth: int,
    artifact_info: dict[str, Any],
) -> None:
    try:
        from torchview import draw_graph
    except ModuleNotFoundError:
        artifact_info["errors"].append("torchview is not installed; graph image was skipped.")
        return

    dot_ready, dot_path_resolved = _ensure_graphviz_dot_on_path()
    if not dot_ready:
        artifact_info["errors"].append("Graphviz dot executable was not found; graph image was skipped.")
        return

    try:
        graph = draw_graph(
            model,
            input_data=inputs,
            graph_name=f"{version}_model",
            depth=depth,
            expand_nested=False,
            save_graph=False,
            device=str(_model_device(model, torch_module)),
        )
        graph.visual_graph.attr(dpi="220")
        dot_path.write_text(graph.visual_graph.source, encoding="utf-8")
        graph.visual_graph.render(filename=str(png_path.with_suffix("")), format="png", cleanup=True)
        graph.visual_graph.render(filename=str(svg_path.with_suffix("")), format="svg", cleanup=True)
        graph.visual_graph.render(filename=str(pdf_path.with_suffix("")), format="pdf", cleanup=True)
        artifact_info["graph_png_path"] = str(png_path)
        artifact_info["graph_svg_path"] = str(svg_path)
        artifact_info["graph_pdf_path"] = str(pdf_path)
        artifact_info["graph_dot_path"] = str(dot_path)
        artifact_info["graph_png_dpi"] = 220
        artifact_info["graphviz_dot"] = dot_path_resolved
    except Exception as exc:
        artifact_info["errors"].append(f"torchview graph failed: {exc}")


def _log_architecture_to_wandb(
    *,
    wandb_run: Any,
    summary_path: Path,
    graph_png_path: Path | None,
    graph_svg_path: Path | None,
    graph_pdf_path: Path | None,
    metadata_path: Path,
) -> None:
    if wandb_run is None:
        return
    try:
        import wandb

        payload: dict[str, Any] = {
            "model_architecture/summary_text": wandb.Html(
                "<pre>" + _html_escape(summary_path.read_text(encoding="utf-8")) + "</pre>"
            ),
            "model_architecture/metadata": wandb.Html(
                "<pre>" + _html_escape(metadata_path.read_text(encoding="utf-8")) + "</pre>"
            ),
            "train_step": 0,
        }
        if graph_png_path is not None and graph_png_path.exists():
            payload["model_architecture/graph"] = wandb.Image(str(graph_png_path))
        wandb_run.log(payload)
        wandb.save(str(summary_path), base_path=str(summary_path.parent))
        wandb.save(str(metadata_path), base_path=str(metadata_path.parent))
        if graph_png_path is not None and graph_png_path.exists():
            wandb.save(str(graph_png_path), base_path=str(graph_png_path.parent))
        if graph_svg_path is not None and graph_svg_path.exists():
            wandb.save(str(graph_svg_path), base_path=str(graph_svg_path.parent))
        if graph_pdf_path is not None and graph_pdf_path.exists():
            wandb.save(str(graph_pdf_path), base_path=str(graph_pdf_path.parent))
    except Exception as exc:
        print(f"*** W&B model architecture logging skipped: {exc}", flush=True)


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
