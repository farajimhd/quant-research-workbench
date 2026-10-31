"""Atomic display/checkpoint mirroring with bounded Windows sharing retries."""
import hashlib
import os
import time
import uuid
from pathlib import Path


def retry_access(operation):
    for attempt in range(20):
        try:
            return operation()
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in (None, 5, 32) or attempt == 19:
                raise
            time.sleep(min(.02 * (attempt + 1), .2))


def copy_atomic(source, target, expected=None):
    source, target = Path(source), Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + '.' + uuid.uuid4().hex + '.mirror.tmp')
    try:
        digest = hashlib.sha256()
        # Open once: atomic source publication cannot splice file versions.
        with retry_access(lambda: source.open('rb')) as src, temporary.open('xb') as dst:
            for block in iter(lambda: src.read(16 * 1024**2), b''):
                dst.write(block)
                if expected is not None:
                    digest.update(block)
        if expected is not None and digest.hexdigest() != expected:
            raise ValueError('Authoritative mirror hash changed: ' + str(source))
        retry_access(lambda: os.replace(temporary, target))
    finally:
        if temporary.exists():
            retry_access(temporary.unlink)


def snapshot(self, node, source, target):
    source, target = Path(source), Path(target)
    if node == 'workstation':
        value = str(source).replace('\\', '/')
        if not value.startswith('D:/TradingML/runtimes/'):
            raise ValueError('Only runtime artifacts may cross machines')
        source = Path(self.job['workstation_share']) / value[3:]
    if source == target or not source.exists():
        return
    stat = source.stat()
    stamp = (stat.st_size, stat.st_mtime_ns)
    if self.stamps.get(str(source)) == stamp:
        return
    copy_atomic(source, target)
    # Failed copies never suppress a later retry or claim a newer checkpoint.
    self.stamps[str(source)] = stamp
