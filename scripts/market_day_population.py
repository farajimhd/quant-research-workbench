"""Read-only adapter for the approved retrospective listing policy."""
from datetime import datetime, time
from zoneinfo import ZoneInfo

from pipelines.certified_research import retrospective_identity
from pipelines.certified_research.contracts import digest


def retrospective_population(client, args, sessions):
    snapshot = retrospective_identity.load(
        lambda query: client.query(query, 'retrospective_identity'),
        args.start, args.end, args.identity_anchor_date, args.conid_review_hash)
    if not snapshot['ready']:
        raise ValueError('Approved retrospective population has no eligible intervals')
    populations, by_day = [], {}
    for day in sessions:
        start = datetime.combine(day, time(4), ZoneInfo('America/New_York'))
        end = datetime.combine(day, time(20), ZoneInfo('America/New_York'))
        members = []
        for interval in snapshot['intervals']:
            if interval.reason or interval.valid_to <= start or interval.valid_from >= end:
                continue
            if interval.valid_from > start or interval.valid_to < end:
                raise ValueError(f'Intraday listing identity boundary: {day} {interval.ticker}')
            members.append(dict(ticker=interval.ticker, listing_id=interval.listing_id,
                security_id=interval.security_id, conid=interval.conid,
                evidence_hash=interval.evidence_hash))
        members.sort(key=lambda r: (r['ticker'], r['listing_id']))
        tickers = {r['ticker'] for r in members}
        if not members or len(tickers) != len(members):
            raise ValueError(f'Empty or ambiguous retrospective population for {day}')
        by_day[str(day)] = tickers
        populations.append(dict(session_date=str(day), authority=retrospective_identity.POLICY,
            certificate=dict(status='approved_retrospective', source_hash=snapshot['source_hash'],
                review_hash=args.conid_review_hash, anchor_date=str(args.identity_anchor_date),
                historical_membership_certified=False),
            tradable_tickers=len(tickers), snapshot_rows=len(members), snapshot_hash=digest(members)))
    evidence = dict(policy=retrospective_identity.POLICY, source_hash=snapshot['source_hash'],
        conid_pool=snapshot['conid_pool'], quarantine=snapshot['quarantine'],
        quarantine_counts=snapshot['quarantine_counts'], eligibility_census=snapshot['eligibility_census'],
        historical_membership_certified=False)
    return populations, by_day, evidence
