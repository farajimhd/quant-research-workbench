"""Causal v5 projection over the shared v4 detector; no market I/O on update."""
from copy import deepcopy
from types import SimpleNamespace
from .swing_book import SwingBook, INTRADAY_VERSION, project
from .resistance_selection import select_areas

VERSION = 'causal-swing-closing-book-5'
LEGACY_CONTRACT = 'resistance-evidence-selection-1'
CONTRACT = 'symmetric-level-evidence-selection-2'


def projection(engine, now, minimum_score=30., maximum_width_bps=100., contract=CONTRACT):
    supports = [dict(project(r, VERSION), p_norm=None, selection_score=None, load_contract=contract)
                for r in engine.active.values() if r['state']=='active' and r['scale']=='major' and r['side']=='support'] if contract==LEGACY_CONTRACT else []
    by_id = {str(r['level_id']): r for r in engine.active.values()}
    result = supports
    for side in (('resistance',) if contract==LEGACY_CONTRACT else ('support','resistance')):
        for area in select_areas(engine.active.values(), now, minimum_score=minimum_score, maximum_width_bps=maximum_width_bps,side=side,role_safe=contract==CONTRACT):
            if not area['selected']:
                continue
            members = [by_id[k] for k in area['members']]
            result.append(dict(unified_level_id=('r:' if side=='resistance' else 's:')+area['id'], price=area['price'], lower=area['lower'], upper=area['upper'],
                side=-1 if side=='resistance' else 1, prominence=area['score'], selection_score=area['score'], p_norm=None,
                created_at_ms=int(max(r['pivot_at'] for r in members)*1000),
                confirmed_at_ms=int(max(r['confirmed_at'] for r in members)*1000), lifecycle='active',
                book_version=VERSION, load_contract=contract, scale='major', timeframes=['1s'],
                member_count=len(members), selection_members=area['members'], sources=[], selection_reasons=area['reasons']))
            if contract==CONTRACT: result[-1]['selection_minimum_score']=minimum_score
    return dict(unified_levels=result, load_contract=contract)


class StreamingSwingBookV5(SwingBook):
    """Completed canonical seconds only. Seed is a compact v4 candidate checkpoint."""
    def __init__(self, seed=None, opening=None, split_factor=1., *, minimum_score=30., maximum_width_bps=100., contract=CONTRACT):
        if contract not in (CONTRACT,LEGACY_CONTRACT): raise ValueError('Unsupported V5 selection contract')
        select_areas([],0,minimum_score=minimum_score,maximum_width_bps=maximum_width_bps)
        self.selection_contract=contract
        self._selection_signatures={}
        self.selection_rebuilds=0
        self.selection_dirty = True
        self.minimum_score, self.maximum_width_bps = minimum_score, maximum_width_bps
        self._selection = None
        self._qualified_references = {}
        self._qualified_members = {}
        super().__init__(seed, opening, split_factor, version=INTRADAY_VERSION)
        if opening is not None:
            self.last_time = opening

    def _level_updated(self, level):
        super()._level_updated(level)
        if self.selection_contract==LEGACY_CONTRACT:
            self.selection_dirty=True
            self.revision+=1
        elif level['scale']=='major':
            threshold=level.get('history_threshold',0)
            departure=min(1.,level.get('best_departure',0)/threshold) if threshold>0 and level.get('last_role_change_at') is None else 0.
            signature=tuple(level.get(k) for k in ('side','state','lower','upper','price','pivot_at','confirmed_at','formed_at','last_role_change_at','independent_retests','role_retests','accepted_crossings'))+(departure,)
            key=level['level_id']
            if self._selection_signatures.get(key)!=signature:
                self._selection_signatures[key]=signature
                self.selection_dirty=True
                self.revision+=1

    def _level_removed(self, key):
        if self._selection_signatures.pop(key,None) is not None or self.selection_contract==LEGACY_CONTRACT:
            self.selection_dirty = True
        super()._level_removed(key)

    def _publish(self, level, t, reason):
        if self.selection_contract==LEGACY_CONTRACT: self.selection_dirty = True
        super()._publish(level,t,reason)

    def _refresh_selection(self):
        if self.selection_dirty or self._selection is None:
            self.selection_rebuilds+=1
            self._selection = projection(self, self.last_time, self.minimum_score, self.maximum_width_bps,self.selection_contract)
            current = self._selection['unified_levels']
            active_members = {k for row in current for k in row.get('selection_members', [])}
            by_id = {str(r['level_id']): r for r in self.active.values()}
            # Selection reads scalar fields only; a shallow copy freezes them
            # without deep-copying every candidate on each completed second.
            qualified_members = {k:dict(by_id[k]) for k in active_members}
            for key, row in self._qualified_references.items():
                side='resistance' if row['side']==-1 else 'support'
                surviving = [k for k in row['selection_members'] if k in by_id and k not in active_members
                    and by_id[k]['side']==side and by_id[k]['state'] in ('active','awaiting_retest','retest_contact')]
                if not surviving or not any(by_id[k]['state']!='active' for k in surviving):
                    continue
                # Split mixed-role areas using evidence frozen when qualified.
                # Do not inherit a departed member's score, geometry or identity.
                retained_rows = [row] if surviving==row['selection_members'] else projection(
                    SimpleNamespace(active={k:self._qualified_members[k] for k in surviving}),
                    self.last_time,self.minimum_score,self.maximum_width_bps,self.selection_contract)['unified_levels']
                for retained in retained_rows:
                    ids=retained['selection_members']
                    if not any(by_id[k]['state']!='active' for k in ids):
                        continue
                    lifecycle = 'retest_contact' if any(by_id[k]['state']=='retest_contact' for k in ids) else 'awaiting_retest'
                    retained=dict(retained,lifecycle=lifecycle)
                    retained['retained_qualified_'+side]=True
                    current.append(retained)
                    active_members.update(ids)
                    qualified_members.update((k,self._qualified_members[k]) for k in ids)
            self._qualified_references = {r['unified_level_id']:dict(r) for r in current if 'selection_members' in r}
            self._qualified_members = qualified_members
            self.selection_dirty = False

    def snapshot(self):
        self._refresh_selection()
        return deepcopy(self._selection)

    def observe(self, *bar):
        # Capture qualification at its causal second, independent of UI polling.
        self._refresh_selection()
        super().observe(*bar)
        self._refresh_selection()
