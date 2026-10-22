"""Family access keeps issuer facts separate; ownership is never an addition rule."""
from .eligibility import require_eligible
from .queries import _timestamp, observations_sql
from pipelines.certified_research.contracts import literal


def retained_family(query, root_cik, cutoff, *, known_at=None, maximum_entities=256):
    """Recorded relationship view, NOT historical public ownership certification.

    Existing curated scope dates are not proof of inception. A relationship is
    only exposed after its first retained observation. Full family completeness
    is unknown because exhibits may explicitly omit subsidiaries.
    """
    require_eligible('issuer_cik',[root_cik])
    if not isinstance(root_cik,str) or len(root_cik)!=10 or not root_cik.isascii() or not root_cik.isdigit():
        raise ValueError('Explicit zero-padded issuer CIK required')
    if not 1<=maximum_entities<=256:raise ValueError('Family bound must be 1..256')
    end=_timestamp(cutoff);known=_timestamp(known_at) if known_at else end
    entities={root_cik};frontier={root_cik};edges={}
    for depth in range(16):
        if not frontier:break
        ids=','.join(map(literal,sorted(frontier)))
        rows=query('SELECT relationship_id,parent_cik,child_cik,evidence_accession_number,evidence_url '
            'FROM q_live.id_issuer_relationship_v1 FINAL '
            f'WHERE parent_cik IN ({ids}) AND relationship_status=\'active\' '
            "AND relationship_type='listed_ultimate_parent' "
            f'AND first_seen_at_utc<={end} AND inserted_at<={known} '
            f'AND valid_from_date<=toDate({end}) '
            f'AND (isNull(valid_to_date_exclusive) OR valid_to_date_exclusive>toDate({end})) LIMIT 10001')
        if len(rows)>10000:raise ValueError('Family edge bound exceeded; no partial result returned')
        discovered=set()
        for row in rows:
            if row['parent_cik'] not in frontier or not row['evidence_accession_number'] or not row['evidence_url']:
                raise ValueError('Relationship identity/evidence missing')
            child=row['child_cik'];require_eligible('issuer_cik',[child])
            if len(child)!=10 or not child.isascii() or not child.isdigit() or child==row['parent_cik']:
                raise ValueError('Invalid subsidiary CIK')
            key=row['relationship_id']
            if key in edges and edges[key]!=row:raise ValueError('Conflicting relationship evidence')
            edges[key]=row;discovered.add(child)
        frontier=discovered-entities;entities.update(frontier)
        if len(entities)>maximum_entities:raise ValueError('Family exceeds entity bound; no truncation allowed')
    if frontier:raise ValueError('Family graph depth exceeds bound')
    # DFS catches cycles even when traversal deduplicates shared descendants.
    adjacency={c:set() for c in entities}
    for edge in edges.values():adjacency[edge['parent_cik']].add(edge['child_cik'])
    visiting=set();visited=set()
    def visit(c):
        if c in visiting:raise ValueError('Cyclic ownership evidence')
        if c in visited:return
        visiting.add(c)
        for child in adjacency[c]:visit(child)
        visiting.remove(c);visited.add(c)
    visit(root_cik)
    return dict(root_cik=root_cik,entities=sorted(entities),relationships=sorted(edges.values(),key=lambda r:r['relationship_id']),
                clock='recorded',completeness='unknown',aggregation='none',public_time_qualified=False)


def family_observations(query,database,root_cik,metrics,cutoff,*,since=None,known_at=None):
    family=retained_family(query,root_cik,cutoff,known_at=known_at)
    # One query over a distinct entity set. Diamond paths cannot multiply facts.
    sql=observations_sql(database,'issuer_cik',family['entities'],metrics,cutoff,
                         clock='recorded',since=since,known_at=known_at)
    return dict(family=family,observations=query(sql))
