"""Bounded issuer cohorts retain the full admitted-universe evidence boundary."""
from bisect import bisect_left,bisect_right
from datetime import date,datetime,time,timezone
from zoneinfo import ZoneInfo
from .contracts import digest
from .identity import ISSUER,identity_key

VERSION='complete-issuer-cohort-partition-v1'


def select(snapshot,issuer_keys):
    """Select only after full-snapshot admission; unrelated rejects never vanish."""
    issuer_keys=tuple(issuer_keys)
    if not issuer_keys:return snapshot
    if (tuple(sorted(set(issuer_keys)))!=issuer_keys or len(issuer_keys)>256
            or any(type(k) is not int or not 0<k<2**64 for k in issuer_keys)):
        raise ValueError('Invalid bounded issuer cohort')
    if not snapshot.get('ready') or snapshot.get('rejected'):
        raise ValueError('Full historical universe must qualify before cohort selection')
    available={identity_key(ISSUER,i.issuer_id) for i in snapshot['intervals']}
    if set(issuer_keys)-available:raise ValueError('Issuer cohort contains an absent historical identity')
    intervals=tuple(i for i in snapshot['intervals'] if identity_key(ISSUER,i.issuer_id) in issuer_keys)
    listings={i.listing_id for i in intervals}
    full_hash=snapshot.get('full_source_hash',snapshot['source_hash'])
    result=dict(snapshot,intervals=intervals,source_rows=tuple(r for r in snapshot.get('source_rows',()) if r['listing_id'] in listings),
        full_source_hash=full_hash,issuer_keys=issuer_keys)
    result['source_hash']=digest(dict(version=VERSION,full_source_hash=full_hash,issuer_keys=issuer_keys))
    return result


def partition(snapshot,membership,config,*,maximum_issuers=128,maximum_sessions=200000):
    """Greedy complete lanes, bounded by actual context/session demand.

    This does not raise the runtime's graph limits. If one issuer cannot fit,
    planning blocks with its measured demand instead of splitting its history.
    """
    if (not snapshot.get('ready') or snapshot.get('rejected') or not membership.get('ready')
            or config.issuer_keys or type(maximum_issuers) is not int or not 1<=maximum_issuers<=256
            or type(maximum_sessions) is not int or not 1<=maximum_sessions<=250000):
        raise ValueError('Invalid complete-universe cohort partition scope')
    owners={listing:security for security,listings in membership['security_listings'].items() for listing in listings}
    target={membership['security_issuers'][owners[s.listing_key]] for s in membership['sessions'] if config.start<=s.day<=config.end}
    if not target:raise ValueError('Cohort partition has no target issuers')
    counts={k:0 for k in target}
    for session in membership['sessions']:
        issuer=membership['security_issuers'][owners[session.listing_key]]
        if issuer in counts:counts[issuer]+=1
    result=pack(snapshot,config,counts,maximum_issuers,maximum_sessions)
    result['membership_hash']=membership['membership_hash'];result.pop('partition_hash')
    return dict(result,partition_hash=digest(result))


def pack(snapshot,config,counts,maximum_issuers,maximum_sessions):
    groups=[];current=[];used=0
    for issuer,count in sorted(counts.items()):
        if count>maximum_sessions:raise ValueError(f'Issuer {issuer}: {count} sessions exceed cohort budget')
        if current and (len(current)==maximum_issuers or used+count>maximum_sessions):
            groups.append(tuple(current));current=[];used=0
        current.append(issuer);used+=count
    if current:groups.append(tuple(current))
    if tuple(sorted(k for group in groups for k in group))!=tuple(sorted(counts)):
        raise ValueError('Cohort partition omitted or duplicated an issuer')
    value=dict(version=VERSION,full_source_hash=snapshot['source_hash'],config_hash=config.content_hash,
        issuer_keys=tuple(sorted(counts)),groups=tuple(groups),session_counts=tuple(sorted(counts.items())),
        maximum_issuers=maximum_issuers,maximum_sessions=maximum_sessions)
    return dict(value,partition_hash=digest(value))


def partition_snapshot(snapshot,config,days,*,maximum_issuers=128,maximum_sessions=200000):
    """Count full session intersections without expanding listing-day rows.

    Binary search uses the same full-session boundary contract as execution
    membership. Memory is O(intervals + source days), not O(listings * days).
    """
    from .identity import validate_intervals
    days=tuple(days)
    if (not snapshot.get('ready') or snapshot.get('rejected') or config.issuer_keys or not days
            or tuple(sorted(set(days)))!=days or any(type(d) is not date for d in days)
            or type(maximum_issuers) is not int or not 1<=maximum_issuers<=256
            or type(maximum_sessions) is not int or not 1<=maximum_sessions<=250000):
        raise ValueError('Invalid bounded cohort census scope')
    ny=ZoneInfo('America/New_York')
    starts=tuple(datetime.combine(d,time(4),ny).astimezone(timezone.utc) for d in days)
    ends=tuple(datetime.combine(d,time(20),ny).astimezone(timezone.utc) for d in days)
    target_left=bisect_left(days,config.start);target_right=bisect_right(days,config.end)
    counts={};targets=set();listing_owners={};security_owners={};ranges={}
    for interval in validate_intervals(tuple(snapshot['intervals'])):
        if interval.reason:continue
        left=interval.valid_from.astimezone(timezone.utc);right=interval.valid_to.astimezone(timezone.utc)
        first=bisect_left(starts,left);last=bisect_right(ends,right)
        full=max(0,last-first)
        overlap=max(0,bisect_left(starts,right)-bisect_right(ends,left))
        if overlap!=full:raise ValueError('Partial-day membership prevents complete cohort partition')
        if not full:continue
        if (listing_owners.setdefault(interval.listing_id,interval.security_id)!=interval.security_id
                or security_owners.setdefault(interval.security_id,interval.issuer_id)!=interval.issuer_id):
            raise ValueError('Cohort census has competing historical identity owners')
        ranges.setdefault(interval.security_id,[]).append((first,last))
        issuer=identity_key(ISSUER,interval.issuer_id);counts[issuer]=counts.get(issuer,0)+full
        if max(first,target_left)<min(last,target_right):targets.add(issuer)
    for intervals in ranges.values():
        previous=-1
        for first,last in sorted(intervals):
            if first<previous:raise ValueError('Competing market listings prevent complete cohort partition')
            previous=last
    if not targets:raise ValueError('Cohort partition has no admitted target issuers')
    result=pack(snapshot,config,{k:counts[k] for k in targets},maximum_issuers,maximum_sessions)
    result.update(context_start=str(days[0]),context_end=str(days[-1]),context_sessions=len(days),days_hash=digest(days))
    result.pop('partition_hash')
    return dict(result,partition_hash=digest(result))
