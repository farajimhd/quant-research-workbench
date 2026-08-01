from __future__ import annotations

import argparse
import shlex
import subprocess

from .oss_gold_benchmark import OSS_PROFILES, OssProfile


DEFAULT_DOWNLOAD_DIR = "/mnt/d/models_artifacts/opensource/huggingface"
DEFAULT_VENV_ACTIVATE = "~/.venvs/vllm/bin/activate"


def _source_command(activate_path: str) -> str:
    """Return a bash-safe source command while preserving home expansion."""
    if activate_path.startswith("~/"):
        return f'source "$HOME"/{shlex.quote(activate_path[2:])}'
    return f"source {shlex.quote(activate_path)}"


def build_server_command(
    *,
    profile: OssProfile,
    distro: str = "",
    venv_activate: str = DEFAULT_VENV_ACTIVATE,
    vllm_bin: str = "vllm",
    model_path: str | None = None,
    served_model_name: str | None = None,
    download_dir: str = DEFAULT_DOWNLOAD_DIR,
    port: int = 8000,
    gpu_memory_utilization: float = 0.88,
    max_model_len: int = 65_536,
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
        "--download-dir",
        download_dir,
        "--enable-prefix-caching",
        *profile.server_args,
    ]
    command = ["wsl.exe"]
    if distro:
        command.extend(("-d", distro))
    shell_command = (
        f"{_source_command(venv_activate)} && "
        "exec env VLLM_USE_FLASHINFER_SAMPLER=0 "
        f"{shlex.join(serve)}"
    )
    command.extend(("--", "bash", "-lc", shell_command))
    return command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Download when absent and serve one calibrated local benchmark model "
            "through vLLM in WSL."
        )
    )
    parser.add_argument("--profile", choices=sorted(OSS_PROFILES), required=True)
    parser.add_argument("--distro", default="")
    parser.add_argument("--venv-activate", default=DEFAULT_VENV_ACTIVATE)
    parser.add_argument("--vllm-bin", default="vllm")
    parser.add_argument("--model-path")
    parser.add_argument("--served-model-name")
    parser.add_argument("--download-dir", default=DEFAULT_DOWNLOAD_DIR)
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.88)
    parser.add_argument("--max-model-len", type=int, default=65_536)
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args(argv)
    if not 0.1 <= args.gpu_memory_utilization <= 0.99:
        parser.error("--gpu-memory-utilization must be between 0.1 and 0.99")
    if args.max_model_len < 16_384:
        parser.error("--max-model-len must be at least 16384")
    command = build_server_command(
        profile=OSS_PROFILES[args.profile],
        distro=args.distro,
        venv_activate=args.venv_activate,
        vllm_bin=args.vllm_bin,
        model_path=args.model_path,
        served_model_name=args.served_model_name,
        download_dir=args.download_dir,
        port=args.port,
        gpu_memory_utilization=args.gpu_memory_utilization,
        max_model_len=args.max_model_len,
    )
    print("COMMAND " + subprocess.list2cmdline(command), flush=True)
    if args.print_only:
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
