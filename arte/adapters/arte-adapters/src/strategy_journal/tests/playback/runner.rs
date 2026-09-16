//! Network-free coordinator exercise using explicit fixture inputs, not defaults.
use crate::playback_runtime::{
    self,
    candidates::Candidates,
    runner::{Inputs, Journals, Step},
};
use arte_core::{Error, Result};
use std::collections::BTreeMap;
struct NoFills;
impl crate::fill_journal::Publisher for NoFills {
    async fn publish(
        &mut self,
        _: &crate::fill_journal::Batch,
    ) -> Result<BTreeMap<String, String>> {
        Err(Error::Unready("unexpected fixture fill".into()))
    }
}
struct NoRejections;
impl crate::rejection_journal::Publisher for NoRejections {
    async fn append(
        &mut self,
        _: &arte_core::strategy_transaction::Committed,
        _: &arte_core::action_rejection::Record,
    ) -> Result<arte_core::action_rejection::Record> {
        Err(Error::Unready("unexpected fixture rejection".into()))
    }
}
pub(super) async fn service<P: crate::strategy_journal::Publisher>(
    controller: &mut playback_runtime::Runtime,
    candidates: &mut Candidates,
    portfolio: &arte_core::portfolio::Portfolio,
    decisions: &mut BTreeMap<String, P>,
) -> Step {
    let calendar = arte_core::session::Session {
        exchange: "XNYS".into(),
        session: 20260915,
        previous_trading_session: 20260914,
        extended: arte_core::coverage::Interval {
            start: 1,
            end: 300_000_000_000,
        },
        regular: arte_core::coverage::Interval {
            start: 220_000_000_000,
            end: 250_000_000_000,
        },
        available_at_ns: 0,
        source_manifest_hash: "a".repeat(64),
    };
    let hash = arte_core::content_hash(&calendar).unwrap();
    let session = arte_core::orders::TradingSession::new(calendar, hash, 0, true).unwrap();
    let risk = arte_core::orders::RiskPolicy {
        band_provider: 1,
        band_session: 20260915,
        band_buffer_ticks: 2,
        max_band_age_ns: 1_000_000_000,
    };
    let mut fills = BTreeMap::<String, NoFills>::new();
    controller
        .service_boundary(
            candidates,
            Inputs {
                actions: playback_runtime::SizedActionInputs {
                    sizing: &BTreeMap::new(),
                    cash_policies: &BTreeMap::new(),
                    portfolio,
                    safety: crate::simulation_runtime::AmendmentSafety {
                        session: &session,
                        risk_policy: &risk,
                        bands: None,
                    },
                    latency_ns: 0,
                    maximum_actions: 2,
                },
                currencies: &BTreeMap::new(),
                maximum_funding_orders: 2,
                maximum_settlement_receipts: 100,
                decision_concurrency: 2,
            },
            Journals {
                fills: &mut fills,
                decisions,
                rejections: &mut NoRejections,
            },
        )
        .await
        .unwrap()
}
