"""Read-only lane admission guard; never controls or modifies GPU training."""

import json
from pathlib import Path
import time
import os

import psutil


class TrainingGuard:
    """Refresh small authoritative status files at most once every two seconds.

    Validation/startup/stale status gets a conservative lane cap. The full
    configured pool becomes eligible during training or when no trainer runs.
    Already-admitted units drain normally, preserving durable range boundaries.
    """

    def __init__(self, campaign, full, reduced):
        self.campaign = Path(campaign) if campaign else None
        self.full, self.reduced = full, min(full, reduced)
        self.limit = self.reduced if self.campaign else full
        self.reason = "awaiting trainer status" if self.campaign else "not configured"
        self.checked = float("-inf")

    def activity_limit(self):
        cache = os.environ.get('BARGPT_ADMISSION_CACHE')
        if cache:
            return self.shared_activity_limit(Path(cache))
        return self.scan_activity()

    def shared_activity_limit(self, path):
        """One nonblocking native observer; workers reuse its bounded-age decision."""
        import msvcrt
        from ..shards.contract import write_json
        binding = [str(self.campaign.resolve()), self.full, self.reduced]
        def cached(age):
            try:
                value = json.loads(path.read_text())
                if value['binding'] == binding and 0 <= time.time()-value['updated'] < age:
                    return value['limit'], value['reason']
            except (OSError, ValueError, KeyError, TypeError):
                pass
        value = cached(3)
        if value is not None:
            return value
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.with_suffix('.lock').open('a+b') as lock:
            if lock.tell() == 0:
                lock.write(b'0'); lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                return cached(10) or (self.reduced, 'awaiting activity observer')
            try:
                value = cached(3)
                if value is None:
                    value = self.scan_activity()
                    write_json(path, dict(binding=binding, updated=time.time(), limit=value[0], reason=value[1]))
                return value
            finally:
                lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)

    def scan_activity(self):
        """Read-only fallback: discover replacement trainers, not just an old PID.

        A live trainer with unknown stage remains protected. Absence is established
        by a complete process scan; inaccessible Python commands fail closed.
        No process is signalled and no training files are written.
        """
        live = []
        protected = False
        try:
            for process in psutil.process_iter(['pid', 'name']):
                if 'python' not in (process.info['name'] or '').lower():
                    continue
                try:
                    args = process.cmdline()
                    command = ' '.join(args).lower()
                    if any(token in command for token in
                           ('run_encoder', 'run_train', 'campaign_v3', 'torch.distributed.run')):
                        live.append((process.pid, process.create_time()))
                        try:
                            output = Path(args[args.index('--output') + 1]).resolve()
                            if not output.is_relative_to(self.campaign.parent.resolve()):
                                raise ValueError('Trainer outside configured runtime')
                            status = json.loads((output / 'progress.json').read_text())
                            normal = (status['state'] == 'running' and status['stage'] == 'training'
                                      and 0 <= time.time() - status['updated'] <= 30
                                      and status['updated'] >= process.create_time())
                            protected |= not normal
                        except (OSError, ValueError, KeyError, IndexError, TypeError):
                            protected = True
                except psutil.NoSuchProcess:
                    continue
            free = psutil.virtual_memory().available / 1024**3
            # Windows processor groups can overstate aggregate utilization on
            # hosts with >64 logical CPUs. Average the explicit per-CPU values.
            loads = psutil.cpu_percent(interval=None, percpu=True)
            cpu = sum(loads) / len(loads)
        except (psutil.Error, OSError):
            return self.reduced, 'trainer activity unavailable'
        if live and protected:
            return self.reduced, f'{len(live)} live trainer(s); stage unknown'
        if free < 80 or cpu >= 85:
            return self.reduced, f'resource pressure CPU {cpu:.0f}% RAM {free:.0f} GiB'
        return self.full, f'{len(live)} live normal trainer(s); CPU {cpu:.0f}% RAM {free:.0f} GiB'

    def refresh(self):
        """Return a cap and reason; unreadable status stays conservative."""
        if self.campaign is None or time.monotonic() - self.checked < 2:
            return self.limit
        self.checked = time.monotonic()
        try:
            controller = json.loads((self.campaign / "progress.json").read_text())
            # Encoder-v2 is a direct run, without the old overnight controller.
            # Terminal/unknown direct status stays conservative: it has no child
            # identity ledger proving that no replacement trainer is live.
            if "child" not in controller and "stage" in controller:
                state, stage = controller["state"], controller["stage"]
                if time.time() - controller["updated"] > 30 or state != 'running':
                    self.limit, self.reason = self.activity_limit()
                    return self.limit
                self.limit = self.full if state == "running" and stage == "training" else self.reduced
                self.reason = f"direct trainer {stage} ({state})"
                return self.limit
            if controller.get("state") == "complete":
                self.limit, self.reason = self.full, "training campaign complete"
                return self.limit
            if controller.get("state") in {"early_stopped", "stopped", "failed"}:
                import psutil

                child = controller.get("child", {})
                pid, created = child.get("pid"), child.get("created")
                if pid and created:
                    try:
                        inactive = (
                            not psutil.pid_exists(pid)
                            or abs(psutil.Process(pid).create_time() - created) > 1
                        )
                    except psutil.NoSuchProcess:
                        inactive = True
                    except psutil.Error:
                        inactive = False
                    if inactive:
                        # A terminal trainer intentionally stops refreshing its
                        # status. Verify process identity before treating that
                        # age as inactivity rather than an unknown live stall.
                        self.limit, self.reason = (
                            self.full,
                            "verified inactive trainer (" + controller["state"] + ")",
                        )
                        return self.limit
            output = Path(controller["child"]["output"])
            if not output.resolve().is_relative_to(self.campaign.resolve()):
                raise ValueError("Trainer output is outside its campaign")
            status = json.loads((output / "progress.json").read_text())
            if time.time() - status["updated"] > 30:
                raise ValueError("Trainer progress is stale")
            stage, state = status["stage"], status["state"]
            active = state == "running"
            self.limit = (
                self.full if stage == "training" or not active else self.reduced
            )
            self.reason = f"trainer {stage} ({state})"
        except (OSError, ValueError, KeyError, TypeError):
            self.limit, self.reason = self.activity_limit()
        return self.limit
