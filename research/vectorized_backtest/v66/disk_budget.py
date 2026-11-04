"""Stop at a generation boundary before result publication exhausts the volume."""
from pathlib import Path
import shutil
from .publication import write_json


def admission(free_bytes,previous_generation_bytes=0):
    # One generation can publish while its successor replays. Include both,
    # allow growth, and retain the existing 64 GiB operational disk reserve.
    estimate=max(32*2**30,int(previous_generation_bytes*1.5))
    required=64*2**30+2*estimate
    return dict(free_bytes=free_bytes,previous_generation_bytes=previous_generation_bytes,
                estimated_generation_bytes=estimate,required_bytes=required,admitted=free_bytes>=required)


def check(root,generation,days):
    root=Path(root);campaign=root.parent.parent
    previous=generation-1;size=0
    if previous>0:
        folders=[root/f'generation-{previous:04d}']
        folders.extend(campaign/'phase-2/replays'/role/f'generation-{previous:04d}' for role in ('training','validation'))
        for folder in folders:
            if not folder.is_dir():continue
            for path in folder.rglob('*'):
                if path.suffix=='.tmp':continue
                try:
                    if path.is_file():size+=path.stat().st_size
                except FileNotFoundError:
                    # Atomic publication may replace an unsealed observer file.
                    # The conservative floor still applies to the estimate.
                    continue
    report=admission(shutil.disk_usage(campaign).free,size)
    write_json(campaign/'disk-admission.json',dict(report,generation=generation,sessions=days))
    if not report['admitted']:
        raise OSError('Insufficient disk headroom for the next generation; sealed results preserved; see disk-admission.json')
