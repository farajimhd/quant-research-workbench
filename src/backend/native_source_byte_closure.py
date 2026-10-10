"""Exact byte verification kernel; never issue a source/execution certificate.

The immutable release owner must approve the pins, establish complete dependency
coverage and retain parent compatibility independently. Callers cannot turn a
self-consistent seal into admission authority through this function.
"""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
import json
import re


def _relative(value):
    if (type(value) is not str or not value or '\\' in value
            or ':' in value or not value.endswith('.py')):
        raise ValueError('Source closure needs exact relative Python paths')
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ValueError('Source closure path is not canonical')
    return value


@dataclass(frozen=True, slots=True)
class SourceByteClosure:
    pins: tuple[tuple[str, str], ...]
    required_paths: tuple[str, ...]
    max_files: int
    max_bytes: int
    metadata_digest: str

    def __post_init__(self):
        if (type(self.max_files) is not int or self.max_files < 1
                or type(self.max_bytes) is not int or self.max_bytes < 1
                or type(self.pins) is not tuple or not self.pins
                or len(self.pins) > self.max_files
                or any(type(pair) is not tuple or len(pair) != 2 for pair in self.pins)
                or type(self.required_paths) is not tuple or not self.required_paths):
            raise ValueError('Source closure requires explicit bounded immutable metadata')
        paths = tuple(_relative(pair[0]) for pair in self.pins)
        if paths != tuple(sorted(set(paths))):
            raise ValueError('Source closure pins need ordered unique paths')
        if any(type(pair[1]) is not str or re.fullmatch('[0-9a-f]{64}', pair[1]) is None
               for pair in self.pins):
            raise ValueError('Source closure pins need exact SHA256 values')
        required = tuple(_relative(value) for value in self.required_paths)
        if required != tuple(sorted(set(required))) or not set(required).issubset(paths):
            raise ValueError('Source closure omits a required dependency')
        if (type(self.metadata_digest) is not str
                or self.metadata_digest != source_closure_metadata_digest(
                    self.pins, self.required_paths, self.max_files, self.max_bytes)):
            raise ValueError('Source closure metadata changed')


def source_closure_metadata_digest(pins, required_paths, max_files, max_bytes):
    value = dict(pins=pins, required_paths=required_paths,
                 max_files=max_files, max_bytes=max_bytes)
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _source_path(root, relative):
    path = root.joinpath(*relative.split('/'))
    if path.is_symlink() or not path.is_file():
        raise ValueError('Source closure file is missing or symbolic: ' + relative)
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError('Source closure directory is symbolic: ' + relative)
    if path.resolve(strict=True) != path:
        raise ValueError('Source closure resolves through a different location: ' + relative)
    return path


def verify_source_byte_closure(root, seal):
    """Read all approved bytes twice; no AST reconstruction, cache or lease.

    Both passes must match every pin and the explicit total byte bound. An
    independent immutable-manifest/source owner remains responsible for pin
    approval, complete dependency selection and loaded-code identity checks.
    """
    if type(root) is not type(Path()) or type(seal) is not SourceByteClosure:
        raise ValueError('Exact source root and byte closure required')
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Source closure root must be a directory')
    seal.__post_init__()
    identity = (seal.pins, seal.required_paths, seal.max_files,
                seal.max_bytes, seal.metadata_digest)
    sizes = None
    for _ in range(2):
        total = 0
        observed_sizes = []
        for relative, expected in seal.pins:
            path = _source_path(root, relative)
            # Check file length before allocating; reading is bounded as well.
            remaining = seal.max_bytes - total
            if path.stat().st_size > remaining:
                raise ValueError('Source closure exceeds its declared byte bound')
            with path.open('rb') as stream:
                raw = stream.read(remaining + 1)
            total += len(raw)
            if total > seal.max_bytes:
                raise ValueError('Source closure exceeds its declared byte bound')
            if sha256(raw).hexdigest() != expected:
                raise ValueError('Approved source bytes differ: ' + relative)
            observed_sizes.append(len(raw))
        if sizes is not None and sizes != observed_sizes:
            raise ValueError('Source closure changed during verification')
        sizes = observed_sizes
        seal.__post_init__()
        if identity != (seal.pins, seal.required_paths, seal.max_files,
                        seal.max_bytes, seal.metadata_digest):
            raise ValueError('Source closure identity changed during verification')
    return seal.metadata_digest
