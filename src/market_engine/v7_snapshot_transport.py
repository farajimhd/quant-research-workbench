"""Lossless V7 transport. Deltas change delivery, never book semantics."""
from copy import deepcopy
from uuid import uuid4

CONTRACT = 'v7-snapshot-delta-1'
ALIASES = ('unified_levels', 'qmd_structure_unified_levels')


class Encoder:
    def __init__(self):
        self.version = None
        self.metadata = {}
        self.rows = {}

    def encode(self, snapshot, base_version=None):
        levels = snapshot[ALIASES[0]]
        if levels != snapshot[ALIASES[1]]:
            raise ValueError('V7 snapshot aliases disagree')
        rows = {row['unified_level_id']: row for row in levels}
        if len(rows) != len(levels):
            raise ValueError('Duplicate V7 level identity')
        metadata = {k: v for k, v in snapshot.items() if k not in ALIASES}
        reset = not self.version or base_version != self.version
        previous_rows = {} if reset else self.rows
        previous_metadata = {} if reset else self.metadata
        version = uuid4().hex
        packet = dict(contract=CONTRACT, version=version,
                      base_version=None if reset else self.version,
                      metadata={k: v for k, v in metadata.items() if k not in previous_metadata or previous_metadata[k] != v},
                      removed_metadata=[k for k in previous_metadata if k not in metadata],
                      upsert=[v for k, v in rows.items() if k not in previous_rows or previous_rows[k] != v],
                      removed=[k for k in previous_rows if k not in rows],
                      order=list(rows))
        # The engine may mutate its fits after this response. Freeze the base.
        self.rows = deepcopy(rows)
        self.metadata = deepcopy(metadata)
        self.version = version
        return packet


class Decoder:
    def __init__(self):
        self.version = None
        self.metadata = {}
        self.rows = {}

    def decode(self, packet):
        if packet.get('contract') != CONTRACT:
            raise ValueError('Unsupported V7 delta contract')
        base = packet['base_version']
        if base is not None and base != self.version:
            raise ValueError('V7 delta base mismatch')
        rows = dict(self.rows) if base is not None else {}
        metadata = dict(self.metadata) if base is not None else {}
        for key in packet['removed']: rows.pop(key)
        for row in packet['upsert']: rows[row['unified_level_id']] = row
        for key in packet['removed_metadata']: metadata.pop(key)
        metadata.update(packet['metadata'])
        order = packet['order']
        if len(order) != len(set(order)) or set(order) != set(rows):
            raise ValueError('V7 delta level order mismatch')
        levels = [rows[key] for key in order]
        result = dict(metadata, unified_levels=levels, qmd_structure_unified_levels=levels)
        self.rows, self.metadata, self.version = rows, metadata, packet['version']
        return result
