"""Ingestion-owned atomic G archive to D SSD copy for new source acquisition."""
from __future__ import annotations

from hashlib import sha256
import os
import shutil
from pathlib import Path
from uuid import uuid4


def space_budget(cache_root,*,reserve_bytes=256*1024**3,reserve_fraction=.10):
    """Read actual D-volume free space; reserve is the larger explicit bound."""
    if type(reserve_bytes) is not int or reserve_bytes<0 or not 0<=reserve_fraction<1:
        raise ValueError('Invalid SSD staging reserve')
    root=Path(cache_root)
    while not root.exists():
        if root.parent==root:raise ValueError('Required SSD staging volume is unavailable')
        root=root.parent
    usage=shutil.disk_usage(root)
    reserve=max(reserve_bytes,int(usage.total*reserve_fraction))
    return dict(total_bytes=usage.total,free_bytes=usage.free,reserve_bytes=reserve,
        usable_bytes=max(0,usage.free-reserve))


def plan_chunks(items,space,*,maximum_chunk_bytes=256*1024**3):
    """Pack ordered whole source units; never truncate a unit to fit the SSD."""
    if type(maximum_chunk_bytes) is not int or maximum_chunk_bytes<0:
        raise ValueError('Invalid staging chunk bound')
    limit=min(space['usable_bytes'],maximum_chunk_bytes) if maximum_chunk_bytes else space['usable_bytes']
    batches=[];current=[];used=0
    for key,size in items:
        if type(size) is not int or size<=0:raise ValueError('Staging unit requires a positive exact byte count')
        if size>limit:raise ValueError(f'{key}: source unit exceeds SSD headroom after reserve')
        if current and used+size>limit:
            batches.append(tuple(current));current=[];used=0
        current.append((key,size));used+=size
    if current:batches.append(tuple(current))
    return dict(chunks=tuple(batches),chunk_limit_bytes=limit,total_bytes=sum(size for batch in batches for _,size in batch),
        reserve_bytes=space['reserve_bytes'],free_bytes=space['free_bytes'])


def validate_roots(archive,cache):
    archive_text=str(archive).replace('\\','/').lower()
    cache_text=str(cache).replace('\\','/').lower()
    if not (archive_text.startswith('g:/') or archive_text.startswith('//desktop-saai85t/workstation-g/')):
        raise ValueError('Archive staging source must be the designated G archive')
    if not (cache_text.startswith('d:/') or cache_text.startswith('//desktop-saai85t/workstation-d/')):
        raise ValueError('Archive staging destination must be the designated D SSD')


def copy_file(source,destination,archive_root,cache_root,expected_size,*,stop=None,progress=None,verify_progress=None,
              reserve_bytes=None,reserve_fraction=.10):
    """Bounded copy, digest readback, no replacement of an existing cache file.

    Root drive policy is checked by the canonical updater before invoking this
    primitive. Its test seam uses isolated runtime directories. Path containment
    and collision checks still apply in every invocation.
    """
    source=Path(source).resolve();destination=Path(destination).resolve()
    archive_root=Path(archive_root).resolve();cache_root=Path(cache_root).resolve()
    if not source.is_relative_to(archive_root) or not destination.is_relative_to(cache_root) or source==destination:
        raise ValueError('Archive staging path escapes its declared root')
    if type(expected_size) is not int or expected_size<=0:raise ValueError('Remote file size is required for staging')
    if destination.exists():
        if destination.is_file() and destination.stat().st_size==expected_size:return dict(status='cached',bytes=expected_size)
        raise ValueError('Existing cache file is incomplete; reconcile it through the downloader')
    if not source.is_file():return dict(status='archive_missing',bytes=0)
    before=source.stat()
    if before.st_size!=expected_size:return dict(status='archive_size_mismatch',bytes=0)
    if reserve_bytes is not None:
        budget=space_budget(cache_root,reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction)
        if expected_size>budget['usable_bytes']:raise ValueError('SSD free space changed; staging would consume reserved headroom')
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name(destination.name+'.stage-'+uuid4().hex+'.part')
    copied=0;checksum=sha256()
    def check():
        if stop is not None and stop.is_set():raise InterruptedError('Archive staging stopped')
    def check_space(additional_bytes):
        if reserve_bytes is not None:
            budget=space_budget(cache_root,reserve_bytes=reserve_bytes,reserve_fraction=reserve_fraction)
            if additional_bytes>budget['usable_bytes']:
                raise ValueError('SSD free space changed; staging would consume reserved headroom')
    try:
        check()
        with source.open('rb') as reader,temporary.open('xb') as writer:
            while True:
                check();chunk=reader.read(8*1024**2)
                if not chunk:break
                check_space(len(chunk))
                writer.write(chunk);checksum.update(chunk);copied+=len(chunk)
                if progress:progress(copied,expected_size)
            writer.flush();os.fsync(writer.fileno())
        after=source.stat()
        if copied!=expected_size or (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise ValueError('Archive file changed while staging')
        actual=sha256()
        verified=0
        with temporary.open('rb') as reader:
            while True:
                check();chunk=reader.read(8*1024**2)
                if not chunk:break
                actual.update(chunk);verified+=len(chunk)
                if verify_progress:verify_progress(verified,expected_size)
        if actual.digest()!=checksum.digest():raise ValueError('SSD staged copy digest differs')
        check();check_space(0)
        # Windows rename fails when the destination exists, preserving other
        # acquisition workers' output rather than silently replacing it.
        if destination.exists():raise ValueError('Cache file appeared during staging')
        temporary.rename(destination)
        return dict(status='staged',bytes=copied,sha256=checksum.hexdigest())
    finally:
        if temporary.exists():temporary.unlink()
