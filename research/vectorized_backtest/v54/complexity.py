"""Count recurrent cells, head readouts and logical joins exactly once."""
def count(member):
    n=member.networks['shared']
    return len(n.nodes)+sum(len(x.sources) for x in n.nodes)+sum(1+len(b.sources) for h in n.heads for b in h.branches)+sum(h.mode!='plain' for h in n.heads)
