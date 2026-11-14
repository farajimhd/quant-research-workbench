from collections import deque
from research.bar_gpt.v5.phase2_ar_shards.run import pop_publication


def test_publication_bypasses_saturated_lane_without_reordering_its_sessions():
    staged=deque([('a',1,{}),('b',3,{}),('a',2,{})]);groups={'a':{'slot':0},'b':{'slot':1}}
    active={1:('a',0,1),2:('a',0,1)}
    assert pop_publication(staged,groups,active,2)[1]==3
    assert list(staged)==[('a',1,{}),('a',2,{})]
    assert pop_publication(staged,groups,active,2) is None
    active.pop(2)
    assert pop_publication(staged,groups,active,2)[1]==1


def test_all_lanes_remain_bounded_and_publish_every_unit_once():
    groups={str(i):{'slot':i} for i in range(16)}
    staged=deque((str(i),j,{}) for i in range(16) for j in range(20));active={};seen={str(i):[] for i in range(16)};seq=0
    while staged or active:
        while len(active)<16:
            item=pop_publication(staged,groups,active,8)
            if item is None:break
            seq+=1;active[seq]=(item[0],item[1],1)
        counts=[sum(groups[k]['slot']%8==i for k,_,_ in active.values()) for i in range(8)]
        assert max(counts)<=2
        if active:
            first=next(iter(active));key,unit,_=active.pop(first);seen[key].append(unit)
    assert all(values==list(range(20)) for values in seen.values())
