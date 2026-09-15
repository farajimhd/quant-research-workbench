//! Shared fill evidence. An execution report is not an authorization to trade.
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Leg {
    Entry,
    Stop,
    Target,
    Exit,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Direction {
    Buy,
    Sell,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Origin {
    Broker {
        session_id: String,
        execution_id: String,
        paper: bool,
    },
    Simulated {
        run_id: String,
        model: String,
    },
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Fill {
    pub schema_version: u32,
    pub origin: Origin,
    pub command_id: String,
    pub account: String,
    pub instrument: u64,
    pub price_scale: u8,
    /// Authority-local observation sequence, not an event storage ordinal.
    pub sequence: u64,
    /// Time this execution report becomes available to the engine/run.
    pub at_ns: u64,
    /// Broker-reported or explicitly modeled execution time. Missing stays missing.
    pub executed_at_ns: Option<u64>,
    pub leg: Leg,
    pub direction: Direction,
    pub quantity: u64,
    pub price: i64,
}
impl Fill {
    pub fn id(&self) -> Result<String> {
        if self.schema_version != 1
            || self.command_id.is_empty()
            || self.account.is_empty()
            || self.instrument == 0
            || self.price_scale > 9
            || self.sequence == 0
            || self.quantity == 0
            || self.price <= 0
            || self.executed_at_ns.is_some_and(|at| at > self.at_ns)
        {
            return Err(Error::Invalid("invalid execution fill contract".into()));
        }
        match &self.origin {
            Origin::Broker {
                session_id,
                execution_id,
                paper,
            } => {
                if session_id.is_empty() || execution_id.is_empty() {
                    return Err(Error::Invalid("broker execution identity missing".into()));
                }
                content_hash(&(
                    "broker-fill-v1",
                    session_id,
                    execution_id,
                    paper,
                    &self.account,
                ))
            }
            Origin::Simulated { run_id, model } => {
                if run_id.is_empty() || model.is_empty() {
                    return Err(Error::Invalid(
                        "simulation execution identity missing".into(),
                    ));
                }
                content_hash(&(
                    "simulated-fill-v1",
                    run_id,
                    model,
                    &self.command_id,
                    &self.account,
                    self.sequence,
                    self.leg,
                ))
            }
        }
    }
}
pub struct FillBook {
    maximum: usize,
    fills: BTreeMap<String, Fill>,
}
impl FillBook {
    pub fn new(maximum: usize) -> Result<Self> {
        if maximum == 0 || maximum > 10_000_000 {
            return Err(Error::Invalid("invalid fill book capacity".into()));
        }
        Ok(Self {
            maximum,
            fills: BTreeMap::new(),
        })
    }
    /// True is a new report. Conflicting corrections require a separate explicit
    /// correction workflow, never replacement of an already-accounted fill.
    pub fn insert(&mut self, fill: Fill) -> Result<bool> {
        let id = fill.id()?;
        if let Some(previous) = self.fills.get(&id) {
            return if content_hash(previous)? == content_hash(&fill)? {
                Ok(false)
            } else {
                Err(Error::Conflict(
                    "execution identity changed contents".into(),
                ))
            };
        }
        if self.fills.len() == self.maximum {
            return Err(Error::Capacity("execution fill book full".into()));
        }
        self.fills.insert(id, fill);
        Ok(true)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn execution_origin_and_retry_identity_remain_distinct() {
        let mut fill = Fill {
            schema_version: 1,
            origin: Origin::Simulated {
                run_id: "r".into(),
                model: "m".into(),
            },
            command_id: "c".into(),
            account: "a".into(),
            instrument: 1,
            price_scale: 2,
            sequence: 1,
            at_ns: 1,
            executed_at_ns: None,
            leg: Leg::Entry,
            direction: Direction::Buy,
            quantity: 1,
            price: 100,
        };
        let mut book = FillBook::new(2).unwrap();
        assert!(book.insert(fill.clone()).unwrap());
        assert!(!book.insert(fill.clone()).unwrap());
        fill.quantity = 2;
        assert!(book.insert(fill.clone()).is_err());
        fill.origin = Origin::Broker {
            session_id: "s".into(),
            execution_id: "e".into(),
            paper: true,
        };
        assert!(book.insert(fill.clone()).unwrap());
        fill.sequence += 1;
        assert!(book.insert(fill).is_err());
    }
}
