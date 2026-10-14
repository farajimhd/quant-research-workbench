"""Exact isolated certified-output disk measurements, never legacy rate guesses."""
from uuid import UUID
from threading import Lock
from copy import deepcopy
import re
from . import schema
from .contracts import digest,identifier,literal

VERSION='isolated-certified-persisted-bytes-v1'


class PhaseObservations:
    """Bounded pilot samples; observed maxima never become admission bounds."""
    def __init__(self,snapshot,*,maximum_samples=1024):
        if not callable(snapshot) or type(maximum_samples) is not int or not 1<=maximum_samples<=4096:
            raise ValueError('Invalid bounded storage observation collector')
        self.snapshot=snapshot;self.maximum=maximum_samples;self.samples=[];self.lock=Lock()

    def record(self,product,phase,query):
        if phase not in ('before_write','after_staging','after_publish','after_cleanup'):
            raise ValueError('Unknown storage observation phase')
        identifier(product)
        with self.lock:
            if len(self.samples)>=self.maximum:raise ValueError('Storage observation bound exceeded; no samples dropped')
            value=self.snapshot(query)
            body=dict(value);hash_=body.pop('evidence_hash',None)
            if hash_!=digest(body) or value.get('upper_bound_proven') is not False:
                raise ValueError('Invalid observed storage snapshot')
            self.samples.append(dict(sequence=len(self.samples),product=product,phase=phase,snapshot=deepcopy(value)))

    def result(self):
        with self.lock:
            value=dict(version='phase-observed-storage-v1',samples=tuple(deepcopy(self.samples)),upper_bound_proven=False,
                observed_maxima={name:max((s['snapshot'][name] for s in self.samples),default=None) for name in
                    ('staging_bytes','output_inactive_bytes','merge_input_bytes','merge_written_uncompressed_bytes')})
            return dict(value,evidence_hash=digest(value))


def transient_snapshot(query,database,staging_tables,output_tables):
    """Observed transient counters, not a certified upper bound on peaks."""
    from .installation import catalog
    identifier(database);staging_tables=tuple(staging_tables);output_tables=tuple(output_tables)
    selected=(*staging_tables,*output_tables)
    known={t.name:group for group,t in catalog(database)}
    if (not selected or len(selected)>64 or len(selected)!=len(set(selected))
            or any(known.get(t)!='staging' for t in staging_tables)
            or any(known.get(t)!='published' for t in output_tables)):
        raise ValueError('Invalid bounded transient measurement scope')
    schema.verify_auxiliary_storage(query,database,selected)
    names=','.join(literal(t) for t in selected)
    rows=query('SELECT table,active,disk_name,sum(bytes_on_disk) bytes FROM system.parts '
        f'WHERE database={literal(database)} AND table IN ({names}) GROUP BY table,active,disk_name ORDER BY table,active,disk_name')
    merges=query('SELECT table,sum(total_size_bytes_compressed) input_bytes,sum(bytes_written_uncompressed) written_uncompressed '
        f'FROM system.merges WHERE database={literal(database)} AND table IN ({names}) GROUP BY table ORDER BY table')
    if any(r['table'] not in selected or int(r['bytes'])<0 or int(r['active']) not in (0,1) for r in rows):
        raise ValueError('Invalid transient part counters')
    if any(r['disk_name']!='live_market_ssd' for r in rows):
        raise ValueError('Active or inactive transient parts are misplaced')
    if any(r['table'] not in selected or int(r['input_bytes'])<0 or int(r['written_uncompressed'])<0 for r in merges):
        raise ValueError('Invalid transient merge counters')
    result=dict(version='observed-transient-storage-v2',database=database,staging_tables=staging_tables,
        output_tables=output_tables,staging_bytes=sum(int(r['bytes']) for r in rows if r['table'] in staging_tables),
        output_inactive_bytes=sum(int(r['bytes']) for r in rows if r['table'] in output_tables and not int(r['active'])),
        merge_input_bytes=sum(int(r['input_bytes']) for r in merges),
        merge_written_uncompressed_bytes=sum(int(r['written_uncompressed']) for r in merges),
        upper_bound_proven=False,part_counters=rows,merge_counters=merges)
    result['evidence_hash']=digest(result)
    return result


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
