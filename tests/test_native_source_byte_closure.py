"""Integrity controls for the verification kernel, not native qualification."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from src.backend.native_source_byte_closure import (
    SourceByteClosure, source_closure_metadata_digest, verify_source_byte_closure,
)


def closure(root, paths=('a.py', 'nested/b.py'), *, max_bytes=1000):
    for path in paths:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'VALUE = 1\n')
    pins = tuple(sorted((path, sha256((root / path).read_bytes()).hexdigest()) for path in paths))
    required = tuple(sorted(paths))
    return SourceByteClosure(pins, required, len(paths), max_bytes,
        source_closure_metadata_digest(pins, required, len(paths), max_bytes))


def test_exact_source_and_repeated_read(tmp_path):
    seal = closure(tmp_path)
    assert verify_source_byte_closure(tmp_path, seal) == seal.metadata_digest
    assert verify_source_byte_closure(tmp_path, seal) == seal.metadata_digest


@pytest.mark.parametrize('replacement', [b'VALUE = 2\n', b'VALUE = 1 # changed\n', b''])
def test_changed_code_comments_and_empty_source_rejected(tmp_path, replacement):
    seal = closure(tmp_path)
    (tmp_path / 'a.py').write_bytes(replacement)
    with pytest.raises(ValueError, match='Approved source bytes differ'):
        verify_source_byte_closure(tmp_path, seal)


def test_missing_file_rejected(tmp_path):
    seal = closure(tmp_path)
    (tmp_path / 'a.py').unlink()
    with pytest.raises(ValueError, match='missing or symbolic'):
        verify_source_byte_closure(tmp_path, seal)


def test_omitted_required_dependency_rejected(tmp_path):
    seal = closure(tmp_path)
    with pytest.raises(ValueError, match='omits a required'):
        replace(seal, required_paths=('a.py', 'missing.py'))


@pytest.mark.parametrize('path', ['/absolute.py', '../outside.py', 'a/../b.py',
    'a//b.py', './a.py', 'C:/a.py', 'a\\b.py', 'data.json'])
def test_noncanonical_paths_rejected(path):
    pins = ((path, 'a' * 64),)
    with pytest.raises(ValueError, match='path|paths'):
        SourceByteClosure(pins, (path,), 1, 100, 'b' * 64)


def test_declared_byte_bound_is_enforced(tmp_path):
    seal = closure(tmp_path, max_bytes=1)
    with pytest.raises(ValueError, match='byte bound'):
        verify_source_byte_closure(tmp_path, seal)


def test_metadata_mutation_rejected(tmp_path):
    seal = closure(tmp_path)
    object.__setattr__(seal, 'max_bytes', 2000)
    with pytest.raises(ValueError, match='metadata changed'):
        verify_source_byte_closure(tmp_path, seal)


def test_source_changed_between_passes_rejected(tmp_path, monkeypatch):
    seal = closure(tmp_path)
    original = Path.open
    opened = 0

    def reading(path, *args, **kwargs):
        nonlocal opened
        if args == ('rb',):
            opened += 1
            if opened == 3:
                with original(tmp_path / 'a.py', 'wb') as stream:
                    stream.write(b'VALUE = 2\n')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', reading)
    with pytest.raises(ValueError, match='Approved source bytes differ'):
        verify_source_byte_closure(tmp_path, seal)


def test_unissued_descriptor_type_rejected(tmp_path):
    with pytest.raises(ValueError, match='Exact source root'):
        verify_source_byte_closure(tmp_path, object())
