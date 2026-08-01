from __future__ import annotations

import argparse
import shlex
import subprocess

from .oss_gold_benchmark import OSS_PROFILES, OssProfile


DEFAULT_DOWNLOAD_DIR = "/mnt/d/models_artifacts/opensource/huggingface"
DEFAULT_NATIVE_DOWNLOAD_DIR = "~/.cache/quant-research-workbench/vllm-models"
DEFAULT_VENV_ACTIVATE = "~/.venvs/vllm/bin/activate"


def _source_command(activate_path: str) -> str:
    """Return a bash-safe source command while preserving home expansion."""
    if activate_path.startswith("~/"):
        return f'source "$HOME"/{shlex.quote(activate_path[2:])}'
    return f"source {shlex.quote(activate_path)}"


def _bash_path_expression(path: str) -> str:
    if path.startswith("~/"):
        return f'"$HOME"/{shlex.quote(path[2:])}'
    return shlex.quote(path)


def _cache_repo_name(model: str) -> str:
    parts = model.split("/")
    if len(parts) < 2 or any(not part for part in parts):
        raise ValueError(f"Expected a Hugging Face repository id, got {model!r}")
    return "models--" + "--".join(parts)


def _native_stage_script(
    *, profile: OssProfile, durable_download_dir: str, native_download_dir: str
) -> str:
    repo_name = _cache_repo_name(profile.model)
    return "\n".join(
        (
            f"durable_cache={_bash_path_expression(durable_download_dir)}",
            f"native_cache={_bash_path_expression(native_download_dir)}",
            'mkdir -p "$native_cache"',
            'cache_fs=$(stat -f -c %T "$native_cache")',
            'case "$cache_fs" in 9p|drvfs) echo "ERROR: native vLLM cache is '
            'on $cache_fs; choose a WSL-native ext4 path with '
            '--native-download-dir." >&2; exit 2;; esac',
            f"repo_name={shlex.quote(repo_name)}",
            'source_repo="$durable_cache/$repo_name"',
            'native_repo="$native_cache/$repo_name"',
            'if [[ -f "$source_repo/refs/main" ]]; then',
            '  source_revision=$(tr -d "\\r\\n" < "$source_repo/refs/main")',
            '  [[ "$source_revision" =~ ^[0-9a-f]{40,64}$ ]] || '
            '{ echo "ERROR: invalid durable model revision." >&2; exit 3; }',
            '  source_blob_count=$(find "$source_repo/blobs" -maxdepth 1 '
            '-type f | wc -l | tr -d " ")',
            '  source_blob_bytes=$(find "$source_repo/blobs" -maxdepth 1 '
            '-type f -printf "%s\\n" | awk \'{total += $1} END '
            "{printf \"%.0f\", total}\')",
            '  expected="$source_revision|$source_blob_count|$source_blob_bytes"',
            '  [[ -d "$source_repo/snapshots/$source_revision" && '
            '"$source_blob_count" -gt 0 ]] || { echo "ERROR: durable model '
            'snapshot is incomplete." >&2; exit 4; }',
            '  marker="$native_repo/.qwrb-stage-complete"',
            '  actual=$(cat "$marker" 2>/dev/null || true)',
            '  if [[ "$actual" != "$expected" || ! -d '
            '"$native_repo/snapshots/$source_revision" ]]; then',
            '    command -v rsync >/dev/null 2>&1 || { echo "ERROR: rsync is '
            'required for resumable native model staging. Install it in WSL '
            'with: sudo apt-get install rsync" >&2; exit 5; }',
            '    broken_source=$(find -L '
            '"$source_repo/snapshots/$source_revision" -xtype l -print -quit)',
            '    [[ -z "$broken_source" ]] || { echo "ERROR: durable model '
            'snapshot contains incomplete blob links." >&2; exit 6; }',
            '    mkdir -p "$native_repo"',
            '    remaining_bytes=$(LC_ALL=C rsync -a --dry-run --stats '
            '"$source_repo/" "$native_repo/" | awk -F: '
            "'/Total transferred file size/ {value=$2; gsub(/[^0-9]/, \"\", "
            "value); print value}')",
            '    [[ "$remaining_bytes" =~ ^[0-9]+$ ]] || { echo "ERROR: '
            'could not calculate remaining native staging bytes." >&2; exit 7; }',
            '    available_bytes=$(df -PB1 "$native_cache" | awk '
            "'NR == 2 {print $4}')",
            '    (( available_bytes >= remaining_bytes + 1073741824 )) || '
            '{ echo "ERROR: insufficient WSL-native disk space for model '
            'staging; need the remaining model bytes plus 1 GiB." >&2; exit 8; }',
            '    echo "STAGING $repo_name revision $source_revision from '
            '$durable_cache to WSL-native cache $native_cache"',
            '    rsync -a --partial --delete --info=progress2 '
            '"$source_repo/" "$native_repo/"',
            '    native_blob_count=$(find "$native_repo/blobs" -maxdepth 1 '
            '-type f | wc -l | tr -d " ")',
            '    native_blob_bytes=$(find "$native_repo/blobs" -maxdepth 1 '
            '-type f -printf "%s\\n" | awk \'{total += $1} END '
            "{printf \"%.0f\", total}\')",
            '    [[ "$native_blob_count" == "$source_blob_count" && '
            '"$native_blob_bytes" == "$source_blob_bytes" && -d '
            '"$native_repo/snapshots/$source_revision" ]] || { echo '
            '"ERROR: native model staging audit failed." >&2; exit 9; }',
            '    printf "%s" "$expected" > "$marker"',
            '  else',
            '    echo "USING validated WSL-native model cache $native_repo"',
            '  fi',
            'else',
            '  echo "Durable model cache is absent; vLLM will download once '
            'into WSL-native cache $native_cache"',
            'fi',
        )
    )


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
        "__NATIVE_CACHE__",
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
    serve_command = " ".join(
        '"$native_cache"' if token == "__NATIVE_CACHE__" else shlex.quote(token)
        for token in serve
    )
    shell_command = "\n".join(
        (
            "set -euo pipefail",
            _source_command(venv_activate),
            (
                _native_stage_script(
                    profile=profile,
                    durable_download_dir=download_dir,
                    native_download_dir=native_download_dir,
                )
                if model_path is None
                else "\n".join(
                    (
                        f"native_cache={_bash_path_expression(native_download_dir)}",
                        'mkdir -p "$native_cache"',
                    )
                )
            ),
            "exec env VLLM_USE_FLASHINFER_SAMPLER=0 " + serve_command,
        )
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
