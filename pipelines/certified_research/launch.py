"""One execution controller for market, reference and acquisition registries."""
from .campaign import Campaign,load_plan
from .handlers import MarketHandlers,ReferenceHandlers,ActivityHandlers,UniverseHandlers,CanonicalSourceHandlers,PreparedShortSourceHandlers,PreparedSecSourceHandlers,V7SeedHandlers,IndicatorSeedHandlers,ReferenceAuthorityHandlers
from .handlers import ReferenceImportHandlers,RebuildOriginHandlers
from .contracts import digest
from . import installation,runtime_plan,checkpoint_backfill


def combine_registries(registries):
    producers={};verifiers={}
    for registry in registries:
        added,checked=registry.registry()
        if set(added)!=set(checked):raise ValueError('Every registered producer requires its authority verifier')
        overlap=set(added)&set(producers)
        if overlap:raise ValueError('Competing execution authority: '+','.join(sorted(overlap)))
        producers.update(added);verifiers.update(checked)
    return producers,verifiers


class ExecutionLaunch:
    """Assemble concrete handlers; unresolved prerequisite handlers block construction.

    The launcher owns configuration/source preparation and secure claims.
    External registries are typed application handlers for acquisition, seed
    and corporate/allocation stages, never code embedded in plan arguments.
    """
    @classmethod
    def from_stored(cls,query,database,dataset_id,claim,client_factory,source_guard,
                    capacity_guard,dictionary_claim_factory,runtime_factory=None,*,
                    prerequisite_registries=(),maximum_units=1_000_000,
                    maximum_metadata_bytes=256*1024**2,storage_observer=None,original_parity_guard=None):
        """Restore handlers against the immutable ClickHouse plan, without writes.

        Runtime construction receives the validated stored configuration and
        dependency graph. Current command-line dates or resource defaults never
        replace them. The regular run/verify gates remain mandatory afterward.
        """
        claim.check();source_guard()
        saved=load_plan(query,database,dataset_id,maximum_units=maximum_units,
            maximum_metadata_bytes=maximum_metadata_bytes)
        if runtime_factory is None:
            runtime_factory=lambda frozen:runtime_plan.runtime(frozen,claim,client_factory,source_guard)
        runtime=runtime_factory(saved)
        launch=cls(query,saved.config,saved.dataset_id,saved.units,saved.source_hash,
            claim,client_factory,source_guard,capacity_guard,dictionary_claim_factory,
            runtime,prerequisite_registries=prerequisite_registries,storage_observer=storage_observer,original_parity_guard=original_parity_guard)
        if launch.controller.plan_hash!=saved.plan_hash:
            raise ValueError('Restored execution differs from its immutable stored plan')
        claim.check();source_guard()
        return launch

    def __init__(self,query,config,dataset_id,plans,source_hash,claim,client_factory,source_guard,
                 capacity_guard,dictionary_claim_factory,market_runtime,*,prerequisite_registries=(),storage_observer=None,original_parity_guard=None):
        if not callable(capacity_guard):raise ValueError('Execution requires qualified capacity admission')
        self.claim=claim;self.source_guard=source_guard;self.capacity_guard=capacity_guard
        self.query=query
        self.config=config;self.plans=tuple(plans)
        if original_parity_guard is None and any(p.unit.product=='fundamentals' and 'original_specification_hash' in p.arguments for p in self.plans):
            from .sec_scope_specification import StoredGuard
            original_parity_guard=StoredGuard(query,self.plans,claim,source_guard)
        self.reference_handlers=ReferenceHandlers(dataset_id,config.database,claim,client_factory,source_guard,self.plans,
            dictionary_claim_factory=dictionary_claim_factory,original_parity_guard=original_parity_guard)
        for plan in self.plans:
            if plan.unit.product=='universe' and tuple(plan.arguments.get('issuer_keys',()))!=config.issuer_keys:
                raise ValueError('Universe issuer cohort differs from the frozen configuration')
            if plan.unit.product=='universe' and plan.arguments.get('conid_review_hash')!=config.conid_review_hash:
                raise ValueError('Universe conid review differs from the frozen configuration')
        if (market_runtime.dataset!=dataset_id or market_runtime.database!=config.database
                or market_runtime.claim is not claim):
            raise ValueError('Market runtime ownership/dataset differs from execution launch')
        if any(p.unit.product=='calculation_recipe' for p in self.plans):
            frozen=runtime_plan.resolve(config,self.plans)
            if (market_runtime.recipe_hash!=frozen['recipe_hash'] or market_runtime.definition!=frozen['definition']
                    or market_runtime.rules!=frozen['rules']):
                raise ValueError('Market runtime differs from the frozen plan descriptor')
        registries=(CanonicalSourceHandlers(claim,client_factory,source_guard),
            runtime_plan.Handlers(config,self.plans,claim,source_guard,market_runtime),
            ReferenceAuthorityHandlers(dataset_id,config.database,claim,client_factory,source_guard),
            ReferenceImportHandlers(dataset_id,config.database,claim,client_factory,source_guard),
            V7SeedHandlers(config.database,claim,client_factory,source_guard,market_runtime),
            IndicatorSeedHandlers(config.database,claim,client_factory,source_guard,market_runtime),
            RebuildOriginHandlers(config.database,claim,client_factory,source_guard,market_runtime,self.plans),
            checkpoint_backfill.Handlers(config.database,claim,client_factory,source_guard,market_runtime,self.plans),
            PreparedSecSourceHandlers(claim,client_factory,source_guard),
            PreparedShortSourceHandlers(dataset_id,config.database,claim,client_factory,source_guard),
            MarketHandlers(dataset_id,config.database,claim,client_factory,source_guard,
                        market_runtime.request,market_runtime.engine,storage_observer=storage_observer),
            self.reference_handlers,
            ActivityHandlers(dataset_id,config.database,claim,client_factory,source_guard,self.plans),
            UniverseHandlers(dataset_id,config.database,claim,client_factory,dictionary_claim_factory,source_guard),
            *prerequisite_registries)
        producers,verifiers=combine_registries(registries)
        self.controller=Campaign(query,config.database,claim,dataset_id,config,self.plans,source_hash,
            source_guard,producers,verifiers)

    def admit(self):
        self.claim.check();self.source_guard()
        self.controller.check_plan()
        if not self.config.database.startswith('certified_research_test_'):
            from .reference_producer import require_original_parity
            for plan in self.plans:
                if plan.unit.product=='fundamentals':
                    self.claim.check();self.source_guard()
                    require_original_parity(self.reference_handlers.scope(plan,{}),self.reference_handlers.original_parity_guard)
                    self.claim.check();self.source_guard()
        if any(p.unit.product=='calculation_recipe' for p in self.plans):runtime_plan.resolve(self.config,self.plans)
        installation.verify(self.query,self.config.database,require_tables=True)
        certificate=self.capacity_guard(self.controller.plan_hash,self.plans)
        if not isinstance(certificate,dict) or certificate.get('status')!='admitted':
            raise ValueError('Qualified storage admission blocked execution')
        if certificate.get('plan_hash')!=self.controller.plan_hash:
            raise ValueError('Storage admission is for another execution plan')
        proof=dict(certificate);fingerprint=proof.pop('certificate_hash',None)
        if fingerprint!=digest(proof):raise ValueError('Storage admission certificate fingerprint differs')
        self.claim.check();self.source_guard()
        return certificate

    def run(self,*,stop=None,progress=None):
        self.admit()
        return self.controller.run(stop=stop,progress=progress)

    def verify(self):
        # Verification is read-only and does not consume future write headroom.
        self.claim.check();self.source_guard()
        installation.verify(self.query,self.config.database,require_tables=True)
        return self.controller.verify()
