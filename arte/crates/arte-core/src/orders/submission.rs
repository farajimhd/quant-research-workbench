//! Single-use permission follows exact readback of a persisted submission marker.
use super::*;
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Marker {
    pub order_key: String,
    pub authorization_hash: String,
    pub request_hash: String,
    pub prepared_at_ns: u64,
}
impl Marker {
    pub fn hash(&self) -> Result<String> {
        content_hash(&("arte.order-submission.v1", self))
    }
    pub(super) fn require(&self, authorization: &Authorization) -> Result<()> {
        if self.order_key != authorization.key()?
            || self.authorization_hash != authorization.hash()?
            || !recovery::hash_valid(&self.request_hash)
            || self.prepared_at_ns == 0
        {
            return Err(Error::Conflict("submission marker identity".into()));
        }
        Ok(())
    }
}
/// Deliberately neither Clone nor serializable. Restarts reconcile instead of
/// recreating permission. Final safety checks must run after marker persistence.
pub struct SendPermit {
    marker: Marker,
    authorization: Authorization,
}
impl SendPermit {
    pub fn marker(&self) -> &Marker {
        &self.marker
    }
    #[allow(clippy::too_many_arguments)]
    pub fn validate_request(
        self,
        request_hash: &str,
        now_ns: u64,
        session: &TradingSession,
        bands: Option<&Bands>,
        policy: &RiskPolicy,
        market: crate::exposure::Check<'_>,
    ) -> Result<Authorization> {
        if request_hash != self.marker.request_hash
            || now_ns < self.marker.prepared_at_ns
            || session.authorization_context(policy)? != self.authorization.context
        {
            return Err(Error::Conflict(
                "broker request or authorization context changed".into(),
            ));
        }
        market.require(self.authorization.bracket.instrument)?;
        session.validate(&self.authorization.bracket, now_ns, bands, policy)?;
        Ok(self.authorization)
    }
}
impl OrderLedger {
    pub fn prepare_submission_marker(
        &mut self,
        id: &str,
        request_hash: &str,
        now_ns: u64,
    ) -> Result<&Marker> {
        let authorization = self.authorization(id)?;
        let record = self.record_mut(id)?;
        if record.state != OrderState::Submitting || record.submission_receipt.is_some() {
            return Err(Error::Unready(
                "submission marker cannot be prepared in current state".into(),
            ));
        }
        if let Some(existing) = &record.submission {
            if existing.request_hash != request_hash || now_ns < existing.prepared_at_ns {
                return Err(Error::Conflict(
                    "submission retry request changed or clock reversed".into(),
                ));
            }
        } else {
            let marker = Marker {
                order_key: authorization.key()?,
                authorization_hash: authorization.hash()?,
                request_hash: request_hash.into(),
                prepared_at_ns: now_ns,
            };
            marker.require(&authorization)?;
            record.submission = Some(marker);
        }
        Ok(record.submission.as_ref().unwrap())
    }
    pub fn acknowledge_submission(&mut self, id: &str, readback: &Marker) -> Result<SendPermit> {
        let authorization = self.authorization(id)?;
        readback.require(&authorization)?;
        let record = self.record_mut(id)?;
        if record.state != OrderState::Submitting
            || record.submission_receipt.is_some()
            || record.submission.as_ref() != Some(readback)
        {
            return Err(Error::Conflict(
                "submission marker readback mismatch or permission already issued".into(),
            ));
        }
        record.submission_receipt = Some(readback.hash()?);
        Ok(SendPermit {
            marker: readback.clone(),
            authorization,
        })
    }
}
