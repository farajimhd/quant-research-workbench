"""Concrete reference producers/verifiers for immutable campaign units.

A client is owned by each call; workers never share cancellation identities.
The launcher supplies frozen source validation and writer ownership. No plan
argument is interpreted as a function, shell command or arbitrary SQL.
"""
from __future__ import annotations

from datetime import date

from . import facts,reference_producer,float_data
from .reference_publication import ReferenceScope,ReferencePublisher

PRODUCTS=('fundamentals','derived_fundamentals','short_volume','short_interest','float_supply')


class ReferenceHandlers:
    def __init__(self,dataset_id,database,claim,client_factory,source_guard,plans):
        self.dataset=dataset_id;self.database=database;self.claim=claim;self.client_factory=client_factory
        self.source_guard=source_guard;self.plans={p.unit.key:p for p in plans}

    def scope(self,plan,parents):
        args=plan.arguments;kind=1 if plan.unit.product in ('fundamentals','derived_fundamentals') else 2
        if plan.unit.product=='derived_fundamentals':
            if len(parents)!=1:raise ValueError('Derived fundamentals require one certified reported parent')
            parent=next(iter(parents.values()));parent_hash=reference_producer.derived_parent_hash(parent)
        elif plan.unit.product=='float_supply':
            families={self.plans[k].unit.product:v for k,v in parents.items()}
            if len(families)!=len(parents):raise ValueError('Competing float parent families')
            parent_hash=float_data.parent_hash(families)
        else:
            parent_hash=args['source_hash']
        return ReferenceScope(self.dataset,kind,int(args['entity_key']),plan.unit.product,
            date.fromisoformat(args['start']),date.fromisoformat(args['end']),parent_hash,facts.catalog_hash())

    def producer(self,plan,parents,stop):
        if stop.is_set():raise InterruptedError('Reference unit stopped before execution')
        if plan.unit.product not in PRODUCTS:raise ValueError('Unregistered reference product')
        client=self.client_factory()
        try:
            query=client.query;scope=self.scope(plan,parents);args=plan.arguments
            if plan.unit.product=='fundamentals':
                names=args.get('metrics')
                selected=tuple(m for m in facts.catalog() if m.tags and (names is None or m.name in names))
                if not selected or (names is not None and set(names)!={m.name for m in selected}):
                    raise ValueError('Reported metric selection is empty or not in the frozen catalog')
                reference_producer.run_sec(scope,args['cik'],selected,query,self.claim,self.source_guard,database=self.database)
            elif plan.unit.product=='derived_fundamentals':
                key=next(iter(parents));parent_plan=self.plans[key]
                if parent_plan.unit.product!='fundamentals':raise ValueError('Wrong derived parent product')
                parent_scope=self.scope(parent_plan,{})
                reference_producer.run_derived(scope,parent_scope,query,self.claim,self.source_guard,database=self.database)
            elif plan.unit.product in ('short_volume','short_interest'):
                reference_producer.run_short(scope,query,self.claim,self.source_guard,database=self.database)
            elif plan.unit.product=='float_supply':
                scopes={self.plans[key].unit.product:self.scope(self.plans[key],{}) for key in parents}
                reference_producer.run_float(scope,int(args['issuer_key']),scopes,query,self.claim,self.source_guard,database=self.database)
        finally:client.close()

    def verifier(self,plan,parents):
        client=self.client_factory()
        try:
            self.claim.check();self.source_guard()
            return ReferencePublisher(client.query,self.database,self.claim).receipt(self.scope(plan,parents))
        finally:client.close()

    def registry(self):
        return ({kind:self.producer for kind in PRODUCTS},{kind:self.verifier for kind in PRODUCTS})
