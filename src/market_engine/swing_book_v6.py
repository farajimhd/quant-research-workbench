"""Daily survivor-only continuation; V5 selection, no hidden candidate carry."""
from copy import deepcopy
from .swing_book import INTRADAY_VERSION
from .swing_book_v5 import StreamingSwingBookV5, CONTRACT
from .resistance_selection import select_areas

VERSION='causal-swing-closing-book-6'
# Only survivor state. No member lists, event histories, local levels or segments.
FIELDS=('level_id','price','lower','upper','side','pivot_at','confirmed_at',
        'last_test','formed_at','last_role_change_at','role_retests',
        'history_threshold','retest_threshold','best_departure',
        'independent_retests','accepted_crossings','history_away','reversal_distance')


class StreamingSwingBookV6(StreamingSwingBookV5):
    def __init__(self, seed=None, opening=None, split_factor=1.):
        if seed is not None:
            if seed['version']!=VERSION:raise ValueError('V6 requires a survivor-only seed')
            seed=deepcopy(seed)
            seed['version']=INTRADAY_VERSION
            for row in seed['levels']:
                if set(row)!=set(FIELDS):raise ValueError('Invalid V6 survivor fields')
                row.update(scale='major',state='active',tests=1,strength=1.,beyond=0,
                    touching=False,previous_contact=False,break_at=None,
                    confirmation_kind='survivor',retest_pending_at=None,history_observed_at=None)
        super().__init__(seed,opening,split_factor,contract=CONTRACT)

    def closing_state(self, closed_at):
        if closed_at<self.last_time:raise ValueError('Close precedes observed candles')
        candidates=self.active;previous=None
        # Removing weak candidates changes greedy area boundaries. Finish the
        # monotone prune/merge here, never defer another merge until tomorrow.
        for _ in range(len(candidates)+2):
            levels=[]
            for side in ('support','resistance'):
                for area in select_areas(candidates.values(),closed_at,side=side,role_safe=True,
                        minimum_score=self.minimum_score,maximum_width_bps=self.maximum_width_bps):
                    if not area['selected']:continue
                    representative=candidates[area['representative_id']]
                    row={k:deepcopy(representative[k]) for k in FIELDS}
                    row.update(lower=area['lower'],upper=area['upper'])
                    members=[candidates[int(k)] for k in area['members']]
                    for key in ('pivot_at','confirmed_at','formed_at'):
                        row[key]=max(r[key] for r in members)
                    levels.append(row)
            levels.sort(key=lambda r:r['level_id'])
            if levels==previous:
                return dict(version=VERSION,closed_at=closed_at,sequence=self.sequence,levels=levels)
            previous=levels
            candidates={r['level_id']:dict(r,scale='major',state='active') for r in levels}
        raise RuntimeError('Survivor consolidation did not converge')

    def snapshot(self):
        value=super().snapshot()
        for row in value['unified_levels']:
            row['book_version']=VERSION
            # Presentation lineage is distinct from the newest confirmation,
            # which still controls when the merged area becomes available.
            row['oldest_member_confirmed_at_ms']=int(min(
                self.active[int(key)]['confirmed_at'] for key in row['selection_members']
            )*1000)
        return value


def project_survivor(row):
    candidate=dict(row,state='active',scale='major')
    area=select_areas([candidate],max(row['confirmed_at'],row['formed_at'],row.get('last_role_change_at') or 0),
                      side=row['side'],role_safe=True)[0]
    return dict(side=1 if row['side']=='support' else -1,prominence=area['score'])
