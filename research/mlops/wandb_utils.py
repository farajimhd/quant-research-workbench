from __future__ import annotations

import os
import queue
import threading
import traceback
from pathlib import Path
from typing import Any


def resolve_wandb_mode(requested: str) -> str:
    if requested != "auto":
        return requested
    env_mode = os.environ.get("WANDB_MODE", "").strip().lower()
    if env_mode in {"online", "offline", "disabled"}:
        return env_mode
    return "online" if os.environ.get("WANDB_API_KEY") else "offline"


def init_wandb(
    *,
    entity: str,
    project: str,
    run_name: str,
    config: dict[str, Any],
    run_dir: Path,
    mode: str,
    timeout_seconds: int,
) -> Any | None:
    if not project or project.lower() in {"off", "none", "disabled"}:
        print("*** WANDB project disabled; writing metrics locally only.", flush=True)
        return None
    resolved_mode = resolve_wandb_mode(mode)
    if resolved_mode == "disabled":
        print("*** WANDB explicitly disabled; writing metrics locally only.", flush=True)
        return None
    if not os.environ.get("WANDB_API_KEY") and resolved_mode == "online":
        raise RuntimeError("WANDB_API_KEY is required for --wandb-mode online.")
    os.environ["WANDB_MODE"] = resolved_mode
    os.environ.setdefault("WANDB_INIT_TIMEOUT", str(timeout_seconds))
    os.environ.setdefault("WANDB_LOGIN_TIMEOUT", str(min(timeout_seconds, 30)))
    print(
        "*** WANDB INIT | "
        f"entity={entity or '<none>'} project={project} run={run_name} "
        f"mode={resolved_mode} api_key_present={bool(os.environ.get('WANDB_API_KEY'))}",
        flush=True,
    )
    try:
        import wandb
    except ModuleNotFoundError:
        raise RuntimeError("wandb is not installed, but W&B logging is enabled.") from None
    result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

    def init_worker() -> None:
        try:
            api_key = os.environ.get("WANDB_API_KEY")
            if api_key and resolved_mode == "online":
                wandb.login(key=api_key, relogin=False)
            run = wandb.init(
                entity=entity or None,
                project=project,
                name=run_name,
                config=config,
                dir=str(run_dir),
                resume="allow",
                mode=resolved_mode,
                settings=wandb.Settings(
                    init_timeout=max(1, int(timeout_seconds)),
                    login_timeout=max(1, min(int(timeout_seconds), 30)),
                ),
            )
            result_queue.put(("ok", run))
        except Exception:
            result_queue.put(("error", traceback.format_exc()))

    thread = threading.Thread(target=init_worker, name="wandb-init", daemon=True)
    thread.start()
    thread.join(timeout=max(1, int(timeout_seconds)))
    if thread.is_alive():
        raise TimeoutError("W&B init timed out before returning.")
    if result_queue.empty():
        raise RuntimeError("W&B init thread returned no result.")
    status, payload = result_queue.get()
    if status == "ok":
        print(f"*** WANDB READY | mode={resolved_mode} dir={getattr(payload, 'dir', '<unknown>')}", flush=True)
        return payload
    raise RuntimeError(f"W&B init failed:\n{payload}")
