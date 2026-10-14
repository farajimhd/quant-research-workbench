"""Normalized collection metadata; independent of existing dataset schema hashes."""
import json
from datetime import datetime, timezone
from . import schema, collection
from .contracts import digest, identifier, literal
from .publication import insert_rows


def tables():
    c = schema.c
    return (
        schema.Table('research_collection_v1', (
            c('collection_id', 'UUID'), c('collection_hash', 'FixedString(64)'),
            c('partition_metadata', 'String'), c('member_count', 'UInt32', 'T64,ZSTD(1)'),
            c('created_at', "DateTime64(6,'UTC')", 'Delta,ZSTD(1)')), ('collection_id',)),
        schema.Table('research_collection_member_v1', (
            c('collection_id', 'UUID'), c('member_index', 'UInt32', 'T64,ZSTD(1)'),
            c('dataset_id', 'UUID'), c('issuer_keys', 'Array(UInt64)'),
            c('plan_hash', 'FixedString(64)')), ('collection_id', 'member_index')),
        schema.Table('research_collection_seal_v1', (
            c('collection_id', 'UUID'), c('collection_hash', 'FixedString(64)'),
            c('root_hash', 'FixedString(64)'),
            c('member_roots', 'Array(Tuple(UUID,FixedString(64),FixedString(64)))'),
            c('published_at', "DateTime64(6,'UTC')", 'Delta,ZSTD(1)')), ('collection_id',)))


def verify_tables(query, database):
    schema.verify_auxiliary_storage(query, database, tuple(t.name for t in tables()))
    compact = lambda value: value.replace(' ', '').replace('`', '')
    for table in tables():
        rows = query(f'SELECT engine,sorting_key,partition_key FROM system.tables '
                     f'WHERE database={literal(database)} AND name={literal(table.name)}')
        if (len(rows) != 1 or rows[0]['engine'] != 'MergeTree'
                or compact(rows[0]['sorting_key']) != ','.join(table.order)
                or compact(rows[0]['partition_key']) != compact(table.partition)):
            raise ValueError('Collection table engine/key/partition differs')
        actual = query(f'SELECT name,type,compression_codec FROM system.columns '
                       f'WHERE database={literal(database)} AND table={literal(table.name)} ORDER BY position')
        expected = [(c.name, compact(c.type), 'CODEC(' + compact(c.codec) + ')') for c in table.columns]
        if [(r['name'], compact(r['type']), compact(r['compression_codec'])) for r in actual] != expected:
            raise ValueError('Collection table schema/codec differs; explicit migration required')


class Store:
    def __init__(self, query, database, collection_id, claim):
        from uuid import UUID
        UUID(collection_id)
        self.query = query; self.database = identifier(database)
        self.id = collection_id; self.claim = claim
        self.where = f'collection_id=toUUID({literal(collection_id)})'

    def guard(self):
        self.claim.check(); verify_tables(self.query, self.database)

    def rows(self, table):
        return self.query(f'SELECT * FROM {self.database}.{table} WHERE {self.where} LIMIT 16385')

    def install(self, frozen):
        if frozen['collection_id'] != self.id:
            raise ValueError('Collection storage owner differs')
        # Validate the complete definition before creating any table or writing.
        collection.Controller(frozen, None, None, None, None)
        metadata = dict(frozen['partition'])
        metadata.pop('groups'); metadata.pop('issuer_keys')
        metadata_json = json.dumps(metadata, sort_keys=True, separators=(',', ':'))
        if len(metadata_json.encode()) > 4*1024**2:
            raise ValueError('Collection census metadata exceeds its explicit bound')
        self.claim.check(); schema.verify_storage(self.query, self.database)
        for table in tables(): self.query(table.ddl(self.database), read=False)
        self.guard()
        expected = [dict(collection_id=self.id, member_index=i, **m)
                    for i, m in enumerate(frozen['members'])]
        old = self.rows('research_collection_member_v1')
        indexed = {}
        for row in old:
            row = dict(row, collection_id=str(row['collection_id']), dataset_id=str(row['dataset_id']),
                       issuer_keys=tuple(row['issuer_keys']), member_index=int(row['member_index']))
            index = row['member_index']
            if index in indexed or not 0 <= index < len(expected) or row != expected[index]:
                raise ValueError('Stored collection members differ or are duplicated')
            indexed[index] = row
        missing = [row for i, row in enumerate(expected) if i not in indexed]
        for offset in range(0, len(missing), 128):
            self.guard(); insert_rows(self.query, self.database, 'research_collection_member_v1', missing[offset:offset+128])
        header = dict(collection_id=self.id, collection_hash=frozen['collection_hash'],
                      partition_metadata=metadata_json,
                      member_count=len(expected))
        headers = self.rows('research_collection_v1')
        if headers:
            if len(headers) != 1 or {k: str(headers[0][k]) for k in header} != {k: str(v) for k, v in header.items()}:
                raise ValueError('Stored collection header differs or is duplicated')
        else:
            self.guard(); insert_rows(self.query, self.database, 'research_collection_v1',
                                     [dict(header, created_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))])
        if self.load() != frozen: raise ValueError('Collection install readback differs')

    def load(self):
        self.guard(); headers = self.rows('research_collection_v1')
        if len(headers) != 1: raise ValueError('Collection header missing or duplicated')
        header = headers[0]; rows = self.rows('research_collection_member_v1')
        if (not 1 <= int(header['member_count']) <= 16384
                or len(header['partition_metadata'].encode()) > 4*1024**2):
            raise ValueError('Collection metadata exceeds its explicit bound')
        if len(rows) != int(header['member_count']): raise ValueError('Collection members incomplete')
        rows.sort(key=lambda r: int(r['member_index']))
        if [int(r['member_index']) for r in rows] != list(range(len(rows))):
            raise ValueError('Collection member indexes incomplete or duplicated')
        members = tuple(collection.Member(str(r['dataset_id']), tuple(r['issuer_keys']), r['plan_hash']) for r in rows)
        partition = json.loads(header['partition_metadata'])
        partition['groups'] = tuple(m.issuer_keys for m in members)
        partition['issuer_keys'] = tuple(sorted(k for m in members for k in m.issuer_keys))
        if 'session_counts' in partition: partition['session_counts'] = tuple(tuple(r) for r in partition['session_counts'])
        frozen = collection.definition(self.id, partition, members)
        if frozen['collection_hash'] != header['collection_hash']: raise ValueError('Collection header fingerprint differs')
        self.guard(); return frozen

    def controller(self, load_launch, source_guard, capacity_guard):
        """Bind execution to actual immutable ClickHouse metadata and ownership."""
        frozen = self.load()
        def guard():
            source_guard()
            if self.load() != frozen:
                raise ValueError('Durable collection definition changed during execution')
        return collection.Controller(frozen, load_launch, guard, capacity_guard, self.seal)

    def seal(self, certificate):
        frozen = self.load()
        body = dict(certificate); root = body.pop('root_hash')
        if any(len(m) != 3 or not isinstance(m[2],str) or len(m[2]) != 64
               or any(c not in '0123456789abcdef' for c in m[2]) for m in body['members']):
            raise ValueError('Collection member authority root is invalid')
        if (digest(body) != root or body['collection_hash'] != frozen['collection_hash']
                or tuple((m[0], m[1]) for m in body['members']) != tuple((m['dataset_id'], m['plan_hash']) for m in frozen['members'])):
            raise ValueError('Collection seal differs from complete frozen membership')
        expected = dict(collection_id=self.id, collection_hash=body['collection_hash'], root_hash=root,
                        member_roots=tuple(tuple(m) for m in body['members']))
        def read():
            return [dict(collection_id=str(r['collection_id']), collection_hash=r['collection_hash'],
                         root_hash=r['root_hash'], member_roots=tuple((str(m[0]), m[1], m[2]) for m in r['member_roots']))
                    for r in self.rows('research_collection_seal_v1')]
        old = read()
        if old and old != [expected]: raise ValueError('Collection seal differs or is duplicated')
        if not old:
            self.guard(); insert_rows(self.query, self.database, 'research_collection_seal_v1',
                                     [dict(expected, published_at=datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'))])
        self.guard()
        if read() != [expected]: raise ValueError('Collection seal readback differs')
