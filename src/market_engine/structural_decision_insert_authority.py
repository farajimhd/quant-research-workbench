"""Issued exact structural domain; never admits tables through return authority."""
from src.market_engine.completed_return_insert_authority import KeeperProductInsertAuthority
from src.market_engine.structural_decision_population_contract import TABLE_SCHEMAS


class KeeperStructuralDecisionInsertAuthority(KeeperProductInsertAuthority):
    @property
    def namespace(self):
        return '/market-products/structural-decision-population/insert-authority/v1'

    @property
    def allowed_tables(self):
        return tuple(TABLE_SCHEMAS)
