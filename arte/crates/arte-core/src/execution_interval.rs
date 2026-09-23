//! Explicit evaluation cadence for every executable or computational definition.
//! A data dependency's bar timeframe does not imply its consumer's cadence.
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", content = "nanoseconds", rename_all = "snake_case")]
pub enum ExecutionInterval {
    Events,
    Fixed(u64),
}

impl ExecutionInterval {
    pub fn validate(self) -> Result<()> {
        match self {
            Self::Events => Ok(()),
            Self::Fixed(ns) if ns >= 100_000_000 && ns.is_multiple_of(100_000_000) => Ok(()),
            _ => Err(Error::Invalid(
                "execution interval must be events or a positive 100 ms multiple".into(),
            )),
        }
    }
    pub fn due(self, event_ns: u64, last_completed_end_ns: Option<u64>) -> Result<bool> {
        self.validate()?;
        Ok(match self {
            Self::Events => true,
            Self::Fixed(ns) => last_completed_end_ns
                .is_some_and(|end| end != 0 && end.is_multiple_of(ns) && end <= event_ns),
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionContract {
    pub kind: ExecutableKind,
    pub id: String,
    pub implementation_hash: String,
    pub interval: ExecutionInterval,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExecutableKind {
    Strategy,
    Watchlist,
    SignalStream,
    RuleSet,
    Scanner,
    Indicator,
    LevelBook,
    Computation(String),
}

impl ExecutionContract {
    pub fn hash(&self) -> Result<String> {
        self.interval.validate()?;
        if let ExecutableKind::Computation(name) = &self.kind {
            if name.is_empty()
                || name.len() > 64
                || !name
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
            {
                return Err(Error::Invalid("computation kind".into()));
            }
        }
        if self.id.is_empty()
            || self.id.len() > 128
            || self.implementation_hash.len() != 64
            || !self
                .implementation_hash
                .bytes()
                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
        {
            return Err(Error::Invalid("execution contract identity".into()));
        }
        content_hash(&("arte.execution-contract.v1", self))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cadence_is_explicit_and_pinned() {
        assert!(ExecutionInterval::Fixed(100_000_000).validate().is_ok());
        assert!(ExecutionInterval::Fixed(150_000_000).validate().is_err());
        assert!(ExecutionInterval::Fixed(0).validate().is_err());
        assert!(
            ExecutionInterval::Fixed(100_000_000)
                .due(100_000_000, None)
                .unwrap()
                == false
        );
        assert!(ExecutionInterval::Fixed(100_000_000)
            .due(100_000_000, Some(100_000_000))
            .unwrap());
        assert!(ExecutionInterval::Events.due(1, None).unwrap());
        let mut c = ExecutionContract {
            kind: ExecutableKind::Watchlist,
            id: "tradability".into(),
            implementation_hash: "a".repeat(64),
            interval: ExecutionInterval::Events,
        };
        let event_hash = c.hash().unwrap();
        c.interval = ExecutionInterval::Fixed(100_000_000);
        assert_ne!(event_hash, c.hash().unwrap());
        c.kind = ExecutableKind::SignalStream;
        assert!(c.hash().is_ok());
        c.kind = ExecutableKind::Computation("portfolio-risk".into());
        assert!(c.hash().is_ok());
        c.kind = ExecutableKind::Computation("".into());
        assert!(c.hash().is_err());
    }
}
