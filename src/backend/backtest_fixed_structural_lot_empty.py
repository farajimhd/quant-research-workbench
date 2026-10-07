"""Certified empty native horizon; no geometry, quote or order authority."""
from dataclasses import dataclass, fields
from datetime import date
from hashlib import sha256
import json
from threading import RLock
from uuid import UUID
from weakref import WeakKeyDictionary

from src.trading_runtime.journal_contract import canonical_json

_ISSUED = WeakKeyDictionary()
_LOCK = RLock()


def _same_candidates(left,right):
    import numpy as np
    if (left.token!=right.token or left.coverage!=right.coverage
            or left.excluded_tickers!=right.excluded_tickers
            or left.source_build_id!=right.source_build_id
            or left.candidate_rule_digest!=right.candidate_rule_digest
            or left.scan_query_sha256!=right.scan_query_sha256
            or len(left.prepared)!=len(right.prepared)):
        return False
    for expected,actual in zip(left.prepared,right.prepared):
        if type(expected) is not type(actual):return False
        for field in fields(expected):
            a,b=getattr(expected,field.name),getattr(actual,field.name)
            if type(a) is np.ndarray:
                if type(b) is not np.ndarray or a.dtype!=b.dtype or not np.array_equal(a,b):return False
            elif a!=b:return False
    return True


@dataclass(frozen=True, slots=True, eq=False, weakref_slot=True)
class PreparedEmptyFixedStructuralLotSource:
    run_id: str
    session_date: date
    through_boundary_ms: int
    market: object
    candidates: object
    parent_json: str
    installed_json: str
    selected_json: str
    policy: object
    parent_payload_hash: str
    selected_configuration_hash: str
    # None is deliberate: emptiness must not masquerade as a V7/quote source.
    intervals = None
    price_authority = None

    @property
    def installed_payload(self):return json.loads(self.installed_json)
    @property
    def selected_payload(self):return json.loads(self.selected_json)
    @property
    def _strategy_id(self):return self.installed_payload['strategy']['strategy_id']
    @property
    def _revision(self):return self.installed_payload['strategy']['revision']

    def require_prepared_source(self):
        with _LOCK:binding=_ISSUED.get(self)
        if (binding is None or binding[0] is not self.market or binding[1] is not self.candidates
                or binding[2:] != (self.run_id,self.session_date,self.through_boundary_ms,
                    self.parent_json,self.installed_json,self.selected_json,self.policy,
                    self.parent_payload_hash,self.selected_configuration_hash)):
            raise ValueError('Empty source was not independently factory certified')
        return self

    def require_installed_admission(self):
        self.require_prepared_source()
        from .backtest_fixed_structural_lot_native import require_installed_source
        require_installed_source(self)

    def request(self, proposal):
        raise ValueError('Certified empty source grants no entry, quote, geometry or lot request')


def prepare_empty_fixed_structural_lot_source(client, *, number,run_id,session_date,
        market,candidates,through_boundary_ms):
    from .backtest_market_data import CertifiedMarketDayPlan,verify_market_day_plan
    from .backtest_strategy_one_candidate_store import (
        CertifiedCandidatePlan,certify_candidate_plan,exclude_candidate_tickers,
        project_candidate_plan,RULE_DIGEST)
    from .backtest_input_scope import input_exclusions
    from .backtest_fixed_structural_lot_native import (
        numbered_strategy_parent,certify_numbered_configuration,load_installed_configuration,
        _issue_installed_source)
    from .backtest_fixed_structural_lot_source import derive_fixed_structural_lot_configuration
    if (type(market) is not CertifiedMarketDayPlan or type(candidates) is not CertifiedCandidatePlan
            or type(session_date) is not date or market.sessions!=(session_date.isoformat(),)
            or type(run_id) is not str or str(UUID(run_id))!=run_id
            or type(through_boundary_ms) is not int or not 0<through_boundary_ms<=57_600_000
            or through_boundary_ms%100):
        raise ValueError('Empty native horizon requires exact source identity and certified clock')
    # Actual complete market rows and candidate coverage are rechecked before
    # any scope projection. An absent product is never an empty certificate.
    verify_market_day_plan(market,client=client)
    full=certify_candidate_plan(market,candidate_rule_digest=RULE_DIGEST,
        through_boundary_ms=57_600_000,client=client)
    occupied={row.ticker for row in full.coverage if row.candidate_count>0}
    exclusions=tuple(ticker for ticker in input_exclusions(session_date.isoformat()) if ticker in occupied)
    fresh=exclude_candidate_tickers(full,exclusions)
    if (not _same_candidates(fresh,candidates) or project_candidate_plan(fresh,through_boundary_ms=through_boundary_ms).prepared):
        raise ValueError('Declared empty horizon differs from genuine complete candidate source')
    parent=certify_numbered_configuration(client,numbered_strategy_parent(number))
    own,policy,proof=load_installed_configuration(client,number=number,parent=parent)
    parent_json=canonical_json(parent.payload)
    if sha256(parent_json.encode()).hexdigest()!=parent.payload_hash:
        raise ValueError('Empty source parent content differs from its immutable certificate')
    selected_json=canonical_json(derive_fixed_structural_lot_configuration(parent.payload,
        policy,installed_configuration=own.payload))
    source=PreparedEmptyFixedStructuralLotSource(run_id,session_date,through_boundary_ms,
        market,fresh,parent_json,canonical_json(own.payload),selected_json,policy,
        parent.payload_hash,sha256(selected_json.encode()).hexdigest())
    with _LOCK:_ISSUED[source]=(market,fresh,run_id,session_date,through_boundary_ms,
        source.parent_json,source.installed_json,source.selected_json,policy,
        source.parent_payload_hash,source.selected_configuration_hash)
    _issue_installed_source(source,own,proof)
    return source
