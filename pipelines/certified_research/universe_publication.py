"""Restart-safe membership publication, with globally owned compact dictionaries."""
from __future__ import annotations

from uuid import UUID
import re

from . import schema,universe
from .contracts import digest,identifier,literal
from .identity import ISSUER,SECURITY,LISTING,identity_key,ensure_identities,validate_intervals
from .publication import insert_rows


class UniversePublisher:
    def __init__(self,query,database,dataset_id,claim,dictionary_claim,source_guard):
        self.query=query;self.database=identifier(database);UUID(dataset_id);self.dataset=dataset_id
        self.claim=claim;self.dictionary_claim=dictionary_claim;self.source_guard=source_guard
        if getattr(dictionary_claim,'dictionary_database',None)!=database:
            raise ValueError('Universe publication requires global dictionary ownership')

    def guard(self):
        self.claim.check();self.dictionary_claim.check();self.source_guard()

    def expected(self,snapshot):
        if snapshot.get('ready') is not True or snapshot.get('rejected'):
            raise ValueError('Unresolved historical universe evidence blocks publication')
        if not re.fullmatch('[0-9a-f]{64}',snapshot.get('source_hash','')):
            raise ValueError('Universe source epoch is missing')
        intervals=validate_intervals(snapshot['intervals'])
        if not intervals:raise ValueError('No proven historical universe intervals')
        rows=tuple(i.row(self.dataset) for i in intervals)
        keys=[(r['listing_key'],r['valid_from']) for r in rows]
        if len(set(keys))!=len(keys):raise ValueError('Duplicate membership persistence keys')
        return rows

    def stored(self,expected):
        actual=self.query(f'SELECT * FROM {self.database}.research_membership_v1 '
            f'WHERE dataset_id=toUUID({literal(self.dataset)})')
        wanted={(r['listing_key'],r['valid_from']):r for r in expected};found={}
        for row in actual:
            row=dict(row,**{k:int(row[k]) for k in ('listing_key','security_key','issuer_key','conid','eligible')})
            key=(row['listing_key'],row['valid_from'])
            if key in found or wanted.get(key)!=row:
                raise ValueError('Membership changed, duplicated or contains unplanned intervals')
            found[key]=row
        return found

    def dictionaries(self,intervals,*,write):
        identities=tuple(sorted({pair for i in intervals for pair in
            ((LISTING,i.listing_id),(SECURITY,i.security_id),(ISSUER,i.issuer_id))}))
        for offset in range(0,len(identities),2048):
            self.guard();batch=identities[offset:offset+2048]
            if write:ensure_identities(self.query,self.database,self.dictionary_claim,batch)
            else:
                keys=','.join(f'({kind},{identity_key(kind,value)})' for kind,value in batch)
                source_ids=','.join(f'({kind},{literal(value)})' for kind,value in batch)
                rows=self.query(f'SELECT entity_kind,entity_key,source_id FROM {self.database}.research_identity_v1 '
                    f'WHERE (entity_kind,entity_key) IN ({keys}) OR (entity_kind,source_id) IN ({source_ids})')
                wanted={(kind,identity_key(kind,value)):value for kind,value in batch}
                found={(int(r['entity_kind']),int(r['entity_key'])):r['source_id'] for r in rows}
                if len(rows)!=len(found) or found!=wanted:raise ValueError('Universe identity dictionary is missing or changed')

    def provenance(self,expected,intervals):
        hashes=sorted({r['evidence_hash'] for r in expected})
        records={i.evidence_hash:i.listing_id for i in intervals}
        if len(records)!=len(hashes) or any(records[i.evidence_hash]!=i.listing_id for i in intervals):
            raise ValueError('Universe provenance assignments are ambiguous')
        for offset in range(0,len(hashes),1024):
            ids=','.join(literal(h) for h in hashes[offset:offset+1024])
            wanted=','.join(f'({literal(h)},{literal(records[h])})' for h in hashes[offset:offset+1024])
            rows=self.query(f'SELECT evidence_hash,count() n,countIf(source_system!=\'reference_gateway\' '
                f'OR (evidence_hash,source_record) NOT IN ({wanted}) '
                f'OR source_hash!=evidence_hash OR lower(hex(SHA256(proof)))!=evidence_hash) bad '
                f'FROM {self.database}.research_provenance_v1 WHERE evidence_hash IN ({ids}) GROUP BY evidence_hash')
            found={r['evidence_hash'] for r in rows if int(r['n'])==1 and int(r['bad'])==0}
            if found!=set(hashes[offset:offset+1024]):raise ValueError('Universe source provenance is missing or changed')

    def receipt(self,snapshot):
        self.guard();schema.verify_storage(self.query,self.database,require_tables=True)
        expected=self.expected(snapshot);found=self.stored(expected)
        if len(found)!=len(expected):return None
        self.dictionaries(snapshot['intervals'],write=False);self.provenance(expected,snapshot['intervals']);self.guard()
        return dict(output_hash=digest(dict(source=snapshot['source_hash'],rows=sorted(expected,
            key=lambda r:(r['listing_key'],r['valid_from'])))),intervals=len(expected))

    def publish(self,snapshot,context_start,end,anchor_date,*,source_database='q_live',fixture_projection=None):
        if fixture_projection is not None and not self.database.startswith('certified_research_test_'):
            raise ValueError('Fixture provenance is forbidden outside isolated test namespaces')
        self.guard();schema.verify_storage(self.query,self.database,require_tables=True)
        previous=self.receipt(snapshot)
        if previous:return previous
        expected=self.expected(snapshot);found=self.stored(expected)
        self.dictionaries(snapshot['intervals'],write=True)
        projection=fixture_projection or universe.candidates_sql(context_start,end,anchor_date,database=source_database)
        for offset in range(0,len(expected),1024):
            batch=expected[offset:offset+1024];ids=','.join(literal(r['evidence_hash']) for r in batch)
            self.guard()
            self.query(f'INSERT INTO {self.database}.research_provenance_v1 '
                f'SELECT DISTINCT p.evidence_hash,\'reference_gateway\',p.listing_id,p.evidence_hash,p.source_proof '
                f'FROM ({projection}) p LEFT ANTI JOIN {self.database}.research_provenance_v1 e '
                f'ON p.evidence_hash=e.evidence_hash WHERE p.evidence_hash IN ({ids})',read=False)
        self.provenance(expected,snapshot['intervals'])
        missing=[r for r in expected if (r['listing_key'],r['valid_from']) not in found]
        for offset in range(0,len(missing),1024):
            self.guard();insert_rows(self.query,self.database,'research_membership_v1',missing[offset:offset+1024])
        result=self.receipt(snapshot)
        if result is None:raise ValueError('Universe publication did not read back completely')
        return result
