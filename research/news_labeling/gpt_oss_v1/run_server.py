from __future__ import annotations

import argparse
import subprocess
import sys

from .config import MODEL_PROFILES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start a local GPT-OSS OpenAI-compatible vLLM server.")
    parser.add_argument("--profile", choices=sorted(MODEL_PROFILES), default="20b")
    parser.add_argument("--model")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    parser.add_argument("--max-model-len", type=int, default=16_384)
    args = parser.parse_args(argv)
    model = args.model or MODEL_PROFILES[args.profile].model
    command = [
        sys.executable, "-m", "vllm.entrypoints.cli.main", "serve", model,
        "--port", str(args.port),
        "--gpu-memory-utilization", str(args.gpu_memory_utilization),
        "--max-model-len", str(args.max_model_len),
        "--enable-prefix-caching",
    ]
    print("COMMAND " + subprocess.list2cmdline(command), flush=True)
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
