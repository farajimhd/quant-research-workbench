"""Incremental document preparation shared by historical and forward runs."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
from . import fdic_extract
from pipelines.certified_research.contracts import digest

NONFINANCIAL = {
 '4eb8ac205a4a586d05a7a1a270cb1dca00f457d65ed15a5264f99ebcad3074b8':
    'Because no financial statements have been included in this Form 10-K/A',
 '5a193b25b25eeeff7afbae1d6b928fa32ead6369af178b3c68bd6be8809a7ec8':
    'Because no financial statements have been included in this Amendment',
}


def prepare(root,report):
    import fitz
    from scripts.run_fundamentals import save
    root=Path(root)
    from .fdic_reports import retained_root
    verified=retained_root(root/'verified',report)
    receipt=json.loads((verified/f"{report['filing_id']}-{report['attachment_id']}.json").read_text(encoding='utf-8'))
    if receipt['report']!=report:raise ValueError('FDIC receipt metadata changed')
    raw=(verified/(receipt['sha256']+'.pdf')).read_bytes()
    if sha256(raw).hexdigest()!=receipt['sha256'] or len(raw)!=report['bytes']:
        raise ValueError('FDIC retained PDF changed')
    cache=root/'prepared';cache.mkdir(exist_ok=True)
    recipe=digest([Path(fdic_extract.__file__).read_text(encoding='utf-8'),Path(__file__).read_text(encoding='utf-8'),fitz.VersionBind])
    target=cache/(digest([report,receipt['sha256'],recipe])+'.json')
    if target.exists():
        saved=json.loads(target.read_text(encoding='utf-8'))
        if digest(saved['extracted'])!=saved['hash']:raise ValueError('FDIC prepared cache changed')
        return saved['extracted'],receipt,True
    with fitz.open(stream=raw,filetype='pdf') as document:
        if receipt['sha256'] in NONFINANCIAL:
            text=' '.join(' '.join(document[i].get_text().split()) for i in range(min(5,len(document))))
            quote=NONFINANCIAL[receipt['sha256']]
            if quote not in text:raise ValueError('Reviewed nonfinancial amendment evidence changed')
            result=dict(report=report,sha256=receipt['sha256'],version=fdic_extract.VERSION,
                observations=[],issues=[],not_applicable_reason='Amendment explicitly contains no financial statements',evidence_quote=quote)
        else:
            cover=' '.join(document[0].get_text().split())
            match=re.search(r'quarterly period ended\s+([A-Za-z]+ \d{1,2},? \d{4})',cover,re.I)
            period=None if not match else datetime.strptime(match[1].replace(',',''),'%B %d %Y').date().isoformat()
            pages=[]
            for i,page in enumerate(document):
                words=[[round(v,2) for v in w[:4]]+[w[4]] for w in page.get_text('words')]
                if any('consolidated' in fdic_extract.clean(' '.join(w[4] for w in row)) for row in fdic_extract.lines(words)):
                    pages.append(dict(page=i+1,words=words,pdf_sha256=receipt['sha256'],cover_period=period))
            result=dict(report=report,sha256=receipt['sha256'],**fdic_extract.extract_document(pages))
            required={'assets','liabilities','equity_parent','eps_diluted','operating_cash_flow'}
            missing=required-{x['metric'] for x in result['observations']}
            if not {'net_income_parent','net_income_common','bank_net_income_consolidated'} & {x['metric'] for x in result['observations']}:
                missing.add('explicit_net_income_measure')
            if missing:result['issues'].append(dict(reason='Missing required bank statement fields',fields=sorted(missing)))
    save(target,dict(extracted=result,hash=digest(result)))
    return result,receipt,False

