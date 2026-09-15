//! Immutable initial outcome of one broker submission; reconciliation is separate.
use super::*;
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub enum Observation {
    Response { status: u16, body: Vec<u8> },
    Unknown { reason: String },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Record {
    pub submission_hash: String,
    pub observed_at_ns: u64,
    pub observation: Observation,
}
impl Record {
    pub fn hash(&self) -> Result<String> {
        content_hash(&("arte.broker-initial-outcome.v1", self))
    }
    pub fn require(&self, marker: &submission::Marker) -> Result<()> {
        if self.submission_hash != marker.hash()? || self.observed_at_ns < marker.prepared_at_ns {
            return Err(Error::Conflict(
                "broker outcome submission or clock mismatch".into(),
            ));
        }
        match &self.observation {
            Observation::Response { status, body }
                if (100..=599).contains(status) && body.len() <= 64 * 1024 =>
            {
                Ok(())
            }
            Observation::Unknown { reason } if !reason.is_empty() && reason.len() <= 2048 => Ok(()),
            _ => Err(Error::Invalid("broker outcome status or size".into())),
        }
    }
}
pub struct Pending {
    record: Record,
    acknowledged: bool,
}
pub struct Committed {
    record: Record,
}
impl Committed {
    pub fn record(&self) -> &Record {
        &self.record
    }
}
impl Pending {
    pub fn new(
        marker: &submission::Marker,
        observed_at_ns: u64,
        observation: Observation,
    ) -> Result<Self> {
        let record = Record {
            submission_hash: marker.hash()?,
            observed_at_ns,
            observation,
        };
        record.require(marker)?;
        Ok(Self {
            record,
            acknowledged: false,
        })
    }
    pub fn record(&self) -> Result<&Record> {
        if self.acknowledged {
            return Err(Error::Unready("broker outcome already committed".into()));
        }
        Ok(&self.record)
    }
    pub fn acknowledge(&mut self, readback: &Record) -> Result<Committed> {
        if self.record()? != readback {
            return Err(Error::Conflict("broker outcome readback differs".into()));
        }
        self.acknowledged = true;
        Ok(Committed {
            record: self.record.clone(),
        })
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn marker() -> submission::Marker {
        submission::Marker {
            order_key: "a".repeat(64),
            authorization_hash: "b".repeat(64),
            request_hash: "c".repeat(64),
            prepared_at_ns: 10,
        }
    }
    #[test]
    fn raw_bytes_and_time_remain_exact_through_readback() {
        let marker = marker();
        let mut pending = Pending::new(
            &marker,
            11,
            Observation::Response {
                status: 429,
                body: vec![0, 255, 128, 10],
            },
        )
        .unwrap();
        let expected = pending.record().unwrap().clone();
        let bytes = serde_json::to_vec(&expected).unwrap();
        assert_eq!(serde_json::from_slice::<Record>(&bytes).unwrap(), expected);
        let mut wrong = expected.clone();
        wrong.observed_at_ns += 1;
        assert!(pending.acknowledge(&wrong).is_err());
        assert_eq!(pending.record().unwrap(), &expected);
        assert_eq!(pending.acknowledge(&expected).unwrap().record(), &expected);
        assert!(pending.acknowledge(&expected).is_err());
    }
    #[test]
    fn invalid_clock_status_and_payload_limits_are_rejected() {
        let marker = marker();
        assert!(Pending::new(
            &marker,
            9,
            Observation::Unknown {
                reason: "lost".into()
            }
        )
        .is_err());
        assert!(Pending::new(
            &marker,
            11,
            Observation::Unknown {
                reason: String::new()
            }
        )
        .is_err());
        assert!(Pending::new(
            &marker,
            11,
            Observation::Response {
                status: 99,
                body: vec![]
            }
        )
        .is_err());
        assert!(Pending::new(
            &marker,
            11,
            Observation::Response {
                status: 200,
                body: vec![0; 65537]
            }
        )
        .is_err());
        let pending = Pending::new(
            &marker,
            11,
            Observation::Unknown {
                reason: "lost".into(),
            },
        )
        .unwrap();
        let mut other = marker;
        other.request_hash = "d".repeat(64);
        assert!(pending.record().unwrap().require(&other).is_err());
    }
}
