"""Explicit workstation V7 campaign authority; no discovery of legacy books."""
from bisect import bisect_left
from functools import lru_cache
import os
from pathlib import Path
import re

from src.runtime_paths import WORKSTATION_RUNTIME_ROOT
from .level_book_store import read, verified_book
from .historical_level_checkpoint import digest
from .streaming_level_book import EXTRACTION_VERSION
from .reaction_band import CONFIG
from .derived_trade_policy import POLICY

BOOK_ID = 'level-book-v7'
FILTERED_CAMPAIGN = 'filtered-0405-v1'


class CoverageUnavailable(ValueError):
    """Expected ticker eligibility failure, distinct from corrupt authority."""

CAMPAIGNS = (
    'all-tradable-20250101-20260912-mle-v1',
    'deferred-common-share-repair-20260913/common-share-supplement',
    'urg-analytic-solver-recovery-v1',
)


def shared_root():
    default = Path('D:/TradingML/runtimes/level-book-v7') if os.environ.get('COMPUTERNAME','').upper()=='DESKTOP-SAAI85T' else WORKSTATION_RUNTIME_ROOT/'level-book-v7'
    root = Path(os.environ.get('QMD_LEVEL_BOOK_V7_ROOT', str(default))).resolve()
    if not root.is_dir():
        raise ValueError('Workstation V7 checkpoint root unavailable')
    return root


def checked_json(path, key):
    value = read(path)
    if value.get(key) != digest({k:v for k,v in value.items() if k!=key}):
        raise ValueError('V7 artifact integrity mismatch: '+str(path))
    return value


class Catalog:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else shared_root()
        self.plans=[];self.by_ticker={}
        for relative in (*CAMPAIGNS, FILTERED_CAMPAIGN):
            path=self.root/relative/'plan.json'
            if not path.exists():
                if relative==CAMPAIGNS[0]:raise ValueError('Main V7 campaign is unavailable')
                continue
            plan=checked_json(path,'plan_hash')
            if plan.get('extraction_version')!=EXTRACTION_VERSION or plan.get('band_config')!=CONFIG:
                raise ValueError('Incompatible V7 extraction/MLE contract')
            if relative == FILTERED_CAMPAIGN:
                if plan.get('input_policy') != POLICY:
                    raise ValueError('Filtered V7 campaign has the wrong input policy')
                if self.plans and plan['start'] > self.plans[0][1]['start']:
                    raise ValueError('Filtered V7 campaign must rebuild the full historical prefix')
            elif self.plans and plan.get('parent_plan_hash')!=self.plans[0][1]['plan_hash']:
                raise ValueError('V7 supplement/recovery has the wrong parent campaign')
            self.plans.append((path.parent,plan))
            for row in plan['rows']:
                if row['status']=='deferred':continue
                ticker=row['ticker']
                if not re.fullmatch(r'[A-Z0-9.\- ]{1,30}',ticker):
                    raise ValueError('Invalid V7 ticker')
                self.by_ticker.setdefault(ticker,[]).append((path.parent,plan,row))
        from .filtered_v7_history import VERSION, kernel
        self.fingerprint=digest(dict(plans=[p['plan_hash'] for _,p in self.plans],
                                     filtered_successor=VERSION, kernel=kernel()))

    @lru_cache(maxsize=128)
    def sources(self,ticker):
        result=[]
        for root,plan,row in self.by_ticker.get(ticker,[]):
            directory=row['directory']
            if not directory or Path(directory).name!=directory:
                raise ValueError('Invalid V7 ticker directory')
            target=root/'tickers'/directory
            try:
                source=read(target/'source-plan.json')
            except FileNotFoundError as exc:
                raise CoverageUnavailable('V7 source plan unavailable for '+ticker) from exc
            if source['plan_hash']!=plan['plan_hash']:
                raise ValueError('V7 ticker source plan mismatch')
            days=[d['source_date'] for d in source['days']]
            if days!=sorted(set(days)) or any(d['ticker']!=ticker for d in source['days']):
                raise ValueError('V7 source sessions must be unique, ordered and ticker-specific')
            result.append((target,plan,source))
        return result

    def select(self,ticker,session):
        """Choose the exact preceding source session, never an older stale book."""
        candidates=self.sources(ticker)
        from .filtered_v7_history import available_sources
        candidates = candidates + available_sources(self.root, ticker, candidates)
        if not candidates:
            reasons = sorted({row.get('reason', 'unpublished') for _, plan in self.plans
                              for row in plan['rows'] if row['ticker'] == ticker})
            raise CoverageUnavailable('No V7 historical coverage for '+ticker
                                      + (': ' + '; '.join(reasons) if reasons else ': absent from published campaigns'))
        failures=[]
        for target,plan,source in reversed(candidates):
            days=[d['source_date'] for d in source['days']]
            index=bisect_left(days,session)-1
            if index<0:
                continue
            try:
                empty=[]
                while index>=0:
                    day=days[index];receipt=read(target/'receipts'/f'{day}.json')
                    from research.level_book.v7.campaign_source import source_hash
                    if receipt['source_hash']!=source_hash(source['days'][index],plan['rules']):
                        raise ValueError('V7 receipt source mismatch')
                    if receipt['state']=='empty':empty.append(day);index-=1;continue
                    if receipt['state']!='complete':raise ValueError('V7 session is not complete')
                    book=verified_book(target/'books'/f'{day}.json.gz')
                    if (receipt['checkpoint_hash']!=book['checkpoint_hash'] or book['ticker']!=ticker
                        or book['session']!=day or receipt['parent_hash']!=book.get('prior_checkpoint_hash')):
                        raise ValueError('V7 checkpoint/receipt identity mismatch')
                    if plan.get('input_policy') == POLICY and book.get('input_policy') != POLICY:
                        raise ValueError('Filtered V7 checkpoint does not certify the input policy')
                    return book,dict(campaign=str(target.parent.parent.relative_to(self.root)),
                        plan_hash=plan['plan_hash'],source_plan=source,last_source_session=days[-1],
                        checkpoint_session=day,verified_empty_sessions=empty,book_id=BOOK_ID,catalog_hash=self.fingerprint)
            except FileNotFoundError:
                failures.append(str(target))
                continue
        raise CoverageUnavailable(f'Verified preceding V7 checkpoint unavailable for {ticker} {session}; no stale or legacy fallback')

    def items(self):
        return [dict(id=BOOK_ID,ticker=ticker,version='causal-level-book-v7-mle-1',
            fingerprint=self.fingerprint,start=min(p['start'] for _,p,_ in entries),
            end=max(p['end'] for _,p,_ in entries),readiness='verified_per_session_on_load')
            for ticker,entries in sorted(self.by_ticker.items())]
