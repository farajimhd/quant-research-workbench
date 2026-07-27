from __future__ import annotations

import argparse
import subprocess
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the local gpt-oss-20b OpenAI-compatible vLLM server.")
    parser.add_argument("--model", default="openai/gpt-oss-20b")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--gpu-memory-utilization", type=float, default=0.85)
    parser.add_argument("--max-model-len", type=int, default=16_384)
    args = parser.parse_args(argv)
    command = [
        sys.executable, "-m", "vllm.entrypoints.cli.main", "serve", args.model,
        "--port", str(args.port),
        "--gpu-memory-utilization", str(args.gpu_memory_utilization),
        "--max-model-len", str(args.max_model_len),
        "--enable-prefix-caching",
    ]
    print("COMMAND " + subprocess.list2cmdline(command), flush=True)
    return subprocess.call(command)


if __name__ == "__main__":
    raise SystemExit(main())
