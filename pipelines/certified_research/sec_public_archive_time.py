"""Explicit conservative public-feed-day availability; never acceptance-as-posting."""
from datetime import date,datetime,time,timedelta,timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo
import re

from .contracts import digest
from .facts import CONSERVATIVE_TIME
from . import sec_receipt_recovery

VERSION='verified-sec-public-feed-day-v1'
NY=ZoneInfo('America/New_York')


def qualify(witness,receipt,*,mode='acceptance_checked_day'):
    """Require original public-feed bytes, official receipt and unambiguous day.

    A qualified day is a reconstructed public date with a conservative next-day
    cutoff, not observed app arrival or an exact SEC website posting timestamp.
    PAC/private-to-public/paper and delayed-day ambiguity remain unsupported.
    """
    if mode not in ('acceptance_checked_day','official_filing_day','official_feed_representation_day','verified_download_time'):raise ValueError('Unknown explicit SEC public-day mode')
    body=dict(witness);hash_=body.pop('witness_hash',None)
    if (hash_!=digest(body) or witness.get('version') not in ('sec-archive-document-byte-witness-v2','sec-archive-original-document-witness-v3')
            or witness.get('original_bytes_verified') is not True or witness.get('container_digest_verified') is not True
            or witness.get('special_release') is not False or type(witness.get('public_document_count')) is not int
            or witness['public_document_count']<=0):
        raise ValueError('SEC public-day qualification requires a verified ordinary public submission')
    if witness['version']=='sec-archive-original-document-witness-v3':
        if (witness.get('source_authority')!='original_archive_document'
                or not re.fullmatch('[0-9a-f]{64}',witness.get('retained_document_hash',''))
                or not re.fullmatch('[0-9a-f]{64}',witness.get('document_hash',''))
                or not re.fullmatch('0*[1-9][0-9]{0,9}',witness.get('cik',''))
                or not re.fullmatch('[0-9]{10}-[0-9]{2}-[0-9]{6}',witness.get('accession',''))
                or not witness.get('document_name')):
            raise ValueError('Archive-original document identity is incomplete')
    if not isinstance(witness.get('form_type'),str) or not witness['form_type']:
        raise ValueError('SEC public-day submission type is unknown')
    day=date.fromisoformat(witness['source_archive_date']);quarter=(day.month-1)//3+1
    if witness.get('official_filing_date')!=str(day):
        raise ValueError('SEC official filing/change dates do not prove the selected public feed day')
    change=witness.get('filing_date_change')
    if mode=='official_feed_representation_day':
        # SEC PDS: this optional field is the last post-acceptance change date,
        # not dissemination time. Only an explicit, valid earlier date qualifies
        # here; exact feed bytes/receipt/Last-Modified below prove the later
        # representation day. Old policies retain their equality requirement.
        if not isinstance(change,str) or not re.fullmatch('[0-9]{4}-[0-9]{2}-[0-9]{2}',change):
            raise ValueError('SEC change-date chronology is unknown')
        if not date(1900,1,1)<=date.fromisoformat(change)<day:
            raise ValueError('SEC change-date chronology is not an earlier date')
    elif mode=='verified_download_time':
        if not isinstance(change,str) or not date(1900,1,1)<=date.fromisoformat(change)<=day:
            raise ValueError('SEC change-date chronology is unknown')
    elif change!=str(day):
        raise ValueError('SEC official filing/change dates do not prove the selected public feed day')
    url=f'https://www.sec.gov/Archives/edgar/Feed/{day.year}/QTR{quarter}/{day:%Y%m%d}.nc.tar.gz'
    source=sec_receipt_recovery.canonical(receipt)
    if (receipt.get('source_kind')!='daily_feed_archive'
            or receipt.get('source_url')!=url or receipt.get('source_date')!=str(day)
            or receipt.get('sha256')!=witness['container_sha256'] or not re.fullmatch('[0-9a-f]{64}',receipt.get('sha256') or '')
            or type(receipt.get('byte_size')) is not int or receipt['byte_size']!=witness['archive_bytes']
            or receipt.get('error')):
        raise ValueError('Original SEC feed download receipt differs from witnessed bytes/day')
    modified=parsedate_to_datetime(receipt.get('last_modified') or '')
    observed=datetime.fromisoformat(receipt.get('downloaded_at_utc','').replace('Z','+00:00'))
    if (modified.tzinfo is None or observed.tzinfo is None or modified>observed
            or (modified.astimezone(NY).date()!=day if mode!='verified_download_time'
                else modified.astimezone(NY).date()<day)):
        raise ValueError('SEC feed representation modification day is not qualified')
    raw=witness.get('acceptance_datetime_raw') or ''
    if raw or mode=='acceptance_checked_day':
        if not re.fullmatch('[0-9]{14}',raw):raise ValueError('SEC acceptance header is unknown')
        local=datetime.strptime(raw,'%Y%m%d%H%M%S');accepted=local.replace(tzinfo=NY)
        if (accepted.astimezone(timezone.utc).astimezone(NY).replace(tzinfo=None)!=local
                or accepted.utcoffset()!=accepted.replace(fold=1).utcoffset() or accepted.date()>day):
            raise ValueError('SEC acceptance header has invalid/ambiguous chronology')
        deadline=time(22) if witness['form_type'] in ('3','4','5') else time(17,30)
        if accepted.date()==day and accepted.time()>=deadline:
            raise ValueError('Same-day SEC dissemination is unproven for a late acceptance')
    available=(observed.astimezone(timezone.utc) if mode=='verified_download_time' else
               datetime.combine(day+timedelta(days=1),time(),NY).astimezone(timezone.utc))
    result=dict(version='verified-sec-feed-representation-day-v2' if mode=='official_feed_representation_day' else VERSION,mode=mode,witness_hash=hash_,receipt_hash=digest(source),source_date=str(day),
        available_at=available.isoformat(),precision=CONSERVATIVE_TIME,precision_name='conservative_day',public_time_qualified=True,
        exact_posting_time=False,allocation_certified=False)
    if mode=='verified_download_time':
        # A complete witnessed public download proves availability only at this
        # observation. Never backdate regenerated bytes to the filing/feed day.
        if receipt.get('status')!='downloaded':raise ValueError('Verified download time requires the original complete receipt')
        result.update(version='verified-sec-download-observation-v1',availability_basis='verified_public_download',
                      filing_date=str(day),exact_posting_time=False)
    return dict(result,qualification_hash=digest(result))
