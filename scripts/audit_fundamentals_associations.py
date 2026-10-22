"""Read-only, resumable relationship audit of the complete retained snapshot."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json,time
from datetime import date
from pipelines.fundamentals.population import snapshot_population
from pipelines.fundamentals.associations import exhibit_sql,cofiling_sql,coverage
from pipelines.certified_research.contracts import digest
from scripts.run_fundamentals import Client,save,lock
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files


def fingerprint(query):
    tables="('sec_filing_document_v3','sec_filing_entity_v3','id_issuer_relationship_v1')"
    if query(f"SELECT mutation_id FROM system.mutations WHERE database='q_live' AND table IN {tables} AND NOT is_done"):
        raise ValueError('Relationship evidence mutation pending')
    return digest([query(f"SELECT name,uuid,create_table_query FROM system.tables WHERE database='q_live' AND name IN {tables} ORDER BY name"),
        query(f"SELECT table,partition_id,name,hash_of_all_files FROM system.parts WHERE database='q_live' AND table IN {tables} AND active ORDER BY table,partition_id,name")])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot-date',required=True,type=date.fromisoformat)
    p.add_argument('--run-dir',required=True)
    p.add_argument('--endpoint',default='http://192.168.1.218:18123')
    a=p.parse_args();base=Path('D:/TradingML/runtimes').resolve();root=Path(a.run_dir).resolve()
    if not base.is_dir() or not root.is_relative_to(base):raise ValueError('Available runtime root required')
    root.mkdir(parents=True,exist_ok=True)
    load_env_files(discover_clickhouse_env_files(),verbose=False)
    client=Client(a.endpoint);start=time.monotonic()
    try:
        with lock(root/'audit.lock'):
            pop=snapshot_population(client.query,a.snapshot_date)
            ciks=pop['families']['sec_bulk'];source=fingerprint(client.query)
            request=dict(snapshot=str(a.snapshot_date),population=pop['provenance'],source=source,
                recipe=digest(Path(__file__).read_text()+__import__('inspect').getsource(__import__('pipelines.fundamentals.associations',fromlist=['']))))
            req=root/'request.json'
            if req.exists() and json.loads(req.read_text())!=request:raise ValueError('Audit inputs changed; use a new run directory')
            save(req,request);save(root/'population.json',pop)
            jobs=[('exhibit-'+str(i),exhibit_sql(ciks[i:i+256])) for i in range(0,len(ciks),256)]
            jobs += [('cofiling-'+str(i),cofiling_sql(ciks,i)) for i in range(64)]
            complete=0;reused=0
            for label,sql in jobs:
                if (root/'STOP').exists():raise InterruptedError('STOP requested; completed audit receipts preserved')
                path=root/(label+'.json')
                save(root/'status.json',dict(state='running',active=label,completed=complete,reused=reused,queued=len(jobs)-complete-1))
                if path.exists():
                    unit=json.loads(path.read_text())
                    if unit['sql_hash']!=digest(sql) or unit['rows_hash']!=digest(unit['rows']):raise ValueError('Audit receipt mismatch')
                    reused+=1
                else:
                    t=time.monotonic();rows=client.query(sql)
                    save(path,dict(sql_hash=digest(sql),rows=rows,rows_hash=digest(rows),seconds=time.monotonic()-t))
                complete+=1
                print(f'Audit {complete}/{len(jobs)} complete | {reused} reused | {label} | {time.monotonic()-start:.1f}s',flush=True)
            if fingerprint(client.query)!=source:raise ValueError('Evidence changed during audit; results not qualified')
            relationships=client.query('SELECT * FROM q_live.id_issuer_relationship_v1 FINAL ORDER BY parent_cik,child_cik,relationship_id')
            exhibits=[];candidates=[]
            for label,_ in jobs:
                rows=json.loads((root/(label+'.json')).read_text())['rows']
                (exhibits if label.startswith('exhibit') else candidates).extend(rows)
            report=coverage(ciks,exhibits,candidates,relationships)
            save(root/'coverage.json',report);save(root/'verified-relationships.json',relationships)
            save(root/'summary.json',dict(symbols=pop['provenance']['population_rows'],unresolved=pop['provenance']['sec_unresolved_symbols'],
                issuers=len(ciks),issuers_with_exhibits=len(exhibits),issuers_with_cofiling_candidates=len({r['primary_cik'] for r in candidates}),
                candidate_pairs=len({(r['primary_cik'],r['entity_cik']) for r in candidates}),
                verified_relationships=len(relationships),seconds=time.monotonic()-start,
                limitation='Metadata discovery only; documents require ownership review. No candidate is a certified relationship.'))
            save(root/'status.json',dict(state='complete',active=None,completed=complete,reused=reused,queued=0,failed=0))
    except BaseException as e:
        save(root/'status.json',dict(state='interrupted' if isinstance(e,(KeyboardInterrupt,InterruptedError)) else 'failed',error=str(e)))
        raise
    finally:client.close()


if __name__=='__main__':main()
