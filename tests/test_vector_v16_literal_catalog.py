"""Feature projection must not narrow the language's legal literal units."""
import pytest
import torch

from research.vectorized_backtest.v16.features import CATALOG
from research.vectorized_backtest.v16.genome import Individual, Policy, STAGES
from research.vectorized_backtest.v16.program import Node, Op, Program, TorchPrograms
from research.vectorized_backtest.v16.rule_plan import RulePlan


@pytest.mark.parametrize('unit', sorted({f.unit for f in CATALOG} - {'bool', 'ratio'}))
@pytest.mark.parametrize('device', ['cpu', pytest.param('cuda', marks=pytest.mark.skipif(
    not torch.cuda.is_available(), reason='CUDA workstation gate'))])
def test_projected_rule_retains_literal_units_and_exact_masks(unit, device):
    feature = next(i for i, f in enumerate(CATALOG) if f.unit == 'ratio')
    rule = Program((Node(Op.FEATURE, feature=feature),
                    Node(Op.CONSTANT, value=0.5),
                    Node(Op.GREATER, a=0, b=1),
                    Node(Op.CONSTANT, value=2, unit=unit),
                    Node(Op.CONSTANT, value=1, unit=unit),
                    Node(Op.GREATER, a=3, b=4),
                    Node(Op.AND, a=2, b=5)), 6)
    member = Individual({s: rule for s in STAGES}, Policy()).validate()
    plan = RulePlan([member], device)
    values = torch.zeros((1, 4, len(CATALOG)), device=device)
    values[0, :, feature] = torch.tensor([0., 1., 1., 2.], device=device)
    known = torch.ones_like(values, dtype=torch.bool)
    known[0, 2, feature] = False
    reference, mask = TorchPrograms([rule], CATALOG, device)(values, known)
    expected = (reference != 0) & mask
    for index, group in enumerate(plan.groups):
        assert group['columns'] == [feature]
        actual = plan.execute(index, values[..., group['columns']], known[..., group['columns']])
        assert torch.equal(actual, expected)
    plan.close()


def test_literal_catalog_does_not_relax_unit_or_feature_checks():
    catalog = [CATALOG[0]]
    rule = Program((Node(Op.CONSTANT, value=1, unit='unknown-unit'),), 0)
    with pytest.raises(ValueError, match='Unknown literal unit'):
        rule.validate(catalog, output_unit='unknown-unit', literal_catalog=CATALOG)
    rule = Program((Node(Op.FEATURE, feature=len(catalog)),), 0)
    with pytest.raises(ValueError, match='Unknown feature'):
        rule.validate(catalog, literal_catalog=CATALOG)
