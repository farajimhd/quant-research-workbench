"""Identity-only dated provider observations; never fundamental availability."""
from dataclasses import dataclass,asdict
from datetime import date
import re
from .contracts import digest


@dataclass(frozen=True)
class ListingObservation:
    as_of: date
    ticker: str
    composite_figi: str
    share_class_figi: str
    cik: str
    exchange: str
    currency: str
    provider_type: str
    active: bool
    list_date: date|None
    delisted_date: date|None

    @property
    def evidence_hash(self):
        return digest(dict(version='provider-dated-listing-observation-v1',**asdict(self)))


def normalize(payload,as_of,requested_ticker):
    """Project only identity/lifecycle fields from a dated API response.

    A successful observation proves this date only. Interval reconstruction
    still requires dated endpoints and complete intervening change coverage.
    Current conids are research identity anchors, not historical execution proof.
    """
    if type(as_of) is not date or not isinstance(payload,dict):raise ValueError('Dated identity observation is required')
    ticker=str(payload.get('ticker') or '').strip().upper()
    if not re.fullmatch(r'[A-Z0-9._-]{1,32}',ticker) or ticker!=requested_ticker.strip().upper():
        raise ValueError('Historical overview returned a different or invalid ticker')
    if payload.get('market')!='stocks' or payload.get('locale')!='us':
        raise ValueError('Historical identity observation is outside US stock scope')
    figi=str(payload.get('composite_figi') or '').strip().upper()
    share=str(payload.get('share_class_figi') or '').strip().upper()
    if not re.fullmatch('[A-Z0-9]{12}',figi) or (share and not re.fullmatch('[A-Z0-9]{12}',share)):
        raise ValueError('Historical identity requires stable FIGI evidence')
    cik=str(payload.get('cik') or '').strip()
    if cik and (not re.fullmatch('[0-9]{1,10}',cik) or int(cik)==0):raise ValueError('Invalid historical issuer CIK')
    exchange=str(payload.get('primary_exchange') or '').strip().upper()
    currency=str(payload.get('currency_name') or '').strip().upper()
    if not exchange or currency!='USD' or type(payload.get('active')) is not bool:
        raise ValueError('Historical venue/currency/activity evidence is incomplete')
    listed=date.fromisoformat(payload['list_date']) if payload.get('list_date') else None
    delisted=date.fromisoformat(str(payload['delisted_utc'])[:10]) if payload.get('delisted_utc') else None
    if ((listed and listed>as_of) or (listed and delisted and listed>delisted)
            or (payload['active'] and delisted and delisted<=as_of)):
        raise ValueError('Historical lifecycle dates conflict with the observation')
    return ListingObservation(as_of,ticker,figi,share,cik.zfill(10) if cik else '',exchange,currency,
        str(payload.get('type') or ''),payload['active'],listed,delisted)


def reconcile(observation,*,expected_composite_figi,expected_cik=None):
    """Fail closed on ticker reuse or an unresolved issuer transition."""
    if observation.composite_figi!=expected_composite_figi.strip().upper():
        raise ValueError('Historical ticker belongs to a different security')
    if expected_cik and observation.cik!=str(expected_cik).zfill(10):
        raise ValueError('Historical issuer transition requires canonical resolution')
    return observation
