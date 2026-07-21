"""Canonical trading lifecycle with explicit adapters for IBKR-shaped legacy callers."""

from src.trading_runtime.broker import BrokerAdapter
from src.trading_runtime.ibkr_schema import (
    AccountLedger,
    AccountSummary,
    Execution,
    LiveOrder,
    OrderRequest,
    OrderStatus,
    PortfolioPosition,
)
from src.trading_runtime.simulated_broker import SimulatedBrokerAdapter, SimulationConfig
from src.trading_runtime.canonical_broker import CanonicalBrokerAdapter
from src.trading_runtime.canonical_session import CanonicalBrokerSession
from src.trading_runtime.domain import (
    AccountValue,
    BrokerAccount,
    BrokerEventEnvelope,
    BrokerEventType,
    BrokerProvider,
    InstrumentContract,
    LedgerBalance,
    OrderIntent,
    OrderLifecycleState,
    OrderState,
    PositionState,
    RoundTripTrade,
    SnapshotManifest,
    TradingMode,
    TradingStateSnapshot,
)

__all__ = [
    "AccountLedger",
    "AccountSummary",
    "BrokerAdapter",
    "CanonicalBrokerAdapter",
    "CanonicalBrokerSession",
    "BrokerAccount",
    "BrokerEventEnvelope",
    "BrokerEventType",
    "BrokerProvider",
    "Execution",
    "LiveOrder",
    "OrderRequest",
    "OrderIntent",
    "OrderLifecycleState",
    "OrderState",
    "OrderStatus",
    "PortfolioPosition",
    "PositionState",
    "RoundTripTrade",
    "SnapshotManifest",
    "TradingMode",
    "TradingStateSnapshot",
    "InstrumentContract",
    "AccountValue",
    "LedgerBalance",
    "SimulatedBrokerAdapter",
    "SimulationConfig",
]
