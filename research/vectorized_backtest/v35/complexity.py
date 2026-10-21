def count(member):
    return sum(len(n.nodes)+sum(len(x.sources) for x in n.nodes) for n in member.networks.values())
