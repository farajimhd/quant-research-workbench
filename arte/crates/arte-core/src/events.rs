use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Exact base-10 quantity. Rejects precision loss instead of rounding silently.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct Decimal {
    pub atoms: i64,
    pub scale: u8,
}
impl Decimal {
    pub fn parse(text: &str) -> Result<Self> {
        let (negative, text) = if let Some(t) = text.strip_prefix('-') {
            (true, t)
        } else {
            (false, text)
        };
        let parts: Vec<_> = text.split('.').collect();
        if parts.len() > 2
            || parts[0].is_empty()
            || parts
                .iter()
                .any(|p| p.is_empty() || !p.bytes().all(|c| c.is_ascii_digit()))
        {
            return Err(Error::Invalid(
                "decimal must be plain base-10 digits".into(),
            ));
        }
        let mut fraction = parts.get(1).copied().unwrap_or("").to_string();
        while fraction.ends_with('0') {
            fraction.pop();
        }
        if fraction.len() > 9 {
            return Err(Error::Invalid("decimal precision exceeds 9 places".into()));
        }
        let digits = format!("{}{}", parts[0], fraction);
        let unsigned: i64 = digits
            .parse()
            .map_err(|_| Error::Invalid("decimal overflow".into()))?;
        Ok(Self {
            atoms: if negative { -unsigned } else { unsigned },
            scale: fraction.len() as u8,
        })
    }
    pub fn positive(self) -> bool {
        self.atoms > 0 && self.scale <= 9
    }
    pub fn to_f64(self) -> f64 {
        self.atoms as f64 / 10_f64.powi(self.scale as i32)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventKind {
    Trade,
    Quote,
}

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct EventKey {
    pub provider: u16,
    pub instrument: u64,
    /// Validated YYYYMMDD provider session, not the UTC arrival date.
    pub session: u32,
    pub kind: EventKind,
    pub sequence: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Payload {
    Trade {
        price: Decimal,
        size: Decimal,
        exchange: u16,
        trade_id: String,
        trf: Option<u16>,
        conditions: Vec<u16>,
        correction: Option<u8>,
    },
    Quote {
        bid: Decimal,
        ask: Decimal,
        bid_size: Decimal,
        ask_size: Decimal,
        bid_exchange: u16,
        ask_exchange: u16,
        conditions: Vec<u16>,
        indicators: Vec<u16>,
    },
}
impl Payload {
    pub fn kind(&self) -> EventKind {
        match self {
            Self::Trade { .. } => EventKind::Trade,
            Self::Quote { .. } => EventKind::Quote,
        }
    }
    pub fn validate(&self) -> Result<()> {
        let decimals: Vec<Decimal> = match self {
            Self::Trade {
                price,
                size,
                trade_id,
                ..
            } => {
                if !price.positive() || !size.positive() || trade_id.is_empty() {
                    return Err(Error::Invalid("invalid trade".into()));
                }
                vec![*price, *size]
            }
            Self::Quote {
                bid,
                ask,
                bid_size,
                ask_size,
                ..
            } => vec![*bid, *ask, *bid_size, *ask_size],
        };
        // Zero/locked/crossed quotes are source facts, not valid trading permission.
        if decimals.iter().any(|d| d.atoms < 0 || d.scale > 9) {
            return Err(Error::Invalid(
                "negative value or unsupported decimal precision".into(),
            ));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct SourceTime {
    pub ns: u64,
    pub precision_ns: u32,
}
impl SourceTime {
    pub fn validate(self) -> Result<()> {
        if self.ns == 0
            || !matches!(self.precision_ns, 1 | 1_000 | 1_000_000)
            || !self.ns.is_multiple_of(self.precision_ns as u64)
        {
            return Err(Error::Invalid("invalid timestamp precision".into()));
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Receipt {
    pub run_id: String,
    pub lane: u16,
    pub sequence: u64,
    pub utc_ns: u64,
    pub monotonic_ns: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Observation {
    pub key: EventKey,
    pub payload: Payload,
    pub sip: SourceTime,
    pub participant: Option<SourceTime>,
    pub available_at_ns: u64,
    pub receipt: Option<Receipt>,
}
impl Observation {
    pub fn validate(&self) -> Result<()> {
        if self.key.provider == 0
            || self.key.instrument == 0
            || !(19000101..=29991231).contains(&self.key.session)
            || self.key.kind != self.payload.kind()
        {
            return Err(Error::Invalid("invalid event identity".into()));
        }
        self.payload.validate()?;
        self.sip.validate()?;
        if let Some(p) = self.participant {
            p.validate()?;
        }
        if self.available_at_ns == 0 {
            return Err(Error::Invalid("availability is required".into()));
        }
        if let Some(r) = &self.receipt {
            if r.run_id.is_empty() || r.utc_ns != self.available_at_ns {
                return Err(Error::Invalid(
                    "live availability must equal receipt time".into(),
                ));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ObservationRef {
    pub key: EventKey,
    pub payload_hash: String,
    pub sip: SourceTime,
    pub participant: Option<SourceTime>,
    pub available_at_ns: u64,
    pub receipt: Option<Receipt>,
}

/// One payload per content identity. Thin observation metadata preserves knowledge time.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EventStore {
    pub payloads: BTreeMap<String, Payload>,
    pub observations: Vec<ObservationRef>,
    by_key: BTreeMap<EventKey, Vec<usize>>,
    max_observations: usize,
}
impl EventStore {
    pub fn new(max_observations: usize) -> Result<Self> {
        if max_observations == 0 {
            return Err(Error::Invalid("event capacity is zero".into()));
        }
        Ok(Self {
            payloads: BTreeMap::new(),
            observations: vec![],
            by_key: BTreeMap::new(),
            max_observations,
        })
    }
    /// Returns existing index for an identical acquisition retry. No payload duplication.
    pub fn insert(&mut self, observation: Observation) -> Result<usize> {
        observation.validate()?;
        let hash = content_hash(&observation.payload)?;
        let thin = ObservationRef {
            key: observation.key.clone(),
            payload_hash: hash.clone(),
            sip: observation.sip,
            participant: observation.participant,
            available_at_ns: observation.available_at_ns,
            receipt: observation.receipt,
        };
        if let Some(indexes) = self.by_key.get(&observation.key) {
            for &i in indexes {
                let old = &self.observations[i];
                if old == &thin
                    || (old.receipt.is_none()
                        && thin.receipt.is_none()
                        && old.payload_hash == thin.payload_hash
                        && old.sip == thin.sip
                        && old.participant == thin.participant
                        && old.available_at_ns <= thin.available_at_ns)
                {
                    return Ok(i);
                }
            }
        }
        if self.observations.len() >= self.max_observations {
            return Err(Error::Capacity("event observations".into()));
        }
        let index = self.observations.len();
        self.payloads.entry(hash).or_insert(observation.payload);
        self.by_key.entry(thin.key.clone()).or_default().push(index);
        self.observations.push(thin);
        Ok(index)
    }
    /// Conflicting market payloads require explicit reconciliation, never last-write-wins.
    pub fn as_known(&self, key: &EventKey, cutoff: u64) -> Result<Option<&ObservationRef>> {
        let Some(indexes) = self.by_key.get(key) else {
            return Ok(None);
        };
        let visible: Vec<_> = indexes
            .iter()
            .map(|i| &self.observations[*i])
            .filter(|o| o.available_at_ns <= cutoff)
            .collect();
        if visible.iter().any(|o| {
            visible
                .first()
                .is_some_and(|first| first.payload_hash != o.payload_hash)
        }) {
            return Err(Error::Conflict("unresolved source correction".into()));
        }
        Ok(visible.into_iter().max_by_key(|o| o.available_at_ns))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    pub fn sample() -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 1,
                session: 20260915,
                kind: EventKind::Trade,
                sequence: 104,
            },
            payload: Payload::Trade {
                price: Decimal::parse("10.01").unwrap(),
                size: Decimal::parse("1.25").unwrap(),
                exchange: 1,
                trade_id: "a".into(),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: SourceTime {
                ns: 1_000_000,
                precision_ns: 1_000_000,
            },
            participant: None,
            available_at_ns: 2_000_000,
            receipt: None,
        }
    }
    #[test]
    fn decimal_exact() {
        assert_eq!(
            Decimal::parse("10.0100").unwrap(),
            Decimal {
                atoms: 1001,
                scale: 2
            }
        );
        assert!(Decimal::parse("1e-3").is_err());
    }
    #[test]
    fn retries_and_enrichment_do_not_duplicate_payload() {
        let mut store = EventStore::new(10).unwrap();
        let original = sample();
        assert_eq!(store.insert(original.clone()).unwrap(), 0);
        assert_eq!(store.insert(original.clone()).unwrap(), 0);
        let mut rest = original.clone();
        rest.participant = Some(SourceTime {
            ns: 999_999,
            precision_ns: 1,
        });
        rest.available_at_ns = 3_000_000;
        store.insert(rest).unwrap();
        assert_eq!(store.payloads.len(), 1);
        assert_eq!(
            store
                .as_known(&original.key, 2_000_000)
                .unwrap()
                .unwrap()
                .participant,
            None
        );
    }
    #[test]
    fn gap_arrival_does_not_renumber() {
        let mut store = EventStore::new(5).unwrap();
        let a = sample();
        store.insert(a.clone()).unwrap();
        let mut b = a;
        b.key.sequence = 102;
        store.insert(b).unwrap();
        assert_eq!(store.observations[0].key.sequence, 104);
    }
    #[test]
    fn capacity_never_evicts() {
        let mut s = EventStore::new(1).unwrap();
        let a = sample();
        s.insert(a.clone()).unwrap();
        let mut b = a;
        b.key.sequence += 1;
        assert!(s.insert(b).is_err());
        assert_eq!(s.observations.len(), 1);
    }
}
