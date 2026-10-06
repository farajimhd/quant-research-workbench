"""Pure bounded grant inventory; no credentials, SQL execution or apply authority.

These are dedicated profiles, not changes to legacy runner grants. Unresolved
installed/readback dependencies remain explicit and prohibit deployment-ready
claims. A source plan does not prove effective grants or physical placement.
"""
from dataclasses import dataclass, replace
import re
from scripts.clickhouse.provision_backtest_v4_runner import desired_plan as base_plan
from scripts.clickhouse.provision_fixed_backtest_v3_principals import PrincipalPlan
from src.trading_runtime.arte_declared_native_entry_schema import TABLES as ENTRY
from src.trading_runtime.arte_declared_native_management_schema import TABLES as MANAGEMENT
from src.trading_runtime.arte_running_financial_checkpoint_schema import TABLES as CHECKPOINT
from src.trading_runtime.arte_market_day_certification import TABLES as CERTIFICATES
from src.trading_runtime.arte_market_day_session_seal import SESSION_SEAL
from src.trading_runtime import (strategy_one_candidate_schema as candidate,
    strategy_one_pivot_schema as pivot, strategy_one_hod_schema as hod,
    strategy_one_entry_evidence_schema as entry)
from src.trading_runtime.strategy_one_configuration_tree import NODE_TABLE, RELEASE_TABLE

RUNNING_PRINCIPAL='backtest_v4_declared_native_runner'
READ_PRINCIPAL='backtest_v4_declared_native_reader'
COMPANIONS=ENTRY+MANAGEMENT+CHECKPOINT

@dataclass(frozen=True,slots=True)
class DependencyEvidence:
    table: str
    source: str

@dataclass(frozen=True,slots=True)
class DeclaredNativePrincipalPlans:
    running: PrincipalPlan
    reader: PrincipalPlan
    evidence: tuple[DependencyEvidence,...]
    unresolved: tuple[str,...]

    def __post_init__(self):
        if (type(self.running) is not PrincipalPlan or type(self.reader) is not PrincipalPlan
                or self.running.principal != RUNNING_PRINCIPAL or self.reader.principal != READ_PRINCIPAL
                or type(self.evidence) is not tuple or type(self.unresolved) is not tuple
                or any(type(s) is not str or not s for s in self.unresolved)
                or any(type(e) is not DependencyEvidence for e in self.evidence)):
            raise ValueError('Declared principal plan has foreign identity or inventory')
        for p in (self.running,self.reader):
            for field in ('select_arte','insert_arte','select_system','select_reference'):
                if type(getattr(p,field)) is not frozenset:
                    raise ValueError('Principal plan sets must be immutable')
            for name in p.select_arte|p.insert_arte|p.select_system:
                if type(name) is not str or re.fullmatch('[a-z][a-z0-9_]*',name) is None:
                    raise ValueError('Principal plan wildcard or foreign table is forbidden')
            for pair in p.select_reference:
                if type(pair) is not tuple or len(pair)!=2 or pair!=('q_live','market_stock_split_v1'):
                    raise ValueError('Declared reference grant is outside causal V7 split reads')
        if self.evidence != _source_evidence():
            raise ValueError('Declared source dependency evidence differs from reviewed query inventory')
        base=base_plan();own=frozenset(t.name for t in COMPANIONS)
        selected=frozenset(e.table for e in self.evidence)
        if (len(own)!=16 or len(self.evidence)!=len(selected)
                or self.running.insert_arte != base.insert_arte|own
                or self.reader.insert_arte or self.running.select_arte != base.select_arte|own|selected
                or self.reader.select_arte != self.running.select_arte
                or self.running.select_system != base.select_system
                or self.reader.select_system != base.select_system
                or self.running.select_reference != frozenset({('q_live','market_stock_split_v1')})
                or self.reader.select_reference != self.running.select_reference):
            raise ValueError('Declared principal dependency or authority drift')

    def require_deployment_ready(self):
        self.__post_init__()
        if self.unresolved:
            raise RuntimeError('Declared principal closure unresolved: '+ '; '.join(self.unresolved))
        raise RuntimeError('Source grant inventory alone cannot certify deployed effective grants/storage')


def _source_evidence():
    """Exact source-only table inventory referenced by prepared consumers."""
    facts={t.name:'backtest_market_data.verify_market_day_plan -> arte_market_day_cold_preflight' for t in CERTIFICATES}
    facts[SESSION_SEAL.name]='backtest_market_plan_cache market certificate/session seal fingerprint'
    for table,source in (
        (candidate.CANDIDATE_TABLE,'backtest_strategy_one_candidate_store.certify_candidate_plan'),
        (candidate.COVERAGE_TABLE,'backtest_strategy_one_candidate_store.certify_candidate_plan'),
        (pivot.PIVOT_TABLE,'backtest_strategy_one_pivot_store.certify_pivot_plan'),
        (pivot.COVERAGE_TABLE,'backtest_strategy_one_pivot_store.certify_pivot_plan'),
        (hod.CONTEXT_TABLE,'backtest_strategy_one_hod_store.certify_hod_plan'),
        (hod.COVERAGE_TABLE,'backtest_strategy_one_hod_store.certify_hod_plan'),
        (entry.ACTIVATION_TABLE,'backtest_strategy_one_entry_store.certify_entry_evidence_plan'),
        (entry.ACTIVATION_RESISTANCE_TABLE,'backtest_strategy_one_entry_store.certify_entry_evidence_plan'),
        (entry.EVIDENCE_TABLE,'backtest_strategy_one_entry_store.certify_entry_evidence_plan'),
        (entry.COVERAGE_TABLE,'backtest_strategy_one_entry_store.certify_entry_evidence_plan'),
        (NODE_TABLE,'backtest_strategy_one_configuration.certify_numbered_configuration exact parent nodes'),
        (RELEASE_TABLE,'backtest_strategy_one_configuration.certify_numbered_configuration exact parent release')):
        if not table.startswith('arte.'):raise ValueError('Foreign source dependency')
        facts[table.split('.',1)[1]]=source
    return tuple(DependencyEvidence(k,v) for k,v in sorted(facts.items()))

def desired_declared_native_plans():
    """Return source-only exact running/read sets and observed query evidence."""
    evidence=_source_evidence(); facts={e.table for e in evidence}
    base=base_plan();own=frozenset(t.name for t in COMPANIONS)
    running=replace(base,principal=RUNNING_PRINCIPAL,select_arte=base.select_arte|own|frozenset(facts),
        insert_arte=base.insert_arte|own,select_reference=frozenset({('q_live','market_stock_split_v1')}))
    reader=replace(running,role='reader',principal=READ_PRINCIPAL,insert_arte=frozenset())
    return DeclaredNativePrincipalPlans(running,reader,evidence,(
        'Own installed full managed configuration/source approval consumer and its exact normalized SELECT inventory are not installed.',
        'Own historical OMS/ACK/protection and assignment membership acceptance remains closed; final concrete reader query dependency closure must be checked.',
        'Execution-price product selection is optional on existing market plans; selected native assembly must declare if liquidity_execution_price_100ms_v1/coverage are needed before grants.',
        'Dedicated managed readonly factory/principal and exact profile preflight are not implemented here; existing legacy permission preflight does not admit all these producer reads.',
        'Keeper market certificate and running checkpoint latest/historical receipt authority/ACLs require separate managed verification; SQL grants cannot attest them.',
    ))
