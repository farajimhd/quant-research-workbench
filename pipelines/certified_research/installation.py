"""Complete derived-data installation catalog; canonical ingestion owns its schemas."""
import re
from . import schema
from .contracts import digest,identifier,literal


def _market_tables(database):
    from .market import staging_ddl
    # The shared kernel uses this restricted declaration grammar. Fail on changes.
    for ddl in staging_ddl(database):
        match=re.fullmatch(r'CREATE TABLE IF NOT EXISTS \w+\.(\w+) \((.*)\)\s*'
            r'ENGINE=MergeTree PARTITION BY (\w+\(\w+\)) ORDER BY \(([^)]+)\)\s*SETTINGS '
            r"storage_policy='live_market_ssd', default_compression_codec='ZSTD\(1\)'",ddl,re.S)
        if not match:raise ValueError('Unsupported market staging DDL grammar')
        name,body,partition,order=match.groups();pieces=[];start=0;depth=0
        for index,char in enumerate(body):
            if char=='(':depth+=1
            elif char==')':depth-=1
            elif char==',' and depth==0:pieces.append(body[start:index]);start=index+1
            if depth<0:raise ValueError('Invalid market staging declaration')
        if depth:raise ValueError('Unbalanced market staging declaration')
        pieces.append(body[start:]);columns=[]
        for piece in pieces:
            column=re.fullmatch(r'\s*(\w+)\s+(\w+(?:\([^)]*\))?)(?:\s+CODEC\((.*)\))?\s*',piece,re.S)
            if not column:raise ValueError('Unsupported market staging column declaration')
            col,kind,codec=column.groups()
            columns.append(schema.c(col,kind,codec or 'ZSTD(1)'))
        yield schema.Table(name,tuple(columns),tuple(v.strip() for v in order.split(',')),partition)


def catalog(database='arte'):
    from . import activity,float_data,reference_producer,short_pressure,historical_identity
    identifier(database)
    groups=(('published',schema.TABLES),('acquisition',(historical_identity.persistence_table(),)),
        ('staging',(*_market_tables(database),reference_producer.staging_table(),float_data.staging_table(),
                    activity.staging_table(),short_pressure.staging_table())))
    result=tuple((group,table) for group,tables in groups for table in tables)
    if len({table.name for _,table in result})!=len(result):raise ValueError('Duplicate installation table')
    return result


def manifest(database='arte'):
    tables=[dict(group=group,name=t.name,order=t.order,partition=t.partition,
                 columns=[vars(c) for c in t.columns]) for group,t in catalog(database)]
    return dict(schema_hash=schema.schema_hash(),installation_hash=digest(tables),
                storage_policy='live_market_ssd',tables=tables)


def verify(query,database,*,require_tables=False):
    """Validate exact definitions and all actual parts before any installation write."""
    schema.verify_storage(query,database,require_tables=require_tables)
    tables={t.name:t for _,t in catalog(database)}
    names=','.join(literal(n) for n in tables)
    rows=query(f'SELECT name,engine,sorting_key,partition_key,storage_policy FROM system.tables '
               f'WHERE database={literal(database)} AND name IN ({names})')
    if require_tables and {r['name'] for r in rows}!=set(tables):raise ValueError('Installation tables are missing')
    compact=lambda value:value.replace(' ','').replace('`','')
    for row in rows:
        t=tables[row['name']]
        if (row['storage_policy']!='live_market_ssd' or row['engine']!='MergeTree'
                or compact(row['sorting_key'])!=','.join(t.order) or compact(row['partition_key'])!=compact(t.partition)):
            raise ValueError('Installation engine/key/storage differs; explicit migration required')
    columns=query(f'SELECT table,name,type,compression_codec FROM system.columns '
                  f'WHERE database={literal(database)} AND table IN ({names})')
    present={r['name'] for r in rows}
    actual={(r['table'],r['name']):(compact(r['type']),compact(r['compression_codec'])) for r in columns}
    expected={(t.name,c.name):(compact(c.type),'CODEC('+compact(c.codec)+')')
              for t in tables.values() if t.name in present for c in t.columns}
    if actual!=expected:
        differences=[(key,expected.get(key),actual.get(key)) for key in sorted(set(actual)|set(expected))
                     if actual.get(key)!=expected.get(key)]
        raise ValueError(f'Installation column/codec differs; explicit migration required: {differences[:3]}')
    parts=query(f'SELECT table,disk_name FROM system.parts WHERE active AND database={literal(database)} '
                f'AND table IN ({names}) GROUP BY table,disk_name')
    if any(r['disk_name']!='live_market_ssd' for r in parts):raise ValueError('Installation parts are misplaced')
    return dict(tables=len(rows),parts=len(parts),installation_hash=manifest(database)['installation_hash'])


def install(query,database,claim):
    from .publication import DictionaryClaim
    if (not isinstance(claim,DictionaryClaim) or claim.dictionary_database!=database):
        raise ValueError('Installation requires global database dictionary ownership')
    claim.check();before=verify(query,database)
    existing={r['name'] for r in query(f'SELECT name FROM system.tables WHERE database={literal(database)}')}
    for _,table in catalog(database):
        if table.name not in existing:
            claim.check();query(table.ddl(database),read=False)
    claim.check();after=verify(query,database,require_tables=True)
    return dict(after,created=after['tables']-before['tables'])
