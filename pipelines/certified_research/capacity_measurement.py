"""Exact isolated certified-output disk measurements, never legacy rate guesses."""
from uuid import UUID
import re
from . import schema
from .contracts import digest,identifier,literal

VERSION='isolated-certified-persisted-bytes-v1'


def measure(query,database,dataset_id,tables,verify_authority,*,maximum_parts=4096):
    """Measure actual parts only when every stored row belongs to this pilot.

    The callback independently verifies the complete dataset authority before
    and after measurement. This supplies persisted-byte evidence only: source
    representativeness and measured staging/merge peaks remain separate gates.
    No profile or capacity admission is fabricated from this snapshot.
    """
    identifier(database);dataset_id=str(UUID(dataset_id));tables=tuple(tables)
    allowed={t.name for t in schema.TABLES if {'dataset_id'}<=set(c.name for c in t.columns)}
    if (not tables or len(tables)>16 or len(tables)!=len(set(tables)) or set(tables)-allowed
            or type(maximum_parts) is not int or not 1<=maximum_parts<=4096):
        raise ValueError('Invalid bounded isolated pilot table scope')
    schema.verify_auxiliary_storage(query,database,tables)
    def root():
        value=verify_authority()
        if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):
            raise ValueError('Pilot measurement requires an independently verified dataset root')
        return value
    verified=root()
    names=','.join(literal(t) for t in tables)
    def parts():
        rows=query('SELECT table,name,rows,bytes_on_disk,data_compressed_bytes,data_uncompressed_bytes,disk_name '
            f'FROM system.parts WHERE active AND database={literal(database)} AND table IN ({names}) '
            f'ORDER BY table,name LIMIT {maximum_parts+1}')
        if len(rows)>maximum_parts:raise ValueError('Pilot physical part evidence exceeds its bound')
        if any(r['disk_name']!='live_market_ssd' for r in rows):raise ValueError('Pilot parts are misplaced')
        return rows
    before=parts();measured=[]
    for name in sorted(tables):
        census=query(f'SELECT count() n,uniqExact(dataset_id) datasets,toString(any(dataset_id)) dataset '
                     f'FROM {database}.{identifier(name)} SETTINGS final=0')[0]
        if int(census['n']) and (int(census['datasets'])!=1 or str(census['dataset'])!=dataset_id):
            raise ValueError('Physical pilot bytes cannot be attributed across mixed datasets')
        selected=[r for r in before if r['table']==name]
        if sum(int(r['rows']) for r in selected)!=int(census['n']):
            raise ValueError('Pilot physical/logical row counts differ; clean isolated evidence required')
        measured.append(dict(table=name,rows=int(census['n']),parts=len(selected),
            disk_bytes=sum(int(r['bytes_on_disk']) for r in selected),
            compressed_bytes=sum(int(r['data_compressed_bytes']) for r in selected),
            uncompressed_bytes=sum(int(r['data_uncompressed_bytes']) for r in selected)))
    if root()!=verified or digest(parts())!=digest(before):
        raise ValueError('Certified pilot authority or physical parts changed during measurement')
    result=dict(version=VERSION,status='measured',database=database,dataset_id=dataset_id,
        authority_root=verified,tables=measured,part_evidence_hash=digest(before),active_parts_only=True,projection_ready=False,
        limitations=('Representative population and comparable product-unit evidence are still required',
                     'Staging and merge peaks are not measured by this persisted snapshot'))
    result['evidence_hash']=digest(result)
    return result
