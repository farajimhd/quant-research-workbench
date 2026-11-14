"""Read-only training evidence for adaptive publication scheduling priority."""
import math
from pathlib import Path
import psutil


def prefer_normal(status, now, available_gib, *, live=False, was_normal=False):
    """Hysteresis: enter below1% input wait, back off at2% or uncertainty."""
    try:
        wait = float(status['metrics']['loader_wait_pct'])
        age = now - status['updated']
        return bool(live and available_gib >= 96 and -1 <= age <= 15
                    and status['state'] == 'running' and status['stage'] == 'training'
                    and math.isfinite(wait) and 0 <= wait < (2 if was_normal else 1))
    except (KeyError, TypeError, ValueError):
        return False


def live_trainer(output, status):
    """Verify a real encoder process on this exact output, never signal it."""
    for process in psutil.process_iter(['name']):
        if 'python' not in (process.info['name'] or '').lower():
            continue
        try:
            args = process.cmdline()
            if not any('run_encoder_v' in token for token in args) or '--output' not in args:
                continue
            if Path(args[args.index('--output')+1]).resolve() != Path(output).resolve():
                continue
            created = process.create_time()
            if process.is_running() and process.status() != psutil.STATUS_STOPPED and created <= status['updated']:
                return dict(pid=process.pid, created=created)
        except (psutil.Error, OSError, ValueError, IndexError, KeyError):
            continue
    return None
