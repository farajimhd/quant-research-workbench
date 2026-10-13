"""Concrete reference producers/verifiers for immutable campaign units.

A client is owned by each call; workers never share cancellation identities.
The launcher supplies frozen source validation and writer ownership. No plan
argument is interpreted as a function, shell command or arbitrary SQL.
"""
from __future__ import annotations

from datetime import date

from . import facts,reference_producer,float_data,producer,price_inputs
from .contracts import digest
from .execution import Unit
from .publication import Publisher,Scope
from .reference_publication import ReferenceScope,ReferencePublisher

PRODUCTS=('fundamentals','derived_fundamentals','short_volume','short_interest','regular_prices','float_supply')
MARKET_PRODUCTS=('market_aggregate','technical','v7')


class MarketHandlers:
    """One source aggregate followed by concurrent consumers in one global pool.

    Request/engine factories must resolve certified context, calendar and rule
    contracts. They are launcher-owned functions, never serialized plan code.
    Aggregation certifies both bars and liquidity before releasing consumers;
    staging data and successful SQL are never campaign authority.
    """
    def __init__(self,dataset_id,database,claim,client_factory,source_guard,request_factory,engine_factory,
                 *,fixture_source=None):
        if fixture_source is not None and not database.startswith('certified_research_test_'):
            raise ValueError('Fixture source overrides are forbidden outside isolated test namespaces')
        self.dataset=dataset_id;self.database=database;self.claim=claim;self.client_factory=client_factory
        self.source_guard=source_guard;self.request_factory=request_factory;self.engine_factory=engine_factory
        self.fixture_source=fixture_source

    def request(self,plan,parents):
        if plan.unit.product not in MARKET_PRODUCTS:raise ValueError('Unregistered market product')
        request=self.request_factory(plan,parents);args=plan.arguments
        if (not isinstance(request,producer.MarketRequest) or request.unit.dataset_id!=self.dataset
                or str(request.unit.day)!=args['day'] or request.unit.listing_key!=args['listing_key']
                or request.unit.ticker!=args['ticker']):
            raise ValueError('Market request does not match immutable campaign scope')
        return request

    def receipts(self,request,query):
        publisher=Publisher(query,self.database,self.claim);base=request.unit
        found={kind:publisher.receipt(Scope(self.dataset,base.day,base.listing_key,kind,request.parent(kind)))
               for kind in ('bars','broker_100ms')}
        if any(r is None for r in found.values()):return None
        return dict(output_hash=digest({k:r['output_hash'] for k,r in sorted(found.items())}),receipts=found)

    @staticmethod
    def shared_parent(parents,combined):
        candidates=[r for r in parents.values() if 'receipts' in r]
        if len(candidates)!=1 or candidates[0]!=combined:
            raise ValueError('Consumer requires its exact certified shared market parent')

    def verifier(self,plan,parents):
        request=self.request(plan,parents);client=self.client_factory()
        try:
            self.claim.check();self.source_guard()
            combined=self.receipts(request,client.query)
            if plan.unit.product=='market_aggregate' or combined is None:return combined
            self.shared_parent(parents,combined)
            bars=combined['receipts']['bars'];base=request.unit
            scope=Scope(self.dataset,base.day,base.listing_key,plan.unit.product,
                        request.parent(plan.unit.product,(bars['output_hash'],)))
            return Publisher(client.query,self.database,self.claim).receipt(scope)
        finally:client.close()

    def producer(self,plan,parents,stop):
        if stop.is_set():raise InterruptedError('Market unit stopped before execution')
        request=self.request(plan,parents)
        _,handler=producer.market_execution(request,self.client_factory,self.claim,self.source_guard,
            lambda:self.engine_factory(plan,parents),database=self.database,
            query_memory_bytes=plan.unit.memory_bytes,fixture_source=self.fixture_source)
        if plan.unit.product=='market_aggregate':
            stage=handler(Unit('aggregate','aggregate'),{},stop)
            # Low-cost publication follows the shared scan; no nested executor.
            for key,kind in (('bars','bars'),('liquidity','broker_100ms')):
                handler(Unit(key,kind),{'aggregate':stage},stop)
        else:
            client=self.client_factory()
            try:combined=self.receipts(request,client.query)
            finally:client.close()
            if combined is None:raise ValueError('Certified shared bars/liquidity are missing')
            self.shared_parent(parents,combined)
            handler(Unit(plan.unit.product,plan.unit.product),{'bars':combined['receipts']['bars']},stop)

    def registry(self):
        return ({kind:self.producer for kind in MARKET_PRODUCTS},{kind:self.verifier for kind in MARKET_PRODUCTS})


class ReferenceHandlers:
    def __init__(self,dataset_id,database,claim,client_factory,source_guard,plans):
        self.dataset=dataset_id;self.database=database;self.claim=claim;self.client_factory=client_factory
        self.source_guard=source_guard;self.plans={p.unit.key:p for p in plans}

    def bar_parents(self,parents):
        if not parents or len(parents)>1000:raise ValueError('Regular prices require bounded shared market parents')
        result=[]
        for key,combined in parents.items():
            plan=self.plans[key];args=plan.arguments
            if plan.unit.product!='market_aggregate':raise ValueError('Wrong regular-price parent product')
            receipt=combined['receipts']['bars']
            scope=Scope(self.dataset,date.fromisoformat(args['day']),int(args['listing_key']),'bars',receipt['parent_hash'])
            if (receipt['dataset_id']!=scope.dataset_id or str(receipt['session_date'])!=str(scope.session_date)
                    or int(receipt['listing_key'])!=scope.listing_key or receipt['product']!='bars'):
                raise ValueError('Regular-price receipt does not match planned market scope')
            result.append((scope,receipt))
        result.sort(key=lambda pair:(pair[0].session_date,pair[0].listing_key))
        if len({p.session_date for p,_ in result})!=len(result):raise ValueError('Competing regular-price listing/day parents')
        return result

    def received_scope(self,plan,receipt):
        """Resolve dynamic parent lineage from certified output, not empty inputs."""
        args=plan.arguments;kind=1 if plan.unit.product in ('fundamentals','derived_fundamentals') else 2
        scope=ReferenceScope(self.dataset,kind,int(args['entity_key']),plan.unit.product,
            date.fromisoformat(args['start']),date.fromisoformat(args['end']),receipt['parent_hash'],facts.catalog_hash())
        if (receipt['dataset_id']!=scope.dataset_id or int(receipt['entity_kind'])!=scope.entity_kind
                or int(receipt['entity_key'])!=scope.entity_key or receipt['family']!=scope.family
                or str(receipt['range_start'])!=str(scope.start) or str(receipt['range_end'])!=str(scope.end)
                or receipt['catalog_hash']!=scope.catalog_hash):
            raise ValueError('Reference parent receipt does not match immutable campaign scope')
        return scope

    def scope(self,plan,parents):
        args=plan.arguments;kind=1 if plan.unit.product in ('fundamentals','derived_fundamentals') else 2
        if plan.unit.product=='derived_fundamentals':
            if len(parents)!=1:raise ValueError('Derived fundamentals require one certified reported parent')
            parent=next(iter(parents.values()));parent_hash=reference_producer.derived_parent_hash(parent)
        elif plan.unit.product=='regular_prices':
            parent_hash=price_inputs.parent_hash(self.bar_parents(parents))
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
            elif plan.unit.product=='regular_prices':
                reference_producer.run_regular_prices(scope,[s for s,_ in self.bar_parents(parents)],query,
                    self.claim,self.source_guard,database=self.database)
            elif plan.unit.product=='float_supply':
                scopes={self.plans[key].unit.product:self.received_scope(self.plans[key],receipt)
                        for key,receipt in parents.items()}
                reference_producer.run_float(scope,int(args['issuer_key']),scopes,query,self.claim,self.source_guard,database=self.database)
        finally:client.close()

    def verifier(self,plan,parents):
        client=self.client_factory()
        try:
            self.claim.check();self.source_guard()
            scope=self.scope(plan,parents)
            if plan.unit.product=='regular_prices':
                reference_producer.regular_price_parents(scope,[s for s,_ in self.bar_parents(parents)],
                    client.query,self.claim,database=self.database)
            return ReferencePublisher(client.query,self.database,self.claim).receipt(scope)
        finally:client.close()

    def registry(self):
        return ({kind:self.producer for kind in PRODUCTS},{kind:self.verifier for kind in PRODUCTS})
