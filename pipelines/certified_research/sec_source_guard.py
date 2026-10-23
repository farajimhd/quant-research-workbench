"""Cache source validation only for an unchanged immutable ClickHouse part set."""
from .contracts import digest

SCOPE="(database,table) IN (('q_live','sec_filing_text_v3'),('q_live','sec_filing_document_v3'),('q_live','sec_filing_archive_accession_v3'),('sec_core','sec_bulk_mirror_xbrl_fact_v3'))"


class SourceGuard:
    def __init__(self,query,audit,owned):
        self.query=query;self.audit=audit;self.owned=owned;self.signature=None
        self.checks=0;self.full_audits=0;self.cache_hits=0

    def fingerprint(self):
        self.owned()
        tables=self.query("SELECT database,name,toString(uuid) uuid,engine FROM system.tables WHERE (database,name) IN "
            "(('q_live','sec_filing_text_v3'),('q_live','sec_filing_document_v3'),('q_live','sec_filing_archive_accession_v3'),('sec_core','sec_bulk_mirror_xbrl_fact_v3')) ORDER BY database,name")
        if len(tables)!=4 or any('MergeTree' not in r['engine'] for r in tables):raise ValueError('SEC source table identity changed')
        if self.query('SELECT mutation_id FROM system.mutations WHERE NOT is_done AND '+SCOPE+' LIMIT 1'):
            raise ValueError('SEC source mutation overlaps qualification')
        parts=self.query("SELECT count() parts,lower(hex(SHA256(arrayStringConcat(arraySort(groupArray(record)),'\\n')))) checksum "
            "FROM (SELECT toJSONString(tuple(database,table,name,toString(hash_of_all_files),rows,bytes_on_disk)) record "
            'FROM system.parts WHERE active AND '+SCOPE+' LIMIT 20001)')
        if len(parts)!=1 or int(parts[0]['parts'])>20000:raise ValueError('SEC source part census exceeds bound')
        return digest(dict(tables=tables,parts=parts))

    def __call__(self):
        self.checks+=1;before=self.fingerprint()
        if before==self.signature:self.cache_hits+=1;return
        # Merges are allowed. A changed part set forces exact source rechecks,
        # and a concurrent second change cannot be admitted on a stale audit.
        for _ in range(2):
            self.audit();self.full_audits+=1;after=self.fingerprint()
            if before==after:self.signature=after;return
            before=after
        raise ValueError('SEC source part set changed throughout bounded revalidation')
