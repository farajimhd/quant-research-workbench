from itertools import product
from research.vectorized_backtest.v66.genome import Network,Neuron,INPUTS
from research.vectorized_backtest.v66.authority import ALLOWED
from research.vectorized_backtest.v66.review_repairs import boolean_decisions


def test_boolean_wrapper_preserves_values_and_masks_including_unknowns():
    for x,y,kx,ky in product((0,1),repeat=4):
        # A decision's unknown output is always zero, unlike arbitrary nodes.
        a=bool(kx and x);b=bool(ky and y);known=bool(kx or ky)
        for operation in (lambda a,b:a and b,lambda a,b:a or b,lambda a,b:a!=b):
            value=operation(a,b)
            assert (bool(known and value>.5),known)==(value,known)


def test_internal_wrapper_removed_and_sizing_remapped():
    nodes=(Neuron((ALLOWED[0],),(1.,),0.,op='identity'),
           Neuron((INPUTS,),(.2,),0.,op='decision'),
           Neuron((INPUTS+1,),(.5,),0.,op='decision'),
           Neuron((INPUTS+2,INPUTS+1),(1.,1.),0.,op='whole_and'))
    result,removed=boolean_decisions(Network(nodes,.5,2,1.,0.))
    assert len(result.nodes)==3 and result.size_source==1
    assert result.nodes[-1].sources==(INPUTS+1,INPUTS+1)
    assert removed[0]['node']==2


def test_unknown_nonzero_boolean_is_not_simplified():
    nodes=(Neuron((ALLOWED[0],),(1.,),0.,op='identity'),
           Neuron((INPUTS,),(.2,),0.,op='decision'),
           Neuron((INPUTS+1,INPUTS+1),(1.,1.),0.,op='whole_xnor'),
           Neuron((INPUTS+2,),(.5,),0.,op='decision'),
           Neuron((INPUTS+3,INPUTS+1),(1.,1.),0.,op='whole_and'))
    original=Network(nodes,.5)
    result,removed=boolean_decisions(original)
    assert not removed and result==original
