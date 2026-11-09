"""Read-only lane admission guard; never controls or modifies GPU training."""

import json
from pathlib import Path
import time


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

    def refresh(self):
        """Return a cap and reason; unreadable status stays conservative."""
        if self.campaign is None or time.monotonic() - self.checked < 2:
            return self.limit
        self.checked = time.monotonic()
        try:
            controller = json.loads((self.campaign / "progress.json").read_text())
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
            self.limit, self.reason = self.reduced, "trainer status unavailable/stale"
        return self.limit
