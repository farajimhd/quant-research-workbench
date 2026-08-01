from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from .oss_gold_benchmark import OSS_PROFILES, OssProfile


DEFAULT_DOWNLOAD_DIR = "/mnt/d/models_artifacts/opensource/huggingface"
DEFAULT_NATIVE_DOWNLOAD_DIR = "~/.cache/quant-research-workbench/vllm-models"
DEFAULT_VENV_ACTIVATE = "~/.venvs/vllm/bin/activate"
DEFAULT_LAUNCHER = Path(__file__).with_suffix(".sh")


def _cache_repo_name(model: str) -> str:
    parts = model.split("/")
    if len(parts) < 2 or any(not part for part in parts):
        raise ValueError(f"Expected a Hugging Face repository id, got {model!r}")
    return "models--" + "--".join(parts)


def _windows_to_wsl_path(path: Path) -> str:
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if len(drive) != 1 or not drive.isalpha():
        raise ValueError(f"WSL launcher must be on a Windows drive: {resolved}")
    relative = resolved.as_posix().split(":", 1)[1].lstrip("/")
    return f"/mnt/{drive}/{relative}"


def build_server_command(
    *,
    profile: OssProfile,
    distro: str = "",
    venv_activate: str = DEFAULT_VENV_ACTIVATE,
    vllm_bin: str = "vllm",
    model_path: str | None = None,
    served_model_name: str | None = None,
    download_dir: str = DEFAULT_DOWNLOAD_DIR,
    native_download_dir: str = DEFAULT_NATIVE_DOWNLOAD_DIR,
    port: int = 8000,
    gpu_memory_utilization: float = 0.88,
    max_model_len: int = 65_536,
    max_num_seqs: int | None = None,
    launcher_path: Path = DEFAULT_LAUNCHER,
) -> list[str]:
    serve = [
        vllm_bin,
        "serve",
        model_path or profile.model,
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--served-model-name",
        served_model_name or profile.model,
        "--gpu-memory-utilization",
        str(gpu_memory_utilization),
        "--max-model-len",
        str(max_model_len),
        "--enable-prefix-caching",
    ]
    resolved_max_num_seqs = (
        max_num_seqs if max_num_seqs is not None else profile.max_num_seqs
    )
    if resolved_max_num_seqs is not None:
        serve.extend(("--max-num-seqs", str(resolved_max_num_seqs)))
    serve.extend(profile.server_args)

    command = ["wsl.exe"]
    if distro:
        command.extend(("-d", distro))
    command.extend(
        (
            "--",
            "bash",
            _windows_to_wsl_path(launcher_path),
            "--venv-activate",
            venv_activate,
            "--durable-download-dir",
            download_dir,
            "--native-download-dir",
            native_download_dir,
            "--repo-name",
            _cache_repo_name(profile.model),
        )
    )
    if model_path is not None:
        command.append("--skip-stage")
    command.extend(("--", *serve))
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Stage and serve one calibrated local benchmark model through vLLM "
            "in WSL."
        )
    )
    parser.add_argument("--profile", choices=sorted(OSS_PROFILES), required=True)
    parser.add_argument("--distro", default="")
    parser.add_argument("--venv-activate", default=DEFAULT_VENV_ACTIVATE)
    parser.add_argument("--vllm-bin", default="vllm")
    parser.add_argument("--model-path")
    parser.add_argument("--served-model-name")
    parser.add_argument(
        "--download-dir",
        default=DEFAULT_DOWNLOAD_DIR,
        help="Durable mounted Hugging Face cache used as the staging source.",
    )
    parser.add_argument(
        "--native-download-dir",
        default=DEFAULT_NATIVE_DOWNLOAD_DIR,
        help="WSL-native cache used by vLLM for model loading.",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.88)
    parser.add_argument("--max-model-len", type=int, default=65_536)
    parser.add_argument("--max-num-seqs", type=int)
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args(argv)
    if not 0.1 <= args.gpu_memory_utilization <= 0.99:
        parser.error("--gpu-memory-utilization must be between 0.1 and 0.99")
    if args.max_model_len < 16_384:
        parser.error("--max-model-len must be at least 16384")
    if args.max_num_seqs is not None and not 1 <= args.max_num_seqs <= 1_024:
        parser.error("--max-num-seqs must be between 1 and 1024")
    command = build_server_command(
        profile=OSS_PROFILES[args.profile],
        distro=args.distro,
        venv_activate=args.venv_activate,
        vllm_bin=args.vllm_bin,
        model_path=args.model_path,
        served_model_name=args.served_model_name,
        download_dir=args.download_dir,
        native_download_dir=args.native_download_dir,
        port=args.port,
        gpu_memory_utilization=args.gpu_memory_utilization,
        max_model_len=args.max_model_len,
        max_num_seqs=args.max_num_seqs,
    )
    print("COMMAND " + subprocess.list2cmdline(command), flush=True)
    if args.print_only:
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
