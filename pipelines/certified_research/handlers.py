"""Concrete reference producers/verifiers for immutable campaign units.

A client is owned by each call; workers never share cancellation identities.
The launcher supplies frozen source validation and writer ownership. No plan
argument is interpreted as a function, shell command or arbitrary SQL.
"""
from __future__ import annotations

from datetime import date

from . import facts,reference_producer,float_data,producer,price_inputs,universe,activity,source
from .universe_publication import UniversePublisher
from .contracts import digest
from .execution import Unit
from .publication import Publisher,Scope
from .reference_publication import ReferenceScope,ReferencePublisher

PRODUCTS=('fundamentals','derived_fundamentals','short_volume','short_interest','regular_prices','float_supply','short_pressure')
MARKET_PRODUCTS=('market_aggregate','technical','v7')


class CanonicalSourceHandlers:
    """Recheck prepared daily source/reporting certificates without writing."""
    def __init__(self,claim,client_factory,source_guard):
        self.claim=claim;self.clients=client_factory;self.guard=source_guard

    def execute(self,plan,parents,stop=None):
        if stop is not None and stop.is_set():raise InterruptedError('Source verification stopped')
        if plan.unit.product!='canonical_source' or parents or set(plan.arguments)!={'day','source_hash'}:
            raise ValueError('Invalid canonical source verification unit')
        day=date.fromisoformat(plan.arguments['day']);client=self.clients()
        try:
            self.claim.check();self.guard()
            canonical=source.inventory(client.query,day,day,sessions=(day,))
            reporting=source.reporting_inventory(client.query,canonical)
            current=source.market_day_hashes(canonical,reporting)[day]
            if current!=plan.arguments['source_hash']:
                raise ValueError('Frozen daily canonical/reporting authority changed')
            self.claim.check();self.guard()
            return dict(product='canonical_source',day=str(day),source_hash=current,output_hash=current)
        finally:client.close()

    def producer(self,plan,parents,stop):return self.execute(plan,parents,stop)
    def verifier(self,plan,parents):return self.execute(plan,parents)
    def registry(self):return {'canonical_source':self.producer},{'canonical_source':self.verifier}


class PreparedShortSourceHandlers:
    """Certify already acquired short inputs, without implicit provider access."""
    def __init__(self,dataset_id,database,claim,client_factory,source_guard):
        self.dataset=dataset_id;self.database=database;self.claim=claim
        self.clients=client_factory;self.guard=source_guard

    def execute(self,plan,parents,stop=None):
        from . import short_sql,schema
        if stop is not None and stop.is_set():raise InterruptedError('Short source verification stopped')
        family=plan.unit.product.removesuffix('_acquisition')
        if plan.unit.product not in ('short_volume_acquisition','short_interest_acquisition') or set(plan.arguments)!={'entity_key','start','end','source_hash','coverage_hash'}:
            raise ValueError('Invalid prepared short source unit')
        args=plan.arguments;start=date.fromisoformat(args['start']);end=date.fromisoformat(args['end'])
        client=self.clients()
        try:
            self.claim.check();self.guard()
            schema.verify_auxiliary_storage(client.query,'q_live',
                ('market_'+family+'_v1','market_reference_publication_coverage_v1'))
            coverage=short_sql.publication_coverage(client.query,start,end,family)
            if not coverage['ready']:raise ValueError('Short acquisition coverage is missing required measurement sessions')
            if coverage['coverage_hash']!=args['coverage_hash']:raise ValueError('Frozen short acquisition coverage changed')
            rows=client.query(short_sql.short_audit(self.database,self.dataset,int(args['entity_key']),start,end,family))
            if len(rows)!=1:raise ValueError('Short source audit is ambiguous')
            audit=rows[0]
            if int(audit['n'])!=int(audit['unique_events']) or any(int(audit[k]) for k in
                    ('unknown_public_time','invalid_public_time','missing_evidence')):
                raise ValueError('Short source lacks unambiguous original public-time evidence')
            if audit['source_hash']!=args['source_hash']:raise ValueError('Frozen short observations changed')
            self.claim.check();self.guard()
            return dict(product=plan.unit.product,entity_key=int(args['entity_key']),source_hash=audit['source_hash'],
                coverage_hash=coverage['coverage_hash'],output_hash=digest(dict(product=plan.unit.product,**args)))
        finally:client.close()

    def producer(self,plan,parents,stop):return self.execute(plan,parents,stop)
    def verifier(self,plan,parents):return self.execute(plan,parents)
    def registry(self):
        products=('short_volume_acquisition','short_interest_acquisition')
        return {p:self.producer for p in products},{p:self.verifier for p in products}


class PreparedSecSourceHandlers:
    """Verify retained SEC inputs, with no SEC or market-provider requests."""
    def __init__(self,claim,client_factory,source_guard):
        self.claim=claim;self.clients=client_factory;self.guard=source_guard

    def execute(self,plan,parents,stop=None):
        from . import reference_sql,schema
        if stop is not None and stop.is_set():raise InterruptedError('SEC source verification stopped')
        args=plan.arguments
        if plan.unit.product!='sec_acquisition' or set(args)!={'entity_key','cik','start','end','source_hash'}:
            raise ValueError('Invalid prepared SEC source unit')
        client=self.clients()
        try:
            self.claim.check();self.guard()
            schema.verify_auxiliary_storage(client.query,'q_live',('sec_filing_v3','sec_xbrl_company_fact_v3'))
            schema.verify_auxiliary_storage(client.query,'sec_core',('sec_bulk_mirror_xbrl_fact_v3',))
            from . import sec_coverage
            sec_coverage.require_ready(client.query,date.fromisoformat(args['start']),date.fromisoformat(args['end']))
            evidence=reference_sql.sec_source_evidence(client.query,int(args['entity_key']),args['cik'],
                date.fromisoformat(args['start']),date.fromisoformat(args['end']))
            if evidence['source_hash']!=args['source_hash']:raise ValueError('Frozen SEC public input evidence changed')
            self.claim.check();self.guard()
            return dict(product='sec_acquisition',**evidence,output_hash=evidence['source_hash'])
        finally:client.close()

    def producer(self,plan,parents,stop):return self.execute(plan,parents,stop)
    def verifier(self,plan,parents):return self.execute(plan,parents)
    def registry(self):return {'sec_acquisition':self.producer},{'sec_acquisition':self.verifier}


class V7SeedHandlers:
    """Explicit compatible causal checkpoint locators; no historical replay."""
    def __init__(self,database,claim,client_factory,source_guard,market_runtime):
        self.database=database;self.claim=claim;self.clients=client_factory
        self.guard=source_guard;self.runtime=market_runtime

    def execute(self,plan,parents,stop=None):
        from . import v7
        if stop is not None and stop.is_set():raise InterruptedError('V7 seed verification stopped')
        if plan.unit.product!='v7_seed' or set(plan.arguments)!={'dependency'}:
            raise ValueError('V7 seed requires an explicit causal dependency locator')
        parent=plan.arguments['dependency'];day=date.fromisoformat(str(parent['session_date']))
        source_hash=self.runtime.sources.get(day)
        if source_hash is None:raise ValueError('V7 seed source day is outside frozen canonical/reporting context')
        client=self.clients()
        try:
            self.claim.check();self.guard()
            dependency,_=v7.verify_seed(client.query,self.database,self.claim,parent,
                self.runtime.recipe_hash,source_hash,self.runtime.rules)
            self.claim.check();self.guard();return dependency
        finally:client.close()

    def producer(self,plan,parents,stop):return self.execute(plan,parents,stop)
    def verifier(self,plan,parents):return self.execute(plan,parents)
    def registry(self):return {'v7_seed':self.producer},{'v7_seed':self.verifier}


class IndicatorSeedHandlers(V7SeedHandlers):
    """Restore only the eight persisted frames from an explicit state locator."""
    def execute(self,plan,parents,stop=None):
        from . import indicator_state
        if stop is not None and stop.is_set():raise InterruptedError('Indicator seed verification stopped')
        if plan.unit.product!='technical_seed' or set(plan.arguments)!={'dependency'}:
            raise ValueError('Indicator seed requires an explicit causal dependency locator')
        parent=plan.arguments['dependency'];receipt=parent.get('state_receipt',parent)
        context=parent.get('state_context',{});day=date.fromisoformat(str(receipt['session_date']))
        source_hash=self.runtime.sources.get(day)
        if (source_hash is None or context.get('recipe_hash')!=self.runtime.recipe_hash
                or context.get('source_hash')!=source_hash or context.get('rules_hash')!=digest(self.runtime.rules)):
            raise ValueError('Indicator seed differs from frozen recipe/source/rules context')
        scope=Scope(str(receipt['dataset_id']),day,int(receipt['listing_key']),'technical_state',receipt['parent_hash'])
        client=self.clients()
        try:
            self.claim.check();self.guard()
            loaded=indicator_state.load(client.query,self.database,self.claim,scope,self.runtime.recipe_hash,
                context['predecessor_hash'],source_hash=source_hash,rules_hash=digest(self.runtime.rules))
            if loaded is None or loaded['receipt']!=receipt:raise ValueError('Indicator seed differs from certified state authority')
            self.claim.check();self.guard()
            return dict(receipt,state_receipt=receipt,state_context=dict(context))
        finally:client.close()

    def registry(self):return {'technical_seed':self.producer},{'technical_seed':self.verifier}


class ReferenceAuthorityHandlers:
    """Reuse qualified current-dataset reference scopes without fabrication."""
    PRODUCTS=('corporate_actions','float_allocation','reported_supply')
    def __init__(self,dataset_id,database,claim,client_factory,source_guard):
        self.dataset=dataset_id;self.database=database;self.claim=claim;self.clients=client_factory;self.guard=source_guard

    def execute(self,plan,parents,stop=None):
        from . import schema
        if stop is not None and stop.is_set():raise InterruptedError('Reference authority verification stopped')
        args=plan.arguments
        if plan.unit.product not in self.PRODUCTS or set(args)!={'entity_key','start','end','receipt'}:
            raise ValueError('Reference prerequisite requires an explicit certified receipt locator')
        row=args['receipt']
        scope=ReferenceScope(self.dataset,2,int(args['entity_key']),plan.unit.product,
            date.fromisoformat(args['start']),date.fromisoformat(args['end']),row['parent_hash'],facts.catalog_hash())
        if (str(row['dataset_id'])!=self.dataset or int(row['entity_kind'])!=2
                or int(row['entity_key'])!=scope.entity_key or row['family']!=scope.family
                or str(row['range_start'])!=str(scope.start) or str(row['range_end'])!=str(scope.end)
                or row['catalog_hash']!=scope.catalog_hash):
            raise ValueError('Reference authority identity/range/catalog differs; cross-dataset import requires a separate certified producer')
        client=self.clients()
        try:
            self.claim.check();self.guard()
            schema.verify_storage(client.query,self.database,require_tables=True)
            actual=ReferencePublisher(client.query,self.database,self.claim).receipt(scope)
            if actual is None or actual!=row:raise ValueError('Reference prerequisite differs from independently verified stored authority')
            self.claim.check();self.guard();return actual
        finally:client.close()

    def producer(self,plan,parents,stop):return self.execute(plan,parents,stop)
    def verifier(self,plan,parents):return self.execute(plan,parents)
    def registry(self):return {p:self.producer for p in self.PRODUCTS},{p:self.verifier for p in self.PRODUCTS}


class ActivityHandlers:
    """Concurrent SQL windows consuming shared current/context liquidity."""
    def __init__(self,dataset_id,database,claim,client_factory,source_guard,plans):
        self.dataset=dataset_id;self.database=database;self.claim=claim;self.client_factory=client_factory
        self.source_guard=source_guard;self.plans={p.unit.key:p for p in plans}

    def inputs(self,plan,parents):
        if plan.unit.product!='activity':raise ValueError('Wrong activity product')
        scopes=[]
        for key,result in parents.items():
            parent=self.plans[key]
            if parent.unit.product!='market_aggregate':raise ValueError('Activity requires shared market parents')
            row=result['receipts']['broker_100ms'];args=parent.arguments
            scope=Scope(self.dataset,date.fromisoformat(args['day']),int(args['listing_key']),'broker_100ms',row['parent_hash'])
            if (row['dataset_id']!=self.dataset or str(row['session_date'])!=str(scope.session_date)
                    or int(row['listing_key'])!=scope.listing_key or row['product']!='broker_100ms'):
                raise ValueError('Activity receipt differs from its planned identity/date')
            scopes.append(scope)
        return scopes

    def execute(self,plan,parents,*,write,stop=None):
        if stop is not None and stop.is_set():raise InterruptedError('Activity stopped before execution')
        client=self.client_factory()
        try:
            self.claim.check();self.source_guard();args=plan.arguments;windows=tuple(args['windows'])
            scopes=self.inputs(plan,parents);verified=activity.source_parents(client.query,self.database,self.claim,scopes)
            scope=Scope(self.dataset,date.fromisoformat(args['day']),int(args['listing_key']),'activity',activity.parent_hash(windows,verified))
            if write:return activity.run(scope,windows,scopes,client.query,self.claim,self.source_guard,database=self.database)
            # Validate completeness/identity even when there is already a receipt.
            activity.projection(scope,'00000000-0000-0000-0000-000000000001',windows,verified,self.database)
            return Publisher(client.query,self.database,self.claim).receipt(scope)
        finally:client.close()

    def producer(self,plan,parents,stop):return self.execute(plan,parents,write=True,stop=stop)
    def verifier(self,plan,parents):return self.execute(plan,parents,write=False)
    def registry(self):return {'activity':self.producer},{'activity':self.verifier}


class UniverseHandlers:
    """Gateway reconstruction and publication as one certified prerequisite."""
    def __init__(self,dataset_id,database,claim,client_factory,dictionary_claim_factory,source_guard):
        self.dataset=dataset_id;self.database=database;self.claim=claim;self.client_factory=client_factory
        self.dictionary_claim_factory=dictionary_claim_factory;self.source_guard=source_guard

    def execute(self,plan,*,write,stop=None):
        if plan.unit.product!='universe':raise ValueError('Wrong universe product')
        if stop is not None and stop.is_set():raise InterruptedError('Universe unit stopped before execution')
        client=self.client_factory();dictionary=None
        try:
            self.claim.check();self.source_guard();args=plan.arguments
            start=date.fromisoformat(args['context_start']);end=date.fromisoformat(args['end'])
            anchor=date.fromisoformat(args['anchor_date'])
            snapshot=universe.load(client.query,start,end,anchor)
            if snapshot['source_hash']!=args['source_hash']:raise ValueError('Frozen Gateway source epoch changed')
            if not snapshot['ready']:
                raise ValueError('Historical universe requires evidence repair: '+str(snapshot['rejection_counts']))
            dictionary=self.dictionary_claim_factory()
            publisher=UniversePublisher(client.query,self.database,self.dataset,self.claim,dictionary,self.source_guard)
            if write:return publisher.publish(snapshot,start,end,anchor)
            return publisher.receipt(snapshot)
        finally:
            if dictionary is not None:dictionary.close()
            client.close()

    def producer(self,plan,parents,stop):return self.execute(plan,write=True,stop=stop)
    def verifier(self,plan,parents):return self.execute(plan,write=False)
    def registry(self):return {'universe':self.producer},{'universe':self.verifier}


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
                or request.unit.ticker!=args['ticker']
                or (request.unit.source_ticker or request.unit.ticker)!=args.get('source_ticker',args['ticker'])):
            raise ValueError('Market request does not match immutable campaign scope')
        producer.validate_recipe(request,self.database,product=plan.unit.product)
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
            if plan.unit.product=='technical':
                from . import indicator_state
                return indicator_state.authority(request,bars,client.query,self.database,self.claim)
            if plan.unit.product=='v7':
                from . import v7
                return v7.authority(request,bars,client.query,self.database,self.claim)
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
    def __init__(self,dataset_id,database,claim,client_factory,source_guard,plans,*,dictionary_claim_factory=None):
        self.dataset=dataset_id;self.database=database;self.claim=claim;self.client_factory=client_factory
        self.source_guard=source_guard;self.plans={p.unit.key:p for p in plans}
        self.dictionary_claim_factory=dictionary_claim_factory

    def publication_query(self,query):
        def owned(sql,*args,**kwargs):
            if not sql.startswith(f'INSERT INTO {self.database}.research_provenance_v1 '):
                return query(sql,*args,**kwargs)
            if self.dictionary_claim_factory is None:
                if not self.database.startswith('certified_research_test_'):
                    raise ValueError('Reference publication requires global dictionary ownership')
                return query(sql,*args,**kwargs)
            self.claim.check();dictionary=self.dictionary_claim_factory()
            try:
                if getattr(dictionary,'dictionary_database',None)!=self.database:
                    raise ValueError('Reference dictionary ownership is for another database')
                dictionary.check();self.claim.check();self.source_guard()
                result=query(sql,*args,**kwargs)
                dictionary.check();self.claim.check()
                return result
            finally:dictionary.close()
        return owned

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
        from . import short_pressure
        args=plan.arguments;kind=1 if plan.unit.product in ('fundamentals','derived_fundamentals') else 2
        if plan.unit.product=='fundamentals' and parents:
            acquired=[receipt for receipt in parents.values() if receipt.get('product')=='sec_acquisition']
            if acquired or not self.database.startswith('certified_research_test_'):
                if len(acquired)!=1:raise ValueError('Fundamentals require one verified SEC input authority')
                receipt=acquired[0]
                if (receipt['source_hash']!=args['source_hash'] or int(receipt['entity_key'])!=int(args['entity_key'])
                        or int(receipt['cik'])!=int(args['cik']) or receipt['start']!=args['start'] or receipt['end']!=args['end']):
                    raise ValueError('Fundamental SEC input identity, range or fingerprint differs')
        if plan.unit.product=='derived_fundamentals':
            if len(parents)!=1:raise ValueError('Derived fundamentals require one certified reported parent')
            parent=next(iter(parents.values()));parent_hash=reference_producer.derived_parent_hash(parent)
        elif plan.unit.product=='short_pressure':
            if len(parents)!=1:raise ValueError('Short pressure requires one certified short-volume parent')
            key=next(iter(parents))
            if self.plans[key].unit.product!='short_volume':raise ValueError('Wrong short-pressure parent product')
            parent_hash=short_pressure.parent_hash(parents[key],tuple(args.get('windows',(20,))))
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
        if self.dictionary_claim_factory is None and not self.database.startswith('certified_research_test_'):
            raise ValueError('Reference publication requires global dictionary ownership')
        client=self.client_factory()
        try:
            query=self.publication_query(client.query);scope=self.scope(plan,parents);args=plan.arguments
            if plan.unit.product=='fundamentals':
                names=args.get('metrics')
                selected=tuple(m for m in facts.catalog() if m.tags and (names is None or m.name in names))
                if not selected or (names is not None and set(names)!={m.name for m in selected}):
                    raise ValueError('Reported metric selection is empty or not in the frozen catalog')
                verified_input=any(p.get('product')=='sec_acquisition' for p in parents.values())
                reference_producer.run_sec(scope,args['cik'],selected,query,self.claim,self.source_guard,database=self.database,
                    expected_source_hash=args['source_hash'] if verified_input else None)
            elif plan.unit.product=='derived_fundamentals':
                key=next(iter(parents));parent_plan=self.plans[key]
                if parent_plan.unit.product!='fundamentals':raise ValueError('Wrong derived parent product')
                parent_scope=self.scope(parent_plan,{})
                reference_producer.run_derived(scope,parent_scope,query,self.claim,self.source_guard,database=self.database)
            elif plan.unit.product in ('short_volume','short_interest'):
                reference_producer.run_short(scope,query,self.claim,self.source_guard,database=self.database)
            elif plan.unit.product=='short_pressure':
                from . import short_pressure
                key=next(iter(parents))
                short_pressure.run(scope,self.received_scope(self.plans[key],parents[key]),
                    tuple(args.get('windows',(20,))),query,self.claim,self.source_guard,database=self.database)
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
