"""Separate CUDA graph recording from concurrent session CUDA submissions."""
from contextlib import contextmanager
from threading import Condition


class CaptureGate:
    def __init__(self):
        self.condition = Condition()
        self.preparing = 0
        self.recording = False

    @contextmanager
    def prepare(self):
        with self.condition:
            self.condition.wait_for(lambda: not self.recording)
            self.preparing += 1
        try:
            yield
        finally:
            with self.condition:
                self.preparing -= 1
                self.condition.notify_all()

    @contextmanager
    def capture(self):
        # The trainer is the only recorder. Block new submissions first so
        # queued producer jobs cannot starve graph recording.
        with self.condition:
            self.recording = True
            self.condition.wait_for(lambda: self.preparing == 0)
        try:
            yield
        finally:
            with self.condition:
                self.recording = False
                self.condition.notify_all()


cuda_capture_gate = CaptureGate()
