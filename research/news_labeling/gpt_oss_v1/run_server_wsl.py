from __future__ import annotations

import argparse
import subprocess

from .config import MODEL_PROFILES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start an existing WSL vLLM GPT-OSS server."
    )
    parser.add_argument("--profile", choices=sorted(MODEL_PROFILES), default="20b")
    parser.add_argument("--distro", default="")
    parser.add_argument("--vllm-bin", default="vllm")
    parser.add_argument("--model-path")
    parser.add_argument("--served-model-name")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.88)
    parser.add_argument("--max-model-len", type=int, default=16_384)
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args(argv)
    profile = MODEL_PROFILES[args.profile]
    model_path = args.model_path or profile.model
    served_model_name = args.served_model_name or profile.model
    serve = [
        args.vllm_bin, "serve", model_path,
        "--host", "0.0.0.0",
        "--port", str(args.port),
        "--served-model-name", served_model_name,
        "--gpu-memory-utilization", str(args.gpu_memory_utilization),
        "--max-model-len", str(args.max_model_len),
        "--safetensors-load-strategy", "prefetch",
        "--enable-prefix-caching",
    ]
    command = ["wsl.exe"]
    if args.distro:
        command.extend(("-d", args.distro))
    # The executable may be a PATH-resolved command or an explicit venv path.
    # Avoid a login shell so model and server arguments remain literal.
    command.extend(("--", "env", "VLLM_USE_FLASHINFER_SAMPLER=0", *serve))
    print("COMMAND " + subprocess.list2cmdline(command), flush=True)
    if args.print_only:
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
