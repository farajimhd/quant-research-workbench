"""Execution-only batching. Calculation modules and existing receipt IDs stay unchanged."""
from pathlib import Path
from . import quote_bars as quotes, market_signals as signals
from pipelines.certified_research.contracts import digest


def batches(rows, max_units=8, max_events=2_000_000):
    """Bound one scan's units and input size; dense symbols run alone."""
    batch=[];events=0
    for row in rows:
        size=int(row['event_count'])
        if batch and (len(batch)>=max_units or events+size>max_events):
            yield batch;batch=[];events=0
        batch.append(row);events+=size
    if batch:yield batch


def stage_sql(db, stage, day, tickers, *, source=None):
    if not tickers or len(set(tickers))!=len(tickers):raise ValueError('Distinct nonempty batch required')
    raw=source or quotes.source_sql(day,tickers[0]).replace(
        'PREWHERE ticker='+quotes.L(tickers[0]),
        'PREWHERE ticker IN ('+','.join(map(quotes.L,tickers))+')')
    # Delegate every sufficient statistic to the unchanged calculation recipe.
    query=quotes.stage_sql(db,stage,day,tickers[0],source=raw)
    return query.replace('ORDER BY bucket_index','ORDER BY (ticker,bucket_index)').replace(
        'SELECT toUInt32(intDiv(sip_timestamp_us-', 'SELECT ticker,toUInt32(intDiv(sip_timestamp_us-').replace(
        'FROM decoded GROUP BY bucket_index','FROM decoded GROUP BY ticker,bucket_index')


def insert_batch(client,db,stage,build,day,attempts,*,guard=lambda:None):
    """Same per-symbol sequential funnel, four INSERTs per bounded batch."""
    if not attempts:raise ValueError('Nonempty attempts required')
    prefix=f'INSERT INTO {db}.quote_bars_v1 '
    selects=[quotes.base_insert(db,stage,build,day,t,a).removeprefix(prefix)+f' AND ticker={quotes.L(t)}' for t,a in attempts.items()]
    guard();client.query(prefix+' UNION ALL '.join(selects),'quote_batch_1s',False)
    for parent,target in zip(quotes.FRAMES,quotes.FRAMES[1:]):
        guard()
        selects=[quotes.rollup_select(db,build,day,t,a,parent,target) for t,a in attempts.items()]
        client.query(prefix+' UNION ALL '.join(selects),'quote_batch_rollup',False)


def signal_build(parent,tokens):
    dictionary=digest(tokens)
    return digest((signals.VERSION,Path(signals.__file__).read_text(encoding='utf-8'),parent,dictionary)),dictionary


def verify_completed(client,db,build,parent,day,rows,saved,certified,tokens,policy,*,guard=lambda:None,progress=lambda n:None):
    """Read every committed row/hash, grouped in bounded sets; never trust counts alone."""
    requested={r['ticker'] for r in rows};done=set();sid,dictionary=signal_build(parent,tokens)
    receipts=client.query(f"SELECT * FROM {db}.market_signals_coverage_v1 FINAL WHERE build_id={quotes.L(sid)} AND session_date=toDate({quotes.L(day)})",'signal_receipt_prefetch')
    by_symbol={r['ticker']:r for r in receipts}
    candidates=[]
    for ticker,r in saved.items():
        if ticker not in requested:continue
        guard();expected=certified[(day,ticker)]
        if r['source_hash']!=expected[0] or r['policy_hash']!=policy:raise ValueError('Published quote source/recipe differs')
        if ticker in by_symbol:
            s=by_symbol[ticker]
            if (s['parent_build_id'],s['parent_trade_attempt'],s['parent_source_hash'],s['dictionary_hash'])!=(parent,expected[3],expected[0],dictionary):
                raise ValueError('Completed signal parent changed')
            candidates.append(ticker)
    # Bound aggregate output and query memory, including dense symbols.
    for offset in range(0,len(candidates),16):
        guard();names=candidates[offset:offset+16]
        def predicate(identity,receipts):
            pairs=','.join('('+quotes.L(t)+',toUUID('+quotes.L(receipts[t]['attempt_id'])+'))' for t in names)
            return f"build_id={quotes.L(identity)} AND session_date=toDate({quotes.L(day)}) AND (ticker,attempt_id) IN ({pairs})"
        query=quotes.evidence(db,build,day,names[0],saved[names[0]]['attempt_id'])
        query=query.split(' WHERE ')[0].replace('SELECT count()','SELECT ticker,count()',1)
        actual=client.query(query+' WHERE '+predicate(build,saved)+' GROUP BY ticker,attempt_id','quote_batch_reuse_readback')
        results={r['ticker']:r for r in actual}
        collected={t:[] for t in names}
        for table,bad,keys in [('estimated_luld_1s_v1','valid!=1 OR lower<=0 OR upper<=lower OR NOT isFinite(reference_price)','bucket_index'),
                               ('market_halt_transition_v1','halted>1 OR status_valid>1','tuple(available_at_us,ordinal)')]:
            query=f"SELECT ticker,count() n,uniqExact({keys}) keys,countIf({bad}) bad,toString(tuple(sum(cityHash64(tuple(*))),sum(sipHash64(tuple(*))))) hash FROM {db}.{table} WHERE {predicate(sid,by_symbol)} GROUP BY ticker,attempt_id"
            output={r.pop('ticker'):r for r in client.query(query,'signal_batch_reuse_readback')}
            for t in names:
                r=output.get(t,dict(n=0,keys=0,bad=0,hash='(0,0)'))
                if r['n']!=r['keys'] or r['bad']:raise ValueError('Signal key/value integrity differs')
                collected[t].append(r)
        for t in names:
            r=saved[t];a=results.get(t)
            # Zero-row quote units have no GROUP BY row. Use the same aggregate's
            # empty-input hash rather than inventing a serialized hash spelling.
            if a is None:a=client.query(quotes.evidence(db,build,day,t,r['attempt_id']),'quote_empty_reuse_readback')[0]
            if (int(a['n']),a['hash'],int(a['bad']),int(a['unique_keys']))!=(int(r['row_count']),r['content_hash'],0,int(r['row_count'])):
                raise ValueError('Completed quote readback differs')
            s=by_symbol[t];out=collected[t]
            if [x['n'] for x in out]!=[s['band_rows'],s['halt_rows']] or digest(out)!=s['content_hash']:
                # Preserve exact empty-aggregate representation and independently
                # recheck mismatches; no waiver of the original evidence digest.
                out=signals.evidence(client,db,sid,day,t,s['attempt_id'])
                if [x['n'] for x in out]!=[s['band_rows'],s['halt_rows']] or digest(out)!=s['content_hash']:raise ValueError('Signal reuse differs')
            done.add(t)
        progress(len(done))
    return done

