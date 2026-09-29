"""Immutable experiment identities and transactional, restartable results."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any


def encoded(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def file_hash(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def output_directory(path: Path) -> Path:
    root = Path(r"D:\TradingML\runtimes").resolve()
    if not root.is_dir():
        raise RuntimeError(f"required runtime root unavailable: {root}")
    path = path.resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError(f"output must be beneath {root}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(encoded(value) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def bind_manifest(path: Path, manifest: dict) -> str:
    identity = digest(manifest)
    record = {"identity": identity, "manifest": manifest}
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != record:
            raise RuntimeError("experiment identity changed; use a new output directory")
    else:
        atomic_json(path, record)
    return identity


def gpu_preflight() -> dict:
    """Fail closed on occupied or unobservable GPUs, without creating a CUDA context."""
    def query(arguments: list[str]) -> str:
        return subprocess.run(
            ["nvidia-smi", *arguments], check=True, capture_output=True,
            text=True, timeout=15,
        ).stdout.strip()

    apps = query(["--query-compute-apps=pid,process_name", "--format=csv,noheader"])
    # WDDM lists desktop graphics processes too; those are not training jobs.
    benign = {"dwm.exe", "explorer.exe", "shellexperiencehost.exe", "shellhost.exe",
              "startmenuexperiencehost.exe", "searchhost.exe", "textinputhost.exe",
              "lockapp.exe", "applicationframehost.exe", "crossdeviceresume.exe",
              "msedgewebview2.exe", "windowsterminal.exe", "taskmgr.exe", "notepad.exe",
              "systemsettings.exe", "logioptionsplus_agent.exe"}
    occupied = []
    for line in apps.splitlines():
        pid, _, name = line.partition(",")
        if pid.strip() == str(os.getpid()):
            continue
        if os.name == "nt" and pid.strip() == "4":
            continue  # Windows System; WDDM can redact its image, not a user training process.
        if os.name == "nt" and name.strip().lower() == str(
            Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "LogonUI.exe"
        ).lower():
            continue  # Windows lock-screen renderer; require the system path, not just its name.
        if Path(name.strip()).name.lower() not in benign:
            occupied.append(line)
    state = query(["--query-gpu=index,name,utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"])
    if occupied:
        raise RuntimeError("GPU occupied or ownership unknown; deferred: " + "; ".join(occupied))
    for line in state.splitlines():
        fields = [part.strip() for part in line.split(",")]
        if len(fields) != 5 or float(fields[2]) > 10 or float(fields[3]) > 4096:
            raise RuntimeError("GPU not idle or telemetry incomplete; deferred: " + line)
    return {"gpus": state, "processes": apps}


class Results:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS predictions (
              day TEXT, ticker TEXT, origin INTEGER, model TEXT, payload TEXT,
              PRIMARY KEY(day,ticker,origin,model));
            CREATE TABLE IF NOT EXISTS attempts (
              id INTEGER PRIMARY KEY, day TEXT, origin INTEGER, status TEXT,
              elapsed REAL, lag REAL, requested INTEGER, returned INTEGER, detail TEXT);
        """)

    def existing(self, day: str, origin: int, model: str) -> set[str]:
        return {row[0] for row in self.db.execute(
            "SELECT ticker FROM predictions WHERE day=? AND origin=? AND model=?", (day, origin, model))}

    def record(self, day: str, origin: int, model: str, tickers: list[str],
               rows: list[dict], checkpoint_hash: str, elapsed: float, lag: float) -> None:
        actual = [(row["ticker"], row["model_id"], row["event_at_us"], row["checkpoint_hash"]) for row in rows]
        expected = {(ticker, model, origin, checkpoint_hash) for ticker in tickers}
        if len(actual) != len(expected) or set(actual) != expected:
            raise RuntimeError("prediction reconciliation failed: missing, duplicate, stale, or wrong release")
        with self.db:
            for row in rows:
                self.db.execute("INSERT INTO predictions VALUES (?,?,?,?,?)",
                                (day, row["ticker"], origin, model, encoded(row)))
            self.db.execute("INSERT INTO attempts VALUES (NULL,?,?,?,?,?,?,?,?)",
                            (day, origin, "completed", elapsed, lag, len(tickers), len(rows), ""))

    def failure(self, day: str, origin: int, count: int, error: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO attempts VALUES(NULL,?,?,?,?,?,?,?,?)",
                            (day, origin, "failed", 0., 0., count, 0, error))

    def close(self) -> None:
        self.db.close()
