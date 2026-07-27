from __future__ import annotations

import argparse
import subprocess


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start the workstation's existing WSL vLLM gpt-oss-20b server."
    )
    parser.add_argument("--distro", default="")
    parser.add_argument("--vllm-bin", default="/home/g835l/venvs/vllm-gptoss/bin/vllm")
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    parser.add_argument("--max-model-len", type=int, default=16_384)
    parser.add_argument("--print-only", action="store_true")
    args = parser.parse_args(argv)
    serve = [
        args.vllm_bin, "serve", args.model,
        "--host", "0.0.0.0",
        "--port", str(args.port),
        "--gpu-memory-utilization", str(args.gpu_memory_utilization),
        "--max-model-len", str(args.max_model_len),
        "--enable-prefix-caching",
    ]
    command = ["wsl.exe"]
    if args.distro:
        command.extend(("-d", args.distro))
    # Call the venv executable directly. A WSL login shell does not expose this
    # virtual environment on PATH on the laptop.
    command.extend(("--", *serve))
    print("COMMAND " + subprocess.list2cmdline(command), flush=True)
    if args.print_only:
        return 0
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
