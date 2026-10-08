"""Native-channel namespace for the shared durable product dispatch protocol."""
from src.market_engine.completed_return_insert_authority import KeeperProductInsertAuthority
from src.market_engine.native_causal_channel_contract import FEATURE_TABLE, COVERAGE_TABLE

ROOT = '/market-products/native-causal-channels/insert-authority/v1'


class NativeChannelInsertAuthority(KeeperProductInsertAuthority):
    @property
    def namespace(self):
        return ROOT

    @property
    def allowed_tables(self):
        return (FEATURE_TABLE, COVERAGE_TABLE)
