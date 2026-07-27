from __future__ import annotations

import argparse
import shlex
import subprocess


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start the workstation's existing WSL vLLM gpt-oss-20b server."
    )
    parser.add_argument("--distro", default="")
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    parser.add_argument("--max-model-len", type=int, default=16_384)
    args = parser.parse_args(argv)
    serve = [
        "vllm", "serve", args.model,
        "--host", "0.0.0.0",
        "--port", str(args.port),
        "--gpu-memory-utilization", str(args.gpu_memory_utilization),
        "--max-model-len", str(args.max_model_len),
        "--enable-prefix-caching",
    ]
    command = ["wsl.exe"]
    if args.distro:
        command.extend(("-d", args.distro))
    command.extend(("--", "bash", "-lic", shlex.join(serve)))
    print("COMMAND " + subprocess.list2cmdline(command), flush=True)
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
