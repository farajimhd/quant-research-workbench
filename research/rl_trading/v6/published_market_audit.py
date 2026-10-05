"""Read-only adapter for the fully audited, published phase-1b dataset."""
import polars as pl
from research.rl_trading.v1.common import digest
from research.rl_trading.v6 import saved_label_audit as source
from research.rl_trading.v6.market_teacher_dataset import VERSION

def published():
    active=source.read_json(source.runtime()/'rl-v6-active-market-teacher.json')
    data=source.read_json(source.mapped(active['dataset']),active['sha256'])
    audit=source.read_json(source.mapped(data['publication_audit']),data['publication_audit_sha256'])
    original,one=source.published()
    source_identity=original['sha256']
    if one.get('development_admission'):
        from research.rl_trading.v6.dataset_admission import verify_market
        verified=verify_market(one,source.runtime(),source.mapped)
        if data!=verified:raise ValueError('UI and training 1b authorities differ')
        source_identity=digest({e['day']:e['teacher_sha256'] for e in [one['context']]+one['days']})
    if (active['version']!=VERSION or data['version']!=VERSION or data['status']!='audited_1b_labels'
        or data['hash']!=digest({k:v for k,v in data.items() if k!='hash'}) or audit['status']!='passed'
        or audit['binding']!=data['binding'] or data['binding']['source_sha256']!=source_identity
        or data['sealed_test_accessed'] is not False or data['rows']!=audit['rows']
        or [(e['day'],e['role']) for e in data['days']]!=[(e['day'],e['role']) for e in [one['context']]+one['days']]
        or audit['day_certificates']!={e['day']:e['sha256'] for e in data['days']}):
        raise ValueError('Published 1b labels differ from their source or audit')
    return active,data

def session(day):
    active,data=published();entry=next((e for e in data['days'] if e['day']==day),None)
    if entry is None:raise ValueError('Session outside approved 1b dataset')
    root=source.mapped(entry['root']);proof=source.read_json(root/'complete.json',entry['sha256'])
    if proof['binding']!=entry.get('producer_binding',data['binding']) or proof['day']!=day or proof['role']!=entry['role']:
        raise ValueError('1b session binding changed')
    return active,data,entry,root,proof

def open_session(day):
    from research.rl_trading.v6 import market_teacher_preview as preview
    active,data,entry,root,proof=session(day)
    job=digest(dict(published_1b=active['sha256'],day=day,adapter='v1'))
    preview.ROOT.mkdir(parents=True,exist_ok=True)
    with preview.LOCK:
        if not (preview.ROOT/(job+'.json')).exists():
            frame=pl.read_parquet(source.verified_local(root/'decisions.parquet',proof['decisions_sha256']))
            names={r['listing_id']:r['ticker'] for r in source.listings(day)['listings']}
            frame=frame.with_columns(pl.col('listing_id').replace_strict(names).alias('ticker'),pl.col('allocation_ratio').fill_null(0.))
            frame.write_parquet(preview.ROOT/(job+'.parquet'))
            from research.rl_trading.v1.common import file_hash
            _,one,_,_,_,_=source.session(day)
            key=digest(dict(dataset=data['binding']['source_sha256'],day=day,certificate=one['teacher_sha256'],projection='positive-gain-rows-v3'))
            meta=dict(version=VERSION,day=day,dataset_sha256=active['sha256'],source_dataset_sha256=data['binding']['source_sha256'],
                published_1b=True,config=data['binding']['config'],groups=proof['groups'],source_rows=proof['rows'],
                pairs=frame.height,selected=proof['selected'],rejected=frame.height-proof['selected'],suppressed_rows=proof['suppressed_rows'],
                decisions_path=str(preview.ROOT/(job+'.parquet')),decisions_sha256=file_hash(preview.ROOT/(job+'.parquet')),
                source_input_key=key,status='published_final_labels',certificate_sha256=entry['sha256'])
            preview.write_json(preview.ROOT/(job+'.json'),meta)
        preview.JOBS[job]=dict(job_id=job,status='complete',stage='Published final 1b labels',completed=1,total=1)
    return dict(preview.JOBS[job])

def ensure_candidates(meta):
    """Cache original positive 1a rows for the explanatory table, without selection."""
    from research.rl_trading.v6 import market_teacher_preview as preview
    from research.rl_trading.v1.common import file_hash
    cache=preview.ROOT/meta['source_input_key'];cache.mkdir(exist_ok=True)
    with source.LOCK:
        if (cache/'complete.json').exists():return
        _,_,proof,root,_,_=source.session(meta['day']);chunks=[];pairs=[];counts={}
        for item in proof['shards']:
            folder=root/item['path'];receipt=source.read_json(folder/'complete.json',item['sha256'])
            frame=pl.scan_parquet(source.verified_local(folder/'labels.parquet',receipt['files']['labels']['sha256']))
            for action,count in frame.group_by('action').len().collect().iter_rows():
                counts[action]=counts.get(action,0)+count
            chunks.append(frame.filter(pl.col('entry_gain')>0).select('listing_id','pair_id','time_us','close','entry_gain','entry_target_us','action').collect())
            pairs.append(pl.read_parquet(source.verified_local(folder/'pairs.parquet',receipt['files']['pairs']['sha256'])))
        if sum(counts.values())!=proof['valid_rows']:raise ValueError('Candidate source coverage mismatch')
        pl.concat(chunks).write_parquet(cache/'candidates.parquet');pl.concat(pairs).write_parquet(cache/'pairs.parquet')
        preview.write_json(cache/'complete.json',dict(candidates_sha256=file_hash(cache/'candidates.parquet'),pairs_sha256=file_hash(cache/'pairs.parquet'),source_rows=proof['valid_rows'],counts=counts))

def chart(meta,listing_id,start_us):
    active,_,entry,root,proof=session(meta['day'])
    if active['sha256']!=meta['dataset_sha256'] or entry['sha256']!=meta['certificate_sha256']:
        raise ValueError('1b publication changed; reload labels')
    for item in proof['shards']:
        receipt=source.read_json(root/item['path']/'complete.json',item['sha256'])
        if listing_id in receipt['identities']:
            if receipt['binding']['source_sha256']!=meta['source_dataset_sha256']:raise ValueError('1b shard source changed')
            frame=pl.read_parquet(source.verified_local(root/item['path']/'labels.parquet',receipt['files']['labels']['sha256'])).filter(pl.col('listing_id')==listing_id)
            break
    else:raise ValueError('Listing missing from published 1b shards')
    payload=source.chart(meta['day'],listing_id,start_us,900,'combined',0)
    clocks=[r['time_us'] for r in payload['labels'] if r['action']!='CONTEXT']
    saved={r['time_us']:r for r in frame.filter(pl.col('time_us').is_in(clocks)).to_dicts()}
    for row in payload['labels']:
        if row['action']=='CONTEXT':
            row.update(action_1a='CONTEXT',allocation_ratio=0.,allocation_loss_mask=False,group_id=None);continue
        final=saved.get(row['time_us'])
        if final is None:raise ValueError('Price candle missing final 1b label')
        row.update(final)
    payload.update(dataset_sha256=active['sha256'],label_source='published_1b_shard',certificate_sha256=entry['sha256'])
    return payload
