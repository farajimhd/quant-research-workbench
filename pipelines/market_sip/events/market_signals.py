"""Simple estimated 1s LULD geometry and source-observed halt transitions.

Bands use completed 1s close means, not official SIP bands. The QMD Tier-2
width scenario is explicit. Halt state starts unknown, never inferred from
silence or a lack of trades. No news-risk channel is produced.
"""
from pathlib import Path
from . import market_day_sql as market
from .quote_bars import verify_storage,scope,source_sql
from pipelines.certified_research.contracts import digest

L=market.literal
VERSION='market-estimated-bands-and-observed-halts-v1'
TABLES=('estimated_luld_1s_v1','market_halt_transition_v1','market_signals_coverage_v1')


def ddl(db='arte'):
    market.identifier(db)
    common='build_id String,session_date Date,ticker LowCardinality(String),attempt_id UUID'
    settings=" SETTINGS storage_policy='live_market_ssd',index_granularity=1024"
    return (
        f"CREATE TABLE IF NOT EXISTS {db}.estimated_luld_1s_v1 ({common},bucket_index UInt32,available_at_us UInt64,reference_price Float64,lower Float64,upper Float64,valid UInt8,model LowCardinality(String)) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date) ORDER BY (build_id,session_date,ticker,bucket_index,attempt_id)"+settings,
        f"CREATE TABLE IF NOT EXISTS {db}.market_halt_transition_v1 ({common},available_at_us UInt64,ordinal UInt64,halted UInt8,status_valid UInt8,reason_tokens Array(UInt16),halt_started_count UInt8,resume_count UInt8) ENGINE=MergeTree PARTITION BY toYYYYMM(session_date) ORDER BY (build_id,session_date,ticker,available_at_us,ordinal,attempt_id)"+settings,
        f"CREATE TABLE IF NOT EXISTS {db}.market_signals_coverage_v1 ({common},parent_build_id String,parent_trade_attempt UUID,parent_source_hash String,dictionary_hash String,band_rows UInt64,halt_rows UInt64,content_hash String,certified_at DateTime64(6,'UTC')) ENGINE=ReplacingMergeTree PARTITION BY toYYYYMM(session_date) ORDER BY (build_id,session_date,ticker)"+settings,
        f"CREATE VIEW IF NOT EXISTS {db}.estimated_luld_current_v1 AS SELECT b.* FROM {db}.estimated_luld_1s_v1 b INNER JOIN (SELECT build_id,session_date,ticker,attempt_id FROM {db}.market_signals_coverage_v1 FINAL) c USING(build_id,session_date,ticker,attempt_id)",
        f"CREATE VIEW IF NOT EXISTS {db}.market_halt_current_v1 AS SELECT b.* FROM {db}.market_halt_transition_v1 b INNER JOIN (SELECT build_id,session_date,ticker,attempt_id FROM {db}.market_signals_coverage_v1 FINAL) c USING(build_id,session_date,ticker,attempt_id)",
    )


def halt_projection_sql(db,build,day,ticker,start_us,end_us):
    """Read a bounded dense 1s view without persisting redundant state rows."""
    market.identifier(db)
    if type(start_us)!=int or type(end_us)!=int or start_us<=0 or not 0<end_us-start_us<=86400*1000000 or (end_us-start_us)%1000000:
        raise ValueError('Expected a bounded whole-second status range')
    return f"""WITH changes AS (SELECT * FROM {db}.market_halt_current_v1 WHERE {scope(build,day,ticker)}),
      counts AS (SELECT ticker,intDiv(available_at_us,1000000) second,sum(halt_started_count) starts,sum(resume_count) resumes FROM changes GROUP BY ticker,second)
      SELECT g.available_at_us,h.halted,h.status_valid,h.reason_tokens,
      c.starts halt_started_count,c.resumes resume_count
      FROM (SELECT toUInt64({start_us})+(number+1)*1000000 available_at_us,{L(ticker)} ticker FROM numbers({(end_us-start_us)//1000000}) ORDER BY ticker,available_at_us) g
      ASOF LEFT JOIN (SELECT * FROM changes ORDER BY ticker,available_at_us,ordinal) h
      ON g.ticker=h.ticker AND g.available_at_us>=h.available_at_us
      LEFT JOIN counts c ON g.ticker=c.ticker AND intDiv(toUInt64(g.available_at_us-1),1000000)=c.second"""


def token_sets(rows):
    halt=[];resume=[]
    for r in rows:
        family=r['source_family'];modifier=int(r['modifier_int']);text=r['condition']
        if ((family=='halt_reason' and text.startswith('Halt:')) or
            (family=='cta_security_status' and modifier in (102,117)) or
            (family=='quote_conditions' and modifier==43) or
            (family=='luld_indicators' and modifier==17)):
            halt.append(int(r['token_id']))
        if ((family=='cta_security_status' and modifier in (100,103,116)) or
            (family=='quote_conditions' and modifier==16) or
            (family=='halt_reason' and text.startswith('Resume:'))):
            resume.append(int(r['token_id']))
    if not halt or not resume:raise ValueError('Halt/resumption token reference missing')
    return sorted(halt),sorted(resume)


def bands_sql(db,build,parent,day,ticker,attempt,trade_attempt,open_us,close_us):
    # Sparse completed bars; RANGE is elapsed seconds, not 300 observed rows.
    begin=f'{market.bounds(day)}+toUInt64(bucket_index)*1000000'
    return f"""INSERT INTO {db}.estimated_luld_1s_v1
      WITH prices AS (SELECT bucket_index,close_int/10000. price FROM {db}.bars_v1
        WHERE {scope(parent,day,ticker,trade_attempt)} AND resolution_ms=1000
          AND price_valid=1 AND trade_count>0 AND close_int>0
          AND {begin}>={int(open_us)} AND {begin}+1000000<={int(close_us)}),
      rolling AS (SELECT *,avg(price) OVER(ORDER BY bucket_index RANGE BETWEEN 299 PRECEDING AND CURRENT ROW) reference,
        first_value(price) OVER(ORDER BY bucket_index ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) anchor FROM prices),
      widths AS (SELECT *,multiIf(anchor>3,reference*.10,anchor>=.75,reference*.20,least(.15,reference*.75))*
        if(anchor<=3 AND {begin}>={int(close_us)-25*60*1000000},2.,1.) width FROM rolling)
      SELECT {L(build)},toDate({L(day)}),{L(ticker)},toUUID({L(attempt)}),bucket_index,
        {begin}+1000000,reference,greatest(.0001,round(reference-width,if(reference>=1,2,4))),round(reference+width,if(reference>=1,2,4)),
        toUInt8(1),'estimated-1s-close-mean-300s-qmd-tier2-width-v1' FROM widths"""


def halt_sql(db,build,day,ticker,attempt,tokens,*,source=None):
    halt,resume=token_sets(tokens);raw=source or source_sql(day,ticker)
    return f"""INSERT INTO {db}.market_halt_transition_v1
      WITH candidates AS (SELECT sip_timestamp_us at_us,ordinal,
        arrayFilter(t->has({halt+resume},toUInt16(t)),[condition_token_1,condition_token_2,condition_token_3,condition_token_4,condition_token_5]) reasons,
        hasAny(reasons,{halt}) h,hasAny(reasons,{resume}) r,
        multiIf(h AND r,toUInt8(2),h,toUInt8(1),toUInt8(0)) state
        FROM ({raw}) WHERE sip_timestamp_us>={market.bounds(day,'04:00:00')}
          AND sip_timestamp_us<{market.bounds(day,'20:00:00')} AND notEmpty(reasons)),
      initial AS (SELECT toUInt64({market.bounds(day,'04:00:00')}-1) at_us,toUInt64(0) ordinal,
        CAST([] AS Array(UInt16)) reasons,toUInt8(2) state),
      ordered AS (SELECT *,row_number() OVER w rn,lagInFrame(state,1,toUInt8(2)) OVER w previous,
        lagInFrame(reasons,1,CAST([] AS Array(UInt16))) OVER w previous_reasons
        FROM (SELECT at_us,ordinal,arrayMap(t->toUInt16(t),reasons) reasons,state FROM candidates
              UNION ALL SELECT * FROM initial)
        WINDOW w AS(ORDER BY at_us,ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
      SELECT {L(build)},toDate({L(day)}),{L(ticker)},toUUID({L(attempt)}),at_us,ordinal,
        toUInt8(state=1),toUInt8(state!=2),reasons,
        toUInt8(state=1 AND previous=0),toUInt8(state=0 AND previous=1)
        FROM ordered WHERE rn=1 OR state!=previous OR reasons!=previous_reasons"""


def evidence(client,db,build,day,ticker,attempt):
    results=[]
    for table,bad in [('estimated_luld_1s_v1','valid!=1 OR lower<=0 OR upper<=lower OR NOT isFinite(reference_price)'),
                       ('market_halt_transition_v1','halted>1 OR status_valid>1')]:
        keys='bucket_index' if table=='estimated_luld_1s_v1' else 'tuple(available_at_us,ordinal)'
        r=client.query(f"SELECT count() n,uniqExact({keys}) keys,countIf({bad}) bad,toString(tuple(sum(cityHash64(tuple(*))),sum(sipHash64(tuple(*))))) hash FROM {db}.{table} WHERE {scope(build,day,ticker,attempt)}",'signal_readback')[0]
        if r['n']!=r['keys'] or r['bad']:raise ValueError('Signal key/value integrity differs')
        results.append(r)
    return results


def publish(client,db,parent,day,ticker,trade_attempt,source_hash,tokens,open_us,close_us):
    import uuid
    dictionary=digest(tokens);build=digest((VERSION,Path(__file__).read_text(encoding='utf-8'),parent,dictionary))
    prior=client.query(f"SELECT * FROM {db}.market_signals_coverage_v1 FINAL WHERE {scope(build,day,ticker)}",'signal_receipt')
    if prior:
        if len(prior)!=1 or prior[0]['parent_source_hash']!=source_hash or prior[0]['parent_trade_attempt']!=trade_attempt:
            raise ValueError('Completed signal parent changed')
        r=prior[0];out=evidence(client,db,build,day,ticker,r['attempt_id'])
        if [x['n'] for x in out]!=[r['band_rows'],r['halt_rows']] or digest(out)!=r['content_hash']:raise ValueError('Signal reuse differs')
        return dict(state='reused',band_rows=r['band_rows'],halt_rows=r['halt_rows'])
    attempt=str(uuid.uuid4())
    client.query(bands_sql(db,build,parent,day,ticker,attempt,trade_attempt,open_us,close_us),'estimated_1s_bands',False)
    client.query(halt_sql(db,build,day,ticker,attempt,tokens),'observed_halt_transitions',False)
    out=evidence(client,db,build,day,ticker,attempt)
    expected=client.query(f"SELECT count() n FROM {db}.bars_v1 WHERE {scope(parent,day,ticker,trade_attempt)} AND resolution_ms=1000 AND price_valid=1 AND trade_count>0 AND close_int>0 AND {market.bounds(day)}+toUInt64(bucket_index)*1000000>={int(open_us)} AND {market.bounds(day)}+(toUInt64(bucket_index)+1)*1000000<={int(close_us)}",'band_coverage_check')[0]['n']
    if out[0]['n']!=expected or not out[1]['n']:raise ValueError('Signal coverage differs')
    verify_storage(client.query,db,TABLES)
    fingerprint=digest(out)
    client.query(f"INSERT INTO {db}.market_signals_coverage_v1 VALUES ({L(build)},toDate({L(day)}),{L(ticker)},toUUID({L(attempt)}),{L(parent)},toUUID({L(trade_attempt)}),{L(source_hash)},{L(dictionary)},{out[0]['n']},{out[1]['n']},{L(fingerprint)},now64(6))",'signal_commit',False)
    if client.query(f"SELECT content_hash FROM {db}.market_signals_coverage_v1 FINAL WHERE {scope(build,day,ticker)}",'signal_commit_readback')!=[dict(content_hash=fingerprint)]:raise ValueError('Signal commit readback differs')
    return dict(state='published',band_rows=out[0]['n'],halt_rows=out[1]['n'])
