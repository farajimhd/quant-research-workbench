"""Repair the known ranked-certificate version collision without data rebuild.

All old evidence is preserved. New certificates bind hard links to the exact
existing label bytes, and a new full manifest passes the stricter launch gate.
No feature/label calculation, sealed-test access or ClickHouse query occurs.
"""
import os
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
import argparse
from copy import deepcopy
import json
from pathlib import Path
from research.rl_trading.v1.common import digest, file_hash, exclusive
from research.rl_trading.v6.prepare_training import VERSION, _write_json
from research.rl_trading.v6.teacher_data import VERSION as TEACHER_VERSION
from research.rl_trading.v6.teacher_trajectory import VERSION as TRAJECTORY_VERSION
from research.rl_trading.v6.split import TRAIN, DEVELOPMENT
from research.rl_trading.v6.training_gate import require_dataset
from research.rl_trading.v6.train import _commit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    from src.runtime_paths import runtime_root
    runtime = runtime_root().resolve()
    dataset, output = args.dataset.resolve(), args.output.resolve()
    if any(not p.is_relative_to(runtime) for p in (dataset, output)) or output == dataset.parent:
        raise ValueError('Distinct runtime output is required')
    original = json.loads(dataset.read_text())
    if (original.get('version') != VERSION or original.get('status') != 'audited_ready_for_training' or
        original.get('sealed_test_accessed') is not False or
        digest({k:v for k,v in original.items() if k != 'hash'}) != original.get('hash') or
        [e['day'] for e in original['days']] != [str(d) for d in TRAIN+DEVELOPMENT]):
        raise ValueError('Repair requires the complete unchanged forward audit')
    output.mkdir(parents=True, exist_ok=True)
    with exclusive(output/'repair.lock'):
        repaired = deepcopy(original)
        for entry in repaired['days']:
            root = Path(entry['teacher_root']).resolve()
            baseline = Path(entry['original_teacher_root']).resolve()
            if any(not p.is_relative_to(runtime) for p in (root, baseline)):
                raise ValueError('Teacher input escaped runtime')
            certificate = root/'complete.json'
            cert = json.loads(certificate.read_text())
            base = json.loads((baseline/'complete.json').read_text())
            if (file_hash(certificate) != entry['teacher_sha256'] or
                file_hash(baseline/'complete.json') != entry['original_teacher_sha256'] or
                base.get('version') != TEACHER_VERSION or
                cert.get('version') != TRAJECTORY_VERSION or
                cert.get('trajectory_version') != TRAJECTORY_VERSION or
                cert.get('status') != 'audited_price_action_teacher'):
                raise ValueError('Certificate is not the known version-collision case')
            target = output/'ranked_teacher'/str(original['ranking']['top_r'])/entry['day']
            target.mkdir(parents=True, exist_ok=True)
            for name in ('decisions', 'outcomes', 'positions'):
                source = root/f'{name}.parquet'
                linked = target/source.name
                if file_hash(source) != cert[f'{name}_sha256']:
                    raise ValueError('Audited label bytes changed')
                if not linked.exists():
                    os.link(source, linked)  # Same-volume hard link, no data copy.
                if not os.path.samefile(source, linked):
                    raise ValueError('Repair output is not the original label bytes')
            fixed = {**cert, 'version': TEACHER_VERSION,
                     'certificate_repair': 'ranked_trajectory_version_namespace_collision',
                     'previous_certificate_sha256': entry['teacher_sha256']}
            if (target/'complete.json').exists():
                if json.loads((target/'complete.json').read_text()) != fixed:
                    raise ValueError('Conflicting repair certificate')
            else:
                _write_json(target/'complete.json', fixed)
            entry.update(teacher_root=str(target), teacher_sha256=file_hash(target/'complete.json'))
        repaired.update(repaired_from_dataset_sha256=file_hash(dataset), repair_source_commit=_commit())
        repaired['hash'] = digest({k:v for k,v in repaired.items() if k != 'hash'})
        temporary = output/'candidate.json'
        _write_json(temporary, repaired)
        require_dataset(temporary, runtime_root=runtime)  # Every hash and gate rechecked.
        temporary.replace(output/'complete.json')
        print(json.dumps({'status':'audited_certificate_repair_passed',
                          'days':len(repaired['days']), 'output':str(output/'complete.json')}), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
