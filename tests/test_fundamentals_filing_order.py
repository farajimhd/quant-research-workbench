from pipelines.fundamentals.filing_order import sql
from tests.test_fundamentals_retained import live

def observations(extra=''):
    return """SELECT '0000320193' cik,'assets' feature,'sec_bulk' family,
        toDateTime64('2026-03-03 05:00:00',9,'UTC') available_at,2 authority_priority,
        arrayJoin(['0000320193-26-000001','0000320193-26-000002']"""+extra+""") accession"""

def test_live_ordering_preserves_clock_and_missing_metadata_ties(live):
    q,db,_,_=live
    q(f"""CREATE TABLE {db}.sec_bulk_mirror_filing_v3 (cik String,accession_number String,
        accepted_at_utc Nullable(DateTime64(9,'UTC')),acceptance_datetime_raw String,
        filing_date Date,accepted_at_source String,source_file_id String)
        ENGINE=ReplacingMergeTree ORDER BY (cik,accession_number,source_file_id)
        SETTINGS storage_policy='live_market_ssd'""",read=False)
    q(f"""CREATE TABLE {db}.sec_filing_v3 (cik String,accession_number String,
        filing_date Date,acceptance_datetime_raw String,source_content_sha256 String)
        ENGINE=ReplacingMergeTree ORDER BY (cik,accession_number,source_content_sha256)
        SETTINGS storage_policy='live_market_ssd'""",read=False)
    def put(acc,clock,source='a',raw=None):
        q(f"INSERT INTO {db}.sec_bulk_mirror_filing_v3 VALUES ('0000320193','{acc}',"
          f"'2026-03-02 {clock}','{raw or '2026-03-02T'+clock+'Z'}','2026-03-02','submissions_bulk','{source*64}')",read=False)
    put('0000320193-26-000001','11:00:00');put('0000320193-26-000002','16:00:00')
    def read(obs):
        return q('SELECT accession,filing_order,toString(available_at) clock FROM ('+
            sql(obs,['0000320193'],'cik,feature',metadata_database=db,reported_metadata_database=db)+') ORDER BY accession')
    rows=read(observations());assert rows[1]['filing_order']>rows[0]['filing_order']>0
    assert {r['clock'] for r in rows}=={'2026-03-03 05:00:00.000000000'}
    qualified=read(observations().replace("'sec_bulk' family","'sec_original_qualified' family"))
    assert [r['filing_order'] for r in qualified]==[r['filing_order'] for r in rows]
    later=read(observations().replace('2026-03-03 05:00:00','2026-06-12 05:00:00'))
    assert [r['filing_order'] for r in later]==[r['filing_order'] for r in rows]
    assert {r['clock'] for r in later}=={'2026-06-12 05:00:00.000000000'}
    unknown=observations().replace("'0000320193-26-000002']","'0000320193-26-000002','0000320193-26-000003']")
    assert {r['filing_order'] for r in read(unknown)}=={0}
    q(f"INSERT INTO {db}.sec_filing_v3 VALUES ('0000320193','0000320193-26-000003','2026-03-03','2026-03-02T18:00:00.000Z','{'c'*64}')",read=False)
    recovered=read(unknown)
    assert recovered[2]['filing_order']>recovered[1]['filing_order']>recovered[0]['filing_order']>0
    # Distinct retained sources disagree about one acceptance; preserve all ties.
    put('0000320193-26-000001','17:00:00','b')
    assert {r['filing_order'] for r in read(observations())}=={0}


def test_live_pinned_header_clock_requires_all_source_hashes(live,monkeypatch):
    from pipelines.fundamentals import filing_order_witnesses as witnesses
    q,_,_,_=live
    rows=q('SELECT accession_number,toString(accepted_at_utc) ordering_clock FROM ('+
        witnesses.source_sql([witnesses.CIK])+') ORDER BY accession_number')
    assert len(rows)==3 and all(r['ordering_clock'] for r in rows)
    assert rows[0]['ordering_clock']<rows[1]['ordering_clock']<rows[2]['ordering_clock']
    wrong=list(witnesses.WITNESSES);a,raw,_=wrong[0];wrong[0]=(a,raw,'0'*64)
    monkeypatch.setattr(witnesses,'WITNESSES',tuple(wrong))
    assert q(witnesses.source_sql([witnesses.CIK]))==[]
