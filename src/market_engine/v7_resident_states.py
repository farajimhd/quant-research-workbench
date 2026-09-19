"""Bounded resident cache of exact, process-owned V7 stream states.

Spills are private temporary runtime files, never external pickle inputs or
historical authority. A miss restores the whole state, including delta bases.
"""
from collections import OrderedDict
from collections.abc import MutableMapping
from pathlib import Path
import pickle
from tempfile import TemporaryDirectory


class ResidentStates(MutableMapping):
    def __init__(self, root, *, capacity=32):
        if capacity < 1:
            raise ValueError('Resident state capacity must be positive')
        self.directory = TemporaryDirectory(prefix='v7-states-', dir=root)
        self.capacity = capacity
        self.resident = OrderedDict()
        self.paths = {}
        self.serial = 0

    def _room(self):
        while len(self.resident) >= self.capacity:
            key, value = next(iter(self.resident.items()))
            path = self.paths[key]
            temporary = path.with_suffix('.writing')
            with temporary.open('wb') as handle:
                pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
            temporary.replace(path)
            del self.resident[key]

    def __getitem__(self, key):
        if key not in self.resident:
            path = self.paths[key]
            self._room()
            with path.open('rb') as handle:
                self.resident[key] = pickle.load(handle)
        self.resident.move_to_end(key)
        return self.resident[key]

    def __setitem__(self, key, value):
        if key not in self.resident:
            self._room()
        if key not in self.paths:
            self.paths[key] = Path(self.directory.name) / f'{self.serial}.pickle'
            self.serial += 1
        self.resident[key] = value
        self.resident.move_to_end(key)

    def __delitem__(self, key):
        path = self.paths.pop(key)
        self.resident.pop(key, None)
        path.unlink(missing_ok=True)

    def __contains__(self, key):
        return key in self.paths

    def __iter__(self):
        return iter(self.paths)

    def __len__(self):
        return len(self.paths)

    def clear(self):
        self.resident.clear()
        self.paths.clear()
        self.directory.cleanup()
