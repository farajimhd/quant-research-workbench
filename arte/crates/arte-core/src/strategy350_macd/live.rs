//! Receipt-bound live MACD evidence from the paired exact-bar and EMA state.
//! The market owner must first consume the same pending scheduler boundary.
use super::{exact_source, Outcome, State};
use crate::{
    content_hash,
    event_order::Scope,
    events::{EventKey, Payload},
    market_structure::scheduler::{Boundary, Kind},
    Error, Result,
};

pub struct Evidence {
    configuration_hash: String,
    scope: Scope,
    run_id: String,
    event_key: EventKey,
    event_hash: String,
    received_at_ns: u64,
    boundary_id: String,
    boundary_sequence: u64,
    outcome: Outcome,
    fingerprint: String,
}
impl Evidence {
    pub fn configuration_hash(&self) -> &str {
        &self.configuration_hash
    }
    pub fn outcome(&self) -> &Outcome {
        &self.outcome
    }
    pub fn fingerprint(&self) -> &str {
        &self.fingerprint
    }
    pub fn scope(&self) -> Scope {
        self.scope
    }
    pub fn run_id(&self) -> &str {
        &self.run_id
    }
    pub fn event_key(&self) -> &EventKey {
        &self.event_key
    }
    pub fn event_hash(&self) -> &str {
        &self.event_hash
    }
    pub fn received_at_ns(&self) -> u64 {
        self.received_at_ns
    }
    pub fn boundary_id(&self) -> &str {
        &self.boundary_id
    }
    pub fn boundary_sequence(&self) -> u64 {
        self.boundary_sequence
    }
}
impl State {
    pub fn preview_live(
        &self,
        source: &exact_source::Source,
        boundary: &Boundary<'_>,
    ) -> Result<Evidence> {
        self.require_source(source)?;
        let Kind::Trade {
            observation,
            eligible: true,
        } = &boundary.kind
        else {
            return Err(Error::Unready("live MACD needs eligible trade".into()));
        };
        observation.validate()?;
        let receipt = observation
            .receipt
            .as_ref()
            .ok_or_else(|| Error::Unready("live MACD receipt missing".into()))?;
        let Payload::Trade { price, .. } = &observation.payload else {
            return Err(Error::Invalid("live MACD requires trade".into()));
        };
        if self.scope
            != (Scope {
                provider: observation.key.provider,
                instrument: observation.key.instrument,
                session: observation.key.session,
            })
            || boundary.id.is_empty()
            || boundary.sequence == 0
            || receipt.lane == 0
            || receipt.sequence == 0
            || receipt.monotonic_ns == 0
            || receipt.utc_ns > boundary.evaluated_at_ns
            || observation.sip.ns > receipt.utc_ns
        {
            return Err(Error::Conflict("live MACD boundary or receipt".into()));
        }
        let price = crate::events::Decimal {
            atoms: price.atoms_at_scale(self.price_scale)?,
            scale: self.price_scale,
        };
        let outcome = self.preview_trade(observation.sip.ns, boundary.evaluated_at_ns, price)?;
        let event_hash = content_hash(observation)?;
        let source_hash = source.identity_hash()?;
        let values = outcome.previews.map(|preview| {
            preview.map(|value| {
                (
                    value.timeframe_ns,
                    value.completed_end_ns,
                    value.line.to_bits(),
                    value.signal.to_bits(),
                )
            })
        });
        let fingerprint = content_hash(&(
            "arte.strategy-350-live-macd-evidence.v1",
            self.config_hash(),
            source_hash.as_str(),
            event_hash.as_str(),
            boundary.id,
            boundary.sequence,
            receipt,
            boundary.evaluated_at_ns,
            values,
            outcome.bullish,
        ))?;
        Ok(Evidence {
            configuration_hash: self.config_hash().into(),
            scope: self.scope,
            run_id: receipt.run_id.clone(),
            event_key: observation.key.clone(),
            event_hash,
            received_at_ns: receipt.utc_ns,
            boundary_id: boundary.id.into(),
            boundary_sequence: boundary.sequence,
            outcome,
            fingerprint,
        })
    }
}
