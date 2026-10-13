"""Ingestion-owned atomic G archive to D SSD copy for new source acquisition."""
from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
from uuid import uuid4


def validate_roots(archive,cache):
    archive_text=str(archive).replace('\\','/').lower()
    cache_text=str(cache).replace('\\','/').lower()
    if not (archive_text.startswith('g:/') or archive_text.startswith('//desktop-saai85t/workstation-g/')):
        raise ValueError('Archive staging source must be the designated G archive')
    if not (cache_text.startswith('d:/') or cache_text.startswith('//desktop-saai85t/workstation-d/')):
        raise ValueError('Archive staging destination must be the designated D SSD')


def copy_file(source,destination,archive_root,cache_root,expected_size,*,stop=None,progress=None,verify_progress=None):
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
    destination.parent.mkdir(parents=True,exist_ok=True)
    temporary=destination.with_name(destination.name+'.stage-'+uuid4().hex+'.part')
    copied=0;checksum=sha256()
    def check():
        if stop is not None and stop.is_set():raise InterruptedError('Archive staging stopped')
    try:
        check()
        with source.open('rb') as reader,temporary.open('xb') as writer:
            while True:
                check();chunk=reader.read(8*1024**2)
                if not chunk:break
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
        check()
        # Windows rename fails when the destination exists, preserving other
        # acquisition workers' output rather than silently replacing it.
        if destination.exists():raise ValueError('Cache file appeared during staging')
        temporary.rename(destination)
        return dict(status='staged',bytes=copied,sha256=checksum.hexdigest())
    finally:
        if temporary.exists():temporary.unlink()
