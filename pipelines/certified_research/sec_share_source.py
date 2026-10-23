"""Compose one retained document into normalized candidate source records."""
from . import sec_document_lineage,sec_share_context,sec_document_payload
from .contracts import digest

VERSION='normalized-sec-share-source-candidate-v1'


def prepare(query,cik,accession,document_name,claim,source_guard,*,maximum_bytes=16*1024**2,maximum_facts=10000):
    """Fetch one bounded payload; no persistence or historical eligibility guess.

    Contexts are factored out of observations. Rejections retain their raw
    context identifiers even when no valid context could be extracted. All
    records remain source candidates, never certified float/supply authorities.
    """
    if (type(maximum_bytes) is not int or not 1<=maximum_bytes<=16*1024**2
            or type(maximum_facts) is not int or not 1<=maximum_facts<=10000):
        raise ValueError('Invalid bounded SEC share preparation')
    def guard():claim.check();source_guard();claim.check()
    guard()
    before=sec_document_lineage.inspect(query,cik,accession,document_name,claim,source_guard)
    original=before['document'];text_hash=original['content_sha256']
    payload=sec_document_payload.load(query,cik,accession,document_name,text_hash,maximum_bytes=maximum_bytes,guard=guard)
    source_bytes=len(payload.encode('utf-8'))
    extracted=sec_share_context.extract(payload,text_hash,cik,
        maximum_bytes=maximum_bytes,maximum_facts=maximum_facts)
    del payload
    guard();contexts={};observations=[]
    document_key=digest(dict(version=VERSION,candidate_hash=before['candidate_hash'],parser=extracted['version']))
    for row in extracted['observations']:
        context=dict(document_key=document_key,context_id=row['context_id'],period_end=row['period_end'],
            dimensions=row['dimensions'])
        key=digest(context)
        existing=contexts.get(row['context_id'])
        if existing is not None and existing!=dict(context,context_key=key):
            raise ValueError('One source context has competing normalized meanings')
        contexts[row['context_id']]=dict(context,context_key=key)
        observations.append(dict(document_key=document_key,context_key=key,extraction_status='observed',
            reason='',rejected_context_id=None,
            **{k:v for k,v in row.items() if k not in ('source_text_hash','dimensions','period_end','context_id')}))
    for row in extracted['rejected']:
        observations.append(dict(document_key=document_key,context_key=None,extraction_status='rejected',
            value=None,rejected_context_id=row['context_id'],
            **{k:v for k,v in row.items() if k not in ('source_text_hash','context_id')}))
    observations.sort(key=lambda row:row['source_position'])
    if len({row['source_position'] for row in observations})!=len(observations):
        raise ValueError('Competing source positions in share extraction')
    after=sec_document_lineage.inspect(query,cik,accession,document_name,claim,source_guard)
    guard()
    if after!=before:raise ValueError('SEC share document lineage changed during preparation')
    source=dict(document_key=document_key,cik=str(int(cik)),lineage=before,parser_version=extracted['version'],
        source_text_hash=text_hash,source_bytes=source_bytes,parsing_prefix_bytes=extracted['parsing_prefix_bytes'],
        parsing_suffix_bytes=extracted['parsing_suffix_bytes'],sec_xbrl_envelope=extracted['sec_xbrl_envelope'])
    result=dict(version=VERSION,document=source,contexts=tuple(contexts[key] for key in sorted(contexts)),
        observations=tuple(observations),candidate_only=True,original_vintage_qualified=False,allocation_certified=False)
    return dict(result,prepared_hash=digest(result))
