"""Ingestion-owned REST recovery validation. Never writes canonical tables.

Preserves the immutable archive and refuses incomplete/ambiguous matching.
Downloaded source and staged evidence must use the workstation runtime root.
"""
import os
import sys
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.dont_write_bytecode=True
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import argparse
from collections import Counter,defaultdict
from datetime import date,datetime,timedelta,timezone
from hashlib import sha256
import json
import math
import re
import struct
import urllib.error
import urllib.parse
import urllib.request

from scripts.repair_qmd_live_canonical_bars import load_dotenv,connection_from_env
from scripts.audit_structural_baseline import write
from src.runtime_paths import WORKSTATION_RUNTIME_ROOT

VERSION='rest-clock-recovery-validator-2'


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def normalize_trade(record,tokens):
    """Mirror the certified direct-import price, Float32, tape and condition codec."""
    correction=max(0,min(15,int(record.get('correction',0))))
    if correction in (7,8,10,11):return None
    sip=int(record['sip_timestamp'])//1000
    sequence=int(record['sequence_number'])
    clock=int(record.get('participant_timestamp',0))//1000
    if not 0<sequence<=4294967295 or sip<=0 or clock<=0:
        raise ValueError('Missing/invalid exact timestamp or source sequence')
    # REST's integer size truncates fractional shares; decimal_size preserves
    # the quantity that the canonical flatfile import encoded as Float32.
    price=float(record['price']);size=float(record.get('decimal_size',record.get('size',0)))
    if not math.isfinite(price) or not math.isfinite(size):raise ValueError('Nonfinite trade')
    subcent=abs(price*100-round(price*100))>1e-7
    scale=int(price>0 and (price<1 or (subcent and price<=429496.7295)))
    encoded=round(price*(10000 if scale else 100))
    valid=price>0 and encoded>0 and not (price>429496.7295 and subcent)
    if valid and encoded>4294967295:raise ValueError('Price overflows canonical codec')
    if not valid:encoded=scale=0
    size=struct.unpack('<f',struct.pack('<f',max(0,size)))[0]
    tape=max(0,min(3,int(record.get('tape',0))-1))
    exchange=int(record.get('exchange',0))
    if not 0<=exchange<=255:raise ValueError('Exchange outside canonical codec')
    conditions=list(record.get('conditions',[]))[:5]
    conditions += [0]*(5-len(conditions))
    key=(sip,1+2*scale+8*tape,encoded,size,exchange,*(tokens.get(int(c),0) for c in conditions))
    return key,clock,sequence


def canonical_key(row):
    return (int(row['sip_timestamp_us']),int(row['event_meta']),int(row['price_primary_int']),
            struct.unpack('<f',struct.pack('<f',float(row['size_primary'])))[0],int(row['exchange_primary']),
            *(int(row[f'condition_token_{i}']) for i in range(1,6)))


def reconcile(canonical,vendor,tokens,verified_order_contract=False):
    groups=defaultdict(list);archive=defaultdict(list);excluded=0;seen=set();ordered_vendor=[]
    for record in vendor:
        normalized=normalize_trade(record,tokens)
        if normalized is None:excluded+=1;continue
        key,clock,sequence=normalized
        if sequence in seen:raise ValueError('Duplicate vendor source sequence')
        seen.add(sequence);groups[key].append(clock)
        ordered_vendor.append((key,clock,sequence))
    ordinals=[int(r['ordinal']) for r in canonical]
    if len(set(ordinals))!=len(ordinals):raise ValueError('Duplicate canonical ordinal')
    for row in canonical:archive[canonical_key(row)].append(row)
    missing=extra=ambiguous=0;matched=[]
    for key in archive.keys()|groups.keys():
        left,right=archive.get(key,[]),groups.get(key,[])
        missing+=max(0,len(left)-len(right));extra+=max(0,len(right)-len(left))
        if len(left)!=len(right):continue
        if len(set(right))>1:
            ambiguous+=len(left);continue
        for row in left:
            matched.append(dict(ordinal=int(row['ordinal']),sip_timestamp_us=key[0],execution_timestamp_us=right[0]))
    matched.sort(key=lambda r:r['ordinal'])
    resolved_by_order=0
    if verified_order_contract and not (missing or extra):
        left=sorted(canonical,key=lambda r:int(r['ordinal']))
        right=sorted(ordered_vendor,key=lambda r:(r[0][0],r[2]))
        # The immutable archive contract orders unioned events by SIP microsecond,
        # source sequence, event type. Removing quotes preserves trade order.
        # Prove the ENTIRE normalized trade sequence, not just duplicate groups.
        if [canonical_key(r) for r in left]!=[r[0] for r in right]:
            raise ValueError('Full canonical/source trade ordering mismatch')
        matched=[dict(ordinal=int(a['ordinal']),sip_timestamp_us=b[0][0],execution_timestamp_us=b[1]) for a,b in zip(left,right)]
        resolved_by_order=ambiguous;ambiguous=0
    report=dict(canonical_trades=len(canonical),vendor_records=len(vendor),excluded_corrections=excluded,
                matched=len(matched),missing=missing,extra=extra,ambiguous=ambiguous,
                resolved_by_verified_order=resolved_by_order,
                complete=bool(canonical) and not (missing or extra or ambiguous) and len(matched)==len(canonical))
    return matched,report


def fetch_pages(folder,ticker,day,key,max_records):
    begin=int(datetime.combine(day,datetime.min.time(),timezone.utc).timestamp()*1000000000)
    params={'timestamp.gte':str(begin),'timestamp.lt':str(begin+86400000000000),'order':'asc','sort':'timestamp','limit':'50000'}
    url=f'https://api.massive.com/v3/trades/{ticker}?'+urllib.parse.urlencode(params)
    records=[];pages=[];index=0
    while url:
        parsed=urllib.parse.urlsplit(url)
        if parsed.scheme!='https' or parsed.netloc not in ('api.massive.com','api.polygon.io'):
            raise ValueError('Untrusted vendor pagination host')
        path=folder/f'vendor-page-{index:04}.json'
        if path.exists():
            page=json.loads(path.read_text())
            if page['request_url']!=url or digest(page['results'])!=page['results_sha256']:
                raise ValueError('Vendor page cache provenance mismatch')
        else:
            request=urllib.request.Request(url,headers={'Authorization':'Bearer '+key})
            try:
                with urllib.request.urlopen(request,timeout=30) as response:data=json.load(response)
            except urllib.error.HTTPError as exc:
                raise RuntimeError(f'Vendor HTTP {exc.code}; no response body or credential logged') from None
            if data.get('status') not in ('OK','DELAYED'):raise ValueError('Vendor did not return an OK result')
            nxt=data.get('next_url')
            if nxt:
                part=urllib.parse.urlsplit(nxt)
                query=[(k,v) for k,v in urllib.parse.parse_qsl(part.query) if k.lower()!='apikey']
                nxt=urllib.parse.urlunsplit((part.scheme,part.netloc,part.path,urllib.parse.urlencode(query),part.fragment))
            page=dict(request_url=url,request_id=data.get('request_id'),results=data.get('results',[]),next_url=nxt)
            page['results_sha256']=digest(page['results']);write(path,page)
        records.extend(page['results']);pages.append(page['results_sha256'])
        if len(records)>max_records:raise ValueError('Vendor record cap exceeded; no partial certification')
        nxt=page['next_url']
        if nxt==url:raise ValueError('Pagination made no progress')
        url=nxt;index+=1
        if index>20:raise ValueError('Page cap exceeded')
        print(f'{ticker} {day}: source pages={index}, trades={len(records)}',flush=True)
    return records,pages


def main(args):
    if not re.fullmatch('[A-Z][A-Z0-9.-]{0,19}',args.ticker):raise ValueError('Invalid ticker')
    if not 0<args.max_records<=1000000:raise ValueError('Record cap must be between 1 and 1000000')
    day=date.fromisoformat(args.date)
    root=args.runtime.resolve()
    if not WORKSTATION_RUNTIME_ROOT.is_dir() or not root.is_relative_to(WORKSTATION_RUNTIME_ROOT.resolve()):
        raise ValueError('Recovery staging requires the available workstation runtime')
    root.mkdir(parents=True,exist_ok=True)
    folder=root/f'{args.ticker}-{day}';folder.mkdir(exist_ok=True)
    plan=dict(version=VERSION,ticker=args.ticker,day=str(day),max_records=args.max_records,
              source_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),mode='validation_only_no_database_writes')
    if (folder/'plan.json').exists() and json.loads((folder/'plan.json').read_text())!=plan:
        raise ValueError('Frozen plan changed; use a new runtime directory')
    write(folder/'plan.json',plan)
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    client=connection_from_env('market_sip_compact')
    bounds=client.json_rows(f"SELECT argMax(event_count,tuple(build_step,updated_at)) AS events,argMax(next_ordinal-event_count,tuple(build_step,updated_at)) AS begin,argMax(next_ordinal,tuple(build_step,updated_at)) AS end FROM events_ordinal_continuity WHERE ticker='{args.ticker}' AND source_date='{day}' FORMAT JSONEachRow",timeout=30)[0]
    if not bounds['events']:raise ValueError('No canonical day boundary')
    canonical=client.json_rows(f"SELECT * FROM events_{day.year} PREWHERE ticker='{args.ticker}' WHERE ordinal>={bounds['begin']} AND ordinal<{bounds['end']} AND bitAnd(event_meta,1)=1 ORDER BY ordinal LIMIT {args.max_records+1} SETTINGS max_threads=2,max_execution_time=25 FORMAT JSONEachRow",timeout=30)
    if len(canonical)>args.max_records:raise ValueError('Canonical record cap exceeded')
    reference=client.json_rows("SELECT modifier_int,min(token_id) AS token_id FROM event_condition_token_reference WHERE source_family='trade_conditions' AND is_join_canonical=1 GROUP BY modifier_int ORDER BY modifier_int FORMAT JSONEachRow",timeout=30)
    tokens={int(r['modifier_int']):int(r['token_id']) for r in reference}
    provenance=client.json_rows(f"SELECT DISTINCT source_filter_key FROM events_source_day_stats WHERE source_date='{day}' ORDER BY source_filter_key FORMAT JSONEachRow",timeout=30)
    expected_filter='flatfile_direct_events_v1|drop_trade_correction_codes=07,08,10,11|condition_slots=5'
    if not provenance or any(r['source_filter_key']!=expected_filter for r in provenance):
        raise ValueError('Canonical source filter/order provenance is not the supported direct-import contract')
    snapshot=dict(bounds=bounds,rows=canonical,reference=reference,provenance=provenance)
    if (folder/'canonical-snapshot.json').exists() and digest(json.loads((folder/'canonical-snapshot.json').read_text()))!=digest(snapshot):
        raise ValueError('Canonical source changed since frozen snapshot')
    write(folder/'canonical-snapshot.json',snapshot)
    api_key=os.getenv('MASSIVE_API_KEY') or os.getenv('POLYGON_API_KEY')
    if not api_key:raise ValueError('Existing vendor API credential unavailable')
    vendor,pages=fetch_pages(folder,args.ticker,day,api_key,args.max_records)
    matched,report=reconcile(canonical,vendor,tokens,verified_order_contract=True)
    report.update(canonical_sha256=digest(snapshot),vendor_page_sha256=pages,matched_sha256=digest(matched))
    controls=client.json_rows(f"SELECT ordinal,execution_timestamp_us FROM q_live.historical_event_execution_clock_v1 FINAL WHERE ticker='{args.ticker}' AND source_date='{day}' SETTINGS max_threads=2,max_execution_time=25 FORMAT JSONEachRow",timeout=30)
    report['control_rows']=len(controls)
    if controls:
        expected={int(r['ordinal']):int(r['execution_timestamp_us']) for r in controls}
        actual={r['ordinal']:r['execution_timestamp_us'] for r in matched}
        report['control_mismatches']=sum(expected.get(k)!=v for k,v in actual.items())+len(expected.keys()-actual.keys())
        report['complete']=report['complete'] and not report['control_mismatches']
    write(folder/'matched-staging.json',matched);write(folder/'validation.json',report)
    print(json.dumps(report),flush=True)
    return 0 if report['complete'] else 2


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',type=Path,required=True);p.add_argument('--ticker',required=True)
    p.add_argument('--date',required=True);p.add_argument('--max-records',type=int,default=200000)
    raise SystemExit(main(p.parse_args()))
