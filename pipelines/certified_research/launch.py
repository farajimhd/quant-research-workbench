"""One execution controller for market, reference and acquisition registries."""
from .campaign import Campaign
from .handlers import MarketHandlers,ReferenceHandlers,ActivityHandlers,UniverseHandlers
from .contracts import digest


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
    def __init__(self,query,config,dataset_id,plans,source_hash,claim,client_factory,source_guard,
                 capacity_guard,dictionary_claim_factory,market_runtime,*,prerequisite_registries=()):
        if not callable(capacity_guard):raise ValueError('Execution requires qualified capacity admission')
        self.claim=claim;self.source_guard=source_guard;self.capacity_guard=capacity_guard
        self.config=config;self.plans=tuple(plans)
        if (market_runtime.dataset!=dataset_id or market_runtime.database!=config.database
                or market_runtime.claim is not claim):
            raise ValueError('Market runtime ownership/dataset differs from execution launch')
        registries=(MarketHandlers(dataset_id,config.database,claim,client_factory,source_guard,
                        market_runtime.request,market_runtime.engine),
            ReferenceHandlers(dataset_id,config.database,claim,client_factory,source_guard,self.plans,
                dictionary_claim_factory=dictionary_claim_factory),
            ActivityHandlers(dataset_id,config.database,claim,client_factory,source_guard,self.plans),
            UniverseHandlers(dataset_id,config.database,claim,client_factory,dictionary_claim_factory,source_guard),
            *prerequisite_registries)
        producers,verifiers=combine_registries(registries)
        self.controller=Campaign(query,config.database,claim,dataset_id,config,self.plans,source_hash,
            source_guard,producers,verifiers)

    def admit(self):
        self.claim.check();self.source_guard()
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
        return self.controller.verify()
