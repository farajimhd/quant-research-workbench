"""Interruptible Phase 2 subprocess handoff; no database access."""
from pathlib import Path
import subprocess
import sys
import threading
from time import monotonic

import psutil
from src.market_engine.level_book_store import read

REPO = Path(__file__).resolve().parents[3]
STOP = threading.Event()


def phase2_process(command, folder, root, handoff, publish):
    started = monotonic()
    peak = 0
    log = folder / 'phase2.log'
    with log.open('w', encoding='utf-8') as stream:
        process = subprocess.Popen([sys.executable, '-B', *command], cwd=REPO,
            stdout=stream, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        observed = psutil.Process(process.pid)
        try:
            while process.poll() is None:
                if handoff.exists():
                    output = Path(read(handoff)['root'])
                    if STOP.is_set() or (root / 'STOP').exists():
                        (output / 'STOP').touch()
                    try:
                        counts = read(output / 'progress.json').get('counts', {})
                        publish('Phase 2 ' + ', '.join(f'{key} {value}' for key, value in counts.items()))
                    except (OSError, ValueError):
                        publish('Phase 2 preparing')
                try:
                    peak = max(peak, observed.memory_info().rss)
                except psutil.NoSuchProcess:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if process.poll() is None:
                if handoff.exists():
                    (Path(read(handoff)['root']) / 'STOP').touch()
                process.wait()
    return dict(exit_code=process.returncode, elapsed_seconds=monotonic()-started,
        peak_rss_bytes=peak, log=str(log))
