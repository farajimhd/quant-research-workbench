"""Read-only full-scope SEC census and bounded original-document parity pilot."""
import os,sys
os.environ['PYTHONDONTWRITEBYTECODE']='1';sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse,json,time
from collections import Counter
from datetime import date,datetime,timezone
from types import SimpleNamespace
from pipelines.certified_research import sec_document_lineage,sec_fundamental_context,sec_fundamental_reconciliation,sec_precision,facts
from pipelines.certified_research import sec_document_payload
from pipelines.certified_research.contracts import digest,literal
from pipelines.fundamentals.eligibility import require_eligible
from scripts.run_fundamentals import Client,save
from research.mlops.env import load_env_files
from research.mlops.clickhouse import discover_clickhouse_env_files


def inspect_document(query,row):
    """Profile the production parser and parity code without publishing evidence."""
    timing={};start=time.perf_counter();previous=start
    def step(label):
        nonlocal previous
        now=time.perf_counter();timing[label]=now-previous;previous=now
    cik=row['cik'];accession=row['accession'];name=row['document']
    require_eligible('issuer_cik',[cik])
    # These guards assert no ownership: this function is strictly read-only.
    readonly=SimpleNamespace(check=lambda:None)
    lineage=sec_document_lineage.inspect(query,cik,accession,name,readonly,lambda:None)
    step('lineage')
    payload=sec_document_payload.load(query,cik,accession,name,lineage['document']['content_sha256'])
    step('fetch')
    extracted=sec_fundamental_context.extract(payload,lineage['document']['content_sha256'],cik)
    step('parse')
    tags=tuple(sorted({tag for metric in facts.catalog() for tag in metric.tags}))
    bulk=sec_fundamental_reconciliation.load_bulk(query,cik,accession,tags);step('bulk')
    compared=sec_fundamental_reconciliation.compare_original(extracted,bulk);step('compare')
    precision=sec_precision.report(extracted);step('precision')
    if sec_document_lineage.inspect(query,cik,accession,name,readonly,lambda:None)!=lineage:
        raise ValueError('Original document changed during profile')
    if sec_fundamental_reconciliation.load_bulk(query,cik,accession,tags)!=bulk:
        raise ValueError('Bulk facts changed during profile')
    step('stability')
    result=dict(document=row,lineage=lineage,extraction=extracted,comparison=compared,timings=timing,
        seconds=time.perf_counter()-start,public_time_qualified=False,certified=False,
        counts=dict(original=len(extracted['observations']),rejected=len(extracted['rejected']),bulk=len(bulk)),
        unmatched_original=len(compared['unmatched_original_positions']),
        precision_gates={mode:dict(ready=gate['ready'],failures=gate['failures']) for mode,gate in precision['gates'].items()},
        comparison_statuses=dict(Counter(r['status'] for r in compared['comparisons'])))
    return dict(result,result_hash=digest(result))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--population',required=True);p.add_argument('--run-dir',required=True)
    p.add_argument('--endpoint',default='http://192.168.1.218:18123')
    p.add_argument('--start',type=date.fromisoformat,default=date(2019,1,1))
    p.add_argument('--end',type=date.fromisoformat,default=date(2026,10,2))
    p.add_argument('--ciks',nargs='+',default=['0000320193','0000789019','0000001750','0000001800','0001089113','0000312069'])
    a=p.parse_args();root=Path(a.run_dir).resolve();runtime=Path('D:/TradingML/runtimes').resolve()
    if not runtime.is_dir() or not root.is_relative_to(runtime):raise ValueError('Required runtime root unavailable')
    if len(set(a.ciks))!=len(a.ciks) or not 1<=len(a.ciks)<=12:raise ValueError('Use 1..12 unique pilot CIKs')
    manifest=json.loads(Path(a.population).read_text(encoding='utf-8-sig'));ciks=manifest['families']['sec_bulk']
    require_eligible('issuer_cik',ciks)
    if set(a.ciks)-set(ciks):raise ValueError('Pilot issuer is outside the reviewed eligible population')
    root.mkdir(parents=True,exist_ok=True)
    load_env_files(discover_clickhouse_env_files(),verbose=False);client=Client(a.endpoint)
    start=time.monotonic()
    request=dict(population_hash=digest(manifest),start=str(a.start),end=str(a.end),ciks=a.ciks)
    if (root/'request.json').exists() and json.loads((root/'request.json').read_text())!=request:raise ValueError('Profile request drift')
    save(root/'request.json',request)
    state=dict(state='running',mode='qualification profile',counts=dict(published=0,unchanged=0,audited=0,failed=0),
        total=len(a.ciks),active={},queued=len(a.ciks),elapsed=0,error=None,blocked=0)
    def status():
        state.update(updated_at=datetime.now(timezone.utc).isoformat(),elapsed=time.monotonic()-start)
        save(root/'status.json',state)
    try:
        ids=','.join(map(literal,ciks));dates=f'source_archive_date BETWEEN toDate({literal(a.start)}) AND toDate({literal(a.end)})'
        census=client.query('SELECT source_archive_date,document_type,count() documents,uniqExact(cik) issuers '
            f'FROM q_live.sec_filing_document_v3 FINAL WHERE cik IN ({ids}) AND {dates} '
            "AND document_type IN ('10-K','10-K/A','10-Q','10-Q/A','20-F','20-F/A','40-F','40-F/A') "
            'GROUP BY source_archive_date,document_type ORDER BY source_archive_date,document_type')
        save(root/'census.json',dict(rows=census,documents=sum(int(x['documents']) for x in census),
             archive_days=len({x['source_archive_date'] for x in census}),database_writes=0,public_qualified=False))
        outcomes=[]
        for cik in a.ciks:
            if (root/'STOP').exists():state['state']='stopped';break
            state['queued']-=1;state['active']={cik:dict(family=cik,stage='original document and bulk parity',started=time.monotonic()-start)};status()
            rows=client.query('SELECT cik,accession_number accession,document_name document,source_archive_date '
                f'FROM q_live.sec_filing_document_v3 FINAL WHERE cik={literal(cik)} AND {dates} '
                "AND document_type IN ('10-K','20-F','40-F') ORDER BY source_archive_date DESC LIMIT 1")
            try:
                if not rows:raise ValueError('No retained annual filing in requested scope')
                result=inspect_document(client.query,rows[0]);save(root/(cik+'.json'),result)
                outcome=dict(cik=cik,seconds=result['seconds'],timings=result['timings'],counts=result['counts'],
                    comparison_statuses=result['comparison_statuses'],all_bulk_matched=result['comparison']['all_bulk_matched'],
                    unmatched_original=result['unmatched_original'],precision_gates=result['precision_gates'])
                if result['unmatched_original'] or result['counts']['rejected'] or not result['comparison']['all_bulk_matched']:
                    state['blocked']+=1
                state['counts']['audited']+=1
            except Exception as error:
                outcome=dict(cik=cik,error=str(error));state['counts']['failed']+=1;state['error']=f'{cik}: {error}'
            outcomes.append(outcome);state['active']={};status()
        if state['state']=='running':state['state']='blocked' if state['counts']['failed'] or state['blocked'] else 'complete'
        report=dict(outcomes=outcomes,seconds=time.monotonic()-start,database_writes=0,
                    public_time_qualified=False,full_run_authorized_by_profile=False)
        save(root/'profile.json',report);status();print(json.dumps(report,indent=2))
        return 1 if state['counts']['failed'] else 0
    finally:client.close()


if __name__=='__main__':raise SystemExit(main())
