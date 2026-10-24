"""Audited host-only handoff of an existing distributed Phase 2 campaign.

Retains the frozen job, optimizer, screen overlay and checkpoint contracts.
The healthy workstation node continues unchanged; only controller I/O and
the active worker set change. No fresh campaign is permitted.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(16 * 1024**2), b''):
            h.update(block)
    return h.hexdigest()


def install_bus(bus_class, lanes_class):
    original_bus_init = bus_class.__init__
    original_lanes_init = lanes_class.__init__

    def local_bus(self, job, node='workstation'):
        original_bus_init(self, job, node)

    def local_mirror(self, node, path, expected):
        if sha(path) != expected:
            raise ValueError('Handoff result identity changed: ' + str(path))

    def workstation_lanes(self, config, workers, panel):
        original_lanes_init(self, config, workers, panel)
        self.job = dict(self.job, machines={'workstation': self.job['machines']['workstation']})

    bus_class.__init__ = local_bus
    bus_class.mirror = local_mirror
    lanes_class.__init__ = workstation_lanes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--job', type=Path, required=True)
    parser.add_argument('--repair', type=Path, required=True)
    parser.add_argument('--handoff', type=Path, required=True)
    args = parser.parse_args()
    job = json.loads(args.job.read_bytes())
    audit = json.loads(args.handoff.read_bytes())
    repair = json.loads((args.repair / 'repair-proof.json').read_bytes())
    assert sha(args.job) == audit['job_sha256'] == repair['job_sha256']
    root = Path(job['campaign'])
    assert sha(root / 'global/checkpoint.json') == audit['checkpoint_sha256']
    checkpoint = json.loads((root / 'global/checkpoint.json').read_bytes())
    assert checkpoint['state']['completed'] == audit['completed'] > 0
    for path, expected in audit['pinned_files'].items():
        assert sha(path) == expected, path
    for name, expected in repair['phase2_files_sha256'].items():
        assert sha(args.repair / name) == expected
    assert sha(args.repair / 'distributed_mirror.py') == repair['mirror_patch_sha256']
    code = job['machines']['workstation']['code']
    os.chdir(code)
    sys.path.insert(0, code)
    spec = importlib.util.spec_from_file_location(
        'research.vectorized_backtest.v42.phase2_activation', args.repair / 'phase2_activation.py')
    activation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(activation)
    activation.install(args.repair)
    from research.vectorized_backtest.v42 import distributed_train as controller
    from research.vectorized_backtest.v42.distributed_bus import Bus, read
    from research.vectorized_backtest.v42.distributed_lanes import DistributedLanes
    install_bus(Bus, DistributedLanes)

    def ready(job):
        import time
        heartbeat = read(Bus(job).root / 'heartbeats/workstation.json')
        assert heartbeat['pid'] == audit['worker']['pid']
        assert heartbeat['creation_time'] == audit['worker']['creation_time']
        assert heartbeat.get('status') != 'exited' and time.time() - heartbeat['epoch'] < 30

    def completed_locals(self, training, config):
        # Every final local seal was copied and hash-pinned by the handoff audit.
        assert len(training['days']) == 24
        for day in training['days']:
            path = root / 'local' / day / 'complete.json'
            assert sha(path) == audit['pinned_files'][str(path)]

    controller.start_agents = ready
    controller.Coordinator.locals = completed_locals
    log = args.job.with_name('coordinator.log').open('a', encoding='utf-8', buffering=1)
    sys.stdout = log
    sys.stderr = log
    sys.argv = [sys.argv[0], '--job', str(args.job), '--authorize-run']
    import traceback
    try:
        controller.main()
    except BaseException:
        traceback.print_exc()
        raise


if __name__ == '__main__':
    main()
