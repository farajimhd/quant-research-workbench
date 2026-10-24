"""Audited transport-only repair for immutable V42 deployments.

Readers request delete sharing; retries also cover Windows rename restrictions
while another reader still holds the target open.
Permission retries are bounded; malformed JSON and non-sharing errors stay fatal.
The original heartbeat loop is retried after transient permission failures, without
fabricating a heartbeat or changing the controller's existing stale timeout.
"""
import json
import os
from pathlib import Path
import sys
import time
import uuid


def retry_access(operation):
    for attempt in range(30):
        try:
            return operation()
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in (None, 5, 32) or attempt == 29:
                raise
            time.sleep(min(.02 * (attempt + 1), .2))


def read(path):
    from research.vectorized_backtest.v27.progress_io import shared_binary

    def load():
        with shared_binary(path) as stream:
            return json.load(stream)

    return retry_access(load)


def publish(root, path, value):
    root, path = Path(root).absolute(), Path(path).absolute()
    if not path.is_relative_to(root):
        raise ValueError('Transport publication escaped its owned root')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                        allow_nan=False), encoding='utf-8')
        retry_access(lambda: os.replace(temporary, path))
    finally:
        if temporary.exists():
            retry_access(temporary.unlink)


def install_transport():
    from research.vectorized_backtest.v42 import distributed_bus, distributed_storage
    distributed_bus.read = read
    distributed_storage.publish = publish


def install_progress(agent_class):
    original = agent_class.progress

    def progress(self):
        while not self.stop.is_set():
            try:
                original(self)
                return
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (None, 5, 32):
                    raise
                print(json.dumps(dict(event='heartbeat_permission_retry', node=self.node,
                                      pid=os.getpid(), epoch=time.time(), error=str(error))),
                      file=sys.stderr, flush=True)

    agent_class.progress = progress
