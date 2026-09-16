//! Journal contract for a pre-submission sizing rejection. This is not permission
//! to discard a funded action: the execution owner must independently rule out a
//! reservation, authorization, submission and fill before resolving its queue.
use crate::{
    content_hash,
    events::Decimal,
    order_funding::sizing::{Assessment, Outcome},
    strategy_dispatch::{Action, Mode},
    strategy_transaction::Committed as DecisionReceipt,
    Error, Result,
};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub schema_version: u32,
    pub decision_id: String,
    pub action_index: usize,
    pub evaluated_at_ns: u64,
    /// Binds the quote, account snapshot and applied policies. The controller owns
    /// their provenance; a syntactically valid hash alone does not certify them.
    pub evidence_hash: String,
    pub assessment: Assessment,
}
impl Record {
    pub fn key(&self) -> Result<String> {
        if !hash(&self.decision_id) || self.action_index >= 8 {
            return Err(Error::Invalid("rejection action identity".into()));
        }
        content_hash(&(
            "arte.action-rejection-slot.v1",
            &self.decision_id,
            self.action_index,
        ))
    }
    pub fn hash(&self) -> Result<String> {
        content_hash(&("arte.action-rejection.v1", self))
    }
    pub fn require(&self, committed: &DecisionReceipt) -> Result<()> {
        let decision = committed.decision();
        self.key()?;
        if self.schema_version != 1
            || self.decision_id != decision.decision_id
            || !hash(&self.evidence_hash)
            || self.evaluated_at_ns < decision.input.evaluated_at_ns
            || (decision.scope.mode == Mode::Backtest
                && self.evaluated_at_ns != decision.input.evaluated_at_ns)
        {
            return Err(Error::Conflict(
                "rejection decision, clock, evidence or version".into(),
            ));
        }
        if !matches!(self.assessment.outcome()?, Outcome::Rejected(_)) {
            return Err(Error::Invalid(
                "fundable assessment is not rejection evidence".into(),
            ));
        }
        let (stop, maximum) = match decision.actions.get(self.action_index) {
            Some(Action::Enter(p)) => (p.stop, p.maximum_buy_price),
            Some(Action::Add(p)) => (p.stop, p.maximum_buy_price),
            _ => {
                return Err(Error::Invalid(
                    "only entry and add sizing may be rejected".into(),
                ))
            }
        };
        let input = self.assessment.input();
        if !stop.is_finite() || stop <= 0. || !maximum.is_finite() || maximum <= 0. {
            return Err(Error::Invalid("invalid rejected strategy prices".into()));
        }
        let stop = Decimal::parse(&stop.to_string())?.atoms_at_scale(input.price_scale)?;
        if u64::try_from(stop).ok() != Some(input.stop) {
            return Err(Error::Conflict(
                "rejection stop differs from committed proposal".into(),
            ));
        }
        let maximum = Decimal::parse(&maximum.to_string())?;
        let scale = maximum.scale.max(input.price_scale);
        let entry = i128::from(input.entry) * 10_i128.pow(u32::from(scale - input.price_scale));
        let cap = i128::from(maximum.atoms) * 10_i128.pow(u32::from(scale - maximum.scale));
        if entry > cap {
            return Err(Error::Conflict(
                "sizing rejection used entry above strategy cap".into(),
            ));
        }
        Ok(())
    }
}
fn hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub struct Pending {
    record: Record,
    acknowledged: bool,
}
#[derive(Debug, Clone)]
pub struct Committed {
    record: Record,
}
impl Committed {
    pub fn record(&self) -> &Record {
        &self.record
    }
    /// The caller must supply an independently read journal row and expected hash.
    /// This validates content, not storage durability or absence of a broker order.
    pub fn from_readback(
        decision: &DecisionReceipt,
        expected_hash: &str,
        record: Record,
    ) -> Result<Self> {
        record.require(decision)?;
        if record.hash()? != expected_hash {
            return Err(Error::Conflict("rejection recovery hash differs".into()));
        }
        Ok(Self { record })
    }
}
impl Pending {
    pub fn new(
        decision: &DecisionReceipt,
        action_index: usize,
        evaluated_at_ns: u64,
        evidence_hash: String,
        assessment: Assessment,
    ) -> Result<Self> {
        let record = Record {
            schema_version: 1,
            decision_id: decision.decision().decision_id.clone(),
            action_index,
            evaluated_at_ns,
            evidence_hash,
            assessment,
        };
        record.require(decision)?;
        Ok(Self {
            record,
            acknowledged: false,
        })
    }
    pub fn record(&self) -> Result<&Record> {
        if self.acknowledged {
            return Err(Error::Unready("rejection already acknowledged".into()));
        }
        Ok(&self.record)
    }
    pub fn acknowledge(&mut self, readback: &Record) -> Result<Committed> {
        if self.record()?.hash()? != readback.hash()? {
            return Err(Error::Conflict("rejection journal readback differs".into()));
        }
        self.acknowledged = true;
        Ok(Committed {
            record: self.record.clone(),
        })
    }
}
