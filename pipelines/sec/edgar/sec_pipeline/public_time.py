"""Shared SEC acceptance/publication policy; acquisition is a separate clock."""
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo
import re

VERSION = 'retained-sec-acceptance-proxy-v1'
NY = ZoneInfo('America/New_York')


def historical_time(metadata):
    raw = metadata.get('acceptance_datetime_raw') or ''
    if re.fullmatch(r'[0-9]{14}', raw):
        naive = datetime.strptime(raw, '%Y%m%d%H%M%S')
        accepted = naive.replace(tzinfo=NY)
        if (accepted.utcoffset() != accepted.replace(fold=1).utcoffset()
                or accepted.astimezone(timezone.utc).astimezone(NY).replace(tzinfo=None) != naive):
            raise ValueError('Ambiguous or invalid SEC acceptance timezone')
    elif metadata.get('time_metadata_source') == 'sec_filing_v3' and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?Z', raw):
        accepted = datetime.fromisoformat(raw.replace('Z','+00:00')).astimezone(NY)
    else:
        raise ValueError('SEC acceptance timestamp is absent or malformed')
    filing = datetime.strptime(metadata.get('filing_date') or '', '%Y-%m-%d').date()
    if (metadata.get('private_to_public') or metadata.get('pac_event_id')
            or int(metadata.get('public_document_count') or 0) <= 0):
        raise ValueError('Private, corrected or non-public SEC occurrence needs separate review')
    if accepted.date() > filing or (filing - accepted.date()).days > 7:
        raise ValueError('SEC filing and acceptance chronology conflicts')
    available = max(accepted, datetime.combine(filing, time(), NY))
    return dict(available_at=available.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S.%f'),
                acceptance_at=accepted.astimezone(timezone.utc).isoformat(), filing_date=str(filing),
                availability_basis='sec_acceptance_proxy' if available == accepted else 'sec_later_filing_day',
                exact_publication_time=False, policy=VERSION,
                recorded_time_basis='ingestion_time_fallback_no_download_receipt')


def source_metadata(archive, filing):
    """Use exact submissions enrichment only for the same parent occurrence."""
    if archive.get('acceptance_datetime_raw'):
        return archive
    if (str(archive.get('primary_cik','')).zfill(10)!=str(filing.get('cik','')).zfill(10)
            or archive.get('accession_number')!=filing.get('accession_number')
            or archive.get('form_type')!=filing.get('form_type')
            or archive.get('filing_date')!=filing.get('filing_date')
            or filing.get('accepted_at_source') not in ('submissions_recent','submissions_bulk','submissions_bulk_fragment')):
        return archive
    raw=filing.get('acceptance_datetime_raw') or ''
    result=dict(archive,acceptance_datetime_raw=raw,time_metadata_source='sec_filing_v3',
        time_metadata_evidence=dict(cik=filing['cik'],accession_number=filing['accession_number'],
            primary_document=filing.get('primary_document'),accepted_at_source=filing['accepted_at_source'],
            acceptance_datetime_raw=raw,filing_date=filing['filing_date']))
    checked=historical_time(result)
    if filing.get('accepted_at_utc'):
        typed=datetime.fromisoformat(str(filing['accepted_at_utc']).replace('Z','+00:00'))
        if typed.tzinfo is None:typed=typed.replace(tzinfo=timezone.utc)
        if typed.astimezone(timezone.utc)!=datetime.fromisoformat(checked['acceptance_at']).astimezone(timezone.utc):
            raise ValueError('SEC typed and raw acceptance timestamps disagree')
    return result
