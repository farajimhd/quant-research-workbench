#!/usr/bin/env bash
set -euo pipefail

venv_activate=""
durable_cache=""
native_cache=""
repo_name=""
skip_stage=false

while (($#)); do
  case "$1" in
    --venv-activate)
      venv_activate=${2:?missing --venv-activate value}
      shift 2
      ;;
    --durable-download-dir)
      durable_cache=${2:?missing --durable-download-dir value}
      shift 2
      ;;
    --native-download-dir)
      native_cache=${2:?missing --native-download-dir value}
      shift 2
      ;;
    --repo-name)
      repo_name=${2:?missing --repo-name value}
      shift 2
      ;;
    --skip-stage)
      skip_stage=true
      shift
      ;;
    --)
      shift
      break
      ;;
    *)
      echo "ERROR: unknown launcher argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$venv_activate" || -z "$durable_cache" || -z "$native_cache" || -z "$repo_name" || $# -eq 0 ]]; then
  echo "ERROR: incomplete vLLM launcher contract." >&2
  exit 2
fi

expand_home() {
  case "$1" in
    "~/"*) printf '%s/%s' "$HOME" "${1#\~/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

venv_activate=$(expand_home "$venv_activate")
durable_cache=$(expand_home "$durable_cache")
native_cache=$(expand_home "$native_cache")

source "$venv_activate"
mkdir -p "$native_cache"
cache_fs=$(stat -f -c %T "$native_cache")
case "$cache_fs" in
  9p|drvfs)
    echo "ERROR: native vLLM cache is on $cache_fs; choose a WSL-native ext4 path with --native-download-dir." >&2
    exit 3
    ;;
esac

source_repo="$durable_cache/$repo_name"
native_repo="$native_cache/$repo_name"

if [[ "$skip_stage" == false && -f "$source_repo/refs/main" ]]; then
  source_revision=$(tr -d '\r\n' < "$source_repo/refs/main")
  [[ "$source_revision" =~ ^[0-9a-f]{40,64}$ ]] || {
    echo "ERROR: invalid durable model revision." >&2
    exit 4
  }
  source_blob_count=$(find "$source_repo/blobs" -maxdepth 1 -type f | wc -l | tr -d ' ')
  source_blob_bytes=$(find "$source_repo/blobs" -maxdepth 1 -type f -printf '%s\n' | awk '{total += $1} END {printf "%.0f", total}')
  expected="$source_revision|$source_blob_count|$source_blob_bytes"
  [[ -d "$source_repo/snapshots/$source_revision" && "$source_blob_count" -gt 0 ]] || {
    echo "ERROR: durable model snapshot is incomplete." >&2
    exit 5
  }

  marker="$native_repo/.qwrb-stage-complete"
  actual=$(cat "$marker" 2>/dev/null || true)
  if [[ "$actual" != "$expected" || ! -d "$native_repo/snapshots/$source_revision" ]]; then
    command -v rsync >/dev/null 2>&1 || {
      echo "ERROR: rsync is required for resumable native model staging. Install it in WSL with: sudo apt-get install rsync" >&2
      exit 6
    }
    broken_source=$(find -L "$source_repo/snapshots/$source_revision" -xtype l -print -quit)
    [[ -z "$broken_source" ]] || {
      echo "ERROR: durable model snapshot contains incomplete blob links." >&2
      exit 7
    }
    mkdir -p "$native_repo"
    remaining_bytes=$(LC_ALL=C rsync -a --dry-run --stats "$source_repo/" "$native_repo/" | awk -F: '/Total transferred file size/ {value=$2; gsub(/[^0-9]/, "", value); print value}')
    [[ "$remaining_bytes" =~ ^[0-9]+$ ]] || {
      echo "ERROR: could not calculate remaining native staging bytes." >&2
      exit 8
    }
    available_bytes=$(df -PB1 "$native_cache" | awk 'NR == 2 {print $4}')
    ((available_bytes >= remaining_bytes + 1073741824)) || {
      echo "ERROR: insufficient WSL-native disk space for model staging; need the remaining model bytes plus 1 GiB." >&2
      exit 9
    }

    echo "STAGING $repo_name revision $source_revision from $durable_cache to WSL-native cache $native_cache"
    rsync -a --partial --delete --info=progress2 "$source_repo/" "$native_repo/"
    native_blob_count=$(find "$native_repo/blobs" -maxdepth 1 -type f | wc -l | tr -d ' ')
    native_blob_bytes=$(find "$native_repo/blobs" -maxdepth 1 -type f -printf '%s\n' | awk '{total += $1} END {printf "%.0f", total}')
    [[ "$native_blob_count" == "$source_blob_count" && "$native_blob_bytes" == "$source_blob_bytes" && -d "$native_repo/snapshots/$source_revision" ]] || {
      echo "ERROR: native model staging audit failed." >&2
      exit 10
    }
    printf '%s' "$expected" > "$marker"
  else
    echo "USING validated WSL-native model cache $native_repo"
  fi
elif [[ "$skip_stage" == false ]]; then
  echo "Durable model cache is absent; vLLM will download once into WSL-native cache $native_cache"
fi

exec env VLLM_USE_FLASHINFER_SAMPLER=0 "$@" --download-dir "$native_cache"
