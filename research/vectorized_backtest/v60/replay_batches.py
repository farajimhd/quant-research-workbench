"""Cost-balanced execution packing; preserve identities and memory limits."""
from .config import STATES
from .strategy_mix import node_count

def work(member):
    from .strategy_mix import leaves
    return sum(1+len(n.sources) for leaf in leaves(member) for net in leaf.networks.values() for n in net.nodes)


def plan_batches(population, missing, width, base_size=512, maximum_size=1024,
                 *, workspace_bytes=4*2**30):
    if not 1<=base_size<=maximum_size<=6144 or width<1:
        raise ValueError('Invalid bounded replay batch configuration')
    if not missing:return []
    if len(set(missing))!=len(missing):raise ValueError('Duplicate replay identity')
    import heapq
    count=(len(missing)+maximum_size-1)//maximum_size
    capacities=[len(missing)//count+(i<len(missing)%count) for i in range(count)]
    bins=[[] for _ in range(count)];heap=[(0,i) for i in range(count)]
    costs={i:work(population[i]) for i in missing}
    for i in sorted(missing,key=lambda i:(-costs[i],i)):
        cost,j=heapq.heappop(heap);bins[j].append(i)
        if len(bins[j])<capacities[j]:heapq.heappush(heap,(cost+costs[i],j))
    batches=[]
    def admit(selection):
        nodes=sum(node_count(population[i]) for i in selection)
        required=nodes*width*5+len(selection)*width*len(STATES)*4
        if required<=workspace_bytes:batches.append(selection);return
        if len(selection)==1:raise MemoryError('One candidate exceeds neural workspace')
        split=min(base_size,(len(selection)+1)//2)
        admit(selection[:split]);admit(selection[split:])
    for selection in bins:admit(sorted(selection))
    return batches
