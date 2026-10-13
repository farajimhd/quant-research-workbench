"""Immutable producer predecessor selection over native attested CH receipts."""
from datetime import date
from types import MappingProxyType

from pipelines.market_sip.events.market_day_checkpoint import CompletedMarketDayLedger, UNIT, SEED
from src.trading_runtime.arte_market_day_certification import family_hash, _typed_readback_row
from src.trading_runtime.arte_market_day_cold_preflight import MarketDayColdAudit
from src.trading_runtime.arte_market_day_keeper import require_attested_inventory


class MarketDayPredecessorLedger:
    """The existing numerical seed reader's interface, without SQLite discovery.

    The current producer may use its own completed checkpoints. Other builds
    require fully audited products and final Keeper attestations. A missing
    declaration is a blocker, never an inferred first-bar/bootstrap policy.
    """
    def __init__(self, current, definition, parents):
        from scripts.build_market_day import digest
        if (type(current) is not CompletedMarketDayLedger or type(definition) is not dict
                or type(parents) is not tuple or len(parents) > 256
                or digest(definition) != current.build_id.split('-', 1)[0]):
            raise ValueError('Predecessor registry lacks its immutable current definition')
        self.current = current
        self.profile = (definition['calculation_source'], definition['rules_hash'], definition['version'])
        planned = {(row['source_date'], row['ticker']) for row in definition['plan']['units']}
        if set(current.scopes) != planned:
            raise ValueError('Current checkpoint population differs from its immutable source plan')
        predecessors = dict(definition['plan']['predecessors'])
        for day, _ in planned:
            previous = predecessors[day]
            if previous is not None and (type(previous) is not str
                    or date.fromisoformat(previous).isoformat() != previous or previous >= day):
                raise ValueError('Declared predecessor violates chronological source ordering')
        self.predecessors = MappingProxyType(predecessors)
        ledgers, candidates = {current.build_id: current}, {}
        for packet in parents:
            if type(packet) is not tuple or len(packet) != 2:
                raise ValueError('Declared predecessor requires its audit and typed header')
            audit, supplied_header = packet
            if (type(audit) is not MarketDayColdAudit or not audit.certificate_parts_on_ssd
                    or not audit.source_authority_verified or not audit.source_storage_verified
                    or not audit.market_products_verified or audit.certificate.session_families is None):
                raise ValueError('Declared predecessor lacks complete native source/product audit')
            certificate = audit.certificate
            require_attested_inventory(audit.attestation, certificate.fence)
            header = _typed_readback_row('market_day_build_header_v1', supplied_header)
            if (family_hash([header]) != certificate.fence['header_hash']
                    or header['build_id'] != certificate.build_id
                    or (header['calculation_source_hash'], header['rules_hash'], header['version']) != self.profile
                    or certificate.build_id in ledgers):
                raise ValueError('Declared predecessor header differs from its approved source profile')
            units = tuple(row for families in certificate.session_families.values() for row in families[UNIT])
            seeds = tuple(row for families in certificate.session_families.values() for row in families[SEED])
            if (family_hash(units) != certificate.fence['stage_hash']
                    or family_hash(seeds) != certificate.fence['seed_hash']):
                raise ValueError('Declared predecessor receipt content differs from its final fence')
            ledger = CompletedMarketDayLedger(certificate.build_id, certificate.scopes, units, seeds)
            ledgers[certificate.build_id] = ledger
            for day, ticker in ledger.scopes:
                unit = ledger.unit(certificate.build_id, day, ticker, 'technical')
                seed = ledger.seed(certificate.build_id, day, ticker)
                if unit is None or seed is None or unit['attempt_id'] != seed['attempt_id']:
                    raise ValueError('Declared predecessor lacks joined technical and seed receipts')
                key = (day, ticker)
                if key in candidates:
                    raise ValueError('Multiple declared predecessors require an explicit immutable selection')
                candidates[key] = MappingProxyType(dict(build_id=certificate.build_id,
                    attempt_id=unit['attempt_id'], source_hash=unit['source_hash']))
        self.ledgers = MappingProxyType(ledgers)
        self.selected = MappingProxyType(candidates)

    def predecessor_for(self, day, ticker):
        day = day.isoformat() if type(day) is date else day
        if (day, ticker) not in self.current._scope_keys:
            raise ValueError('Requested seed window is outside the immutable source plan')
        return self.predecessors[day]

    def for_target(self, day, ticker):
        """Bind the legacy numerical reader to one causal target session."""
        day = day.isoformat() if type(day) is date else day
        self.predecessor_for(day, ticker)
        registry = self
        class TargetLedger:
            def unit(self, build_id, previous, symbol, stage):
                previous = previous.isoformat() if type(previous) is date else previous
                if symbol != ticker or previous >= day:
                    raise ValueError('Predecessor receipt is not before its target session')
                return registry.unit(build_id, previous, symbol, stage)
            def seed(self, build_id, previous, symbol):
                previous = previous.isoformat() if type(previous) is date else previous
                if symbol != ticker or previous >= day:
                    raise ValueError('Predecessor seed is not before its target session')
                return registry.seed(build_id, previous, symbol)
            def candidates(self, previous, symbol, *profile):
                previous = previous.isoformat() if type(previous) is date else previous
                if symbol != ticker or previous >= day:
                    raise ValueError('Predecessor candidate is not before its target session')
                return registry._candidates(previous, symbol, *profile)
        return TargetLedger()

    def unit(self, build_id, day, ticker, stage):
        ledger = self.ledgers.get(build_id)
        if ledger is None:
            raise ValueError('Predecessor build is not declared')
        return ledger.unit(build_id, day, ticker, stage)

    def seed(self, build_id, day, ticker):
        ledger = self.ledgers.get(build_id)
        if ledger is None:
            raise ValueError('Predecessor build is not declared')
        return ledger.seed(build_id, day, ticker)

    def _candidates(self, day, ticker, build_id, database, calculation_source, rules_hash, version):
        day = day.isoformat() if type(day) is date else day
        if (build_id != self.current.build_id or database != 'arte'
                or (calculation_source, rules_hash, version) != self.profile):
            raise ValueError('Predecessor lookup changed its immutable producer profile')
        if (day, ticker) in self.current._scope_keys:
            unit = self.current.unit(build_id, day, ticker, 'technical')
            seed = self.current.seed(build_id, day, ticker)
            if unit is not None and seed is not None:
                if unit['attempt_id'] != seed['attempt_id']:
                    raise ValueError('Current predecessor checkpoint has incompatible seed lineage')
                return [dict(build_id=build_id, attempt_id=unit['attempt_id'], source_hash=unit['source_hash'])]
        selected = self.selected.get((day, ticker))
        if selected is None:
            raise ValueError('Predecessor scope is undeclared; bootstrap must not be inferred')
        return [dict(selected)]
