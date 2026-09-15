//! One send followed by retryable outcome publication. No implicit resend path.
use super::*;
use arte_core::orders::{
    outcome::{Committed, Observation, Pending, Record},
    submission::Marker,
};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Phase {
    Ready,
    Sending,
    Observed,
    Publishing,
    Complete,
}
enum State {
    Ready(Box<(Request, SendPermit)>),
    Sending,
    Observed(Record),
    Publishing(Pending),
    Complete,
}
pub struct Attempt {
    authorization: Authorization,
    marker: Marker,
    state: State,
}
impl Attempt {
    pub fn new(authorization: Authorization, request: Request, permit: SendPermit) -> Result<Self> {
        let marker = permit.marker().clone();
        marker.require(&authorization)?;
        if marker.request_hash != request.hash()
            || authorization.hash()? != request.authorization_hash
        {
            return Err(Error::Conflict(
                "execution attempt identity mismatch".into(),
            ));
        }
        Ok(Self {
            authorization,
            marker,
            state: State::Ready(Box::new((request, permit))),
        })
    }
    pub fn phase(&self) -> Phase {
        match self.state {
            State::Ready(_) => Phase::Ready,
            State::Sending => Phase::Sending,
            State::Observed(_) => Phase::Observed,
            State::Publishing(_) => Phase::Publishing,
            State::Complete => Phase::Complete,
        }
    }
    pub fn marker(&self) -> &Marker {
        &self.marker
    }
    pub fn observed_record(&self) -> Option<&Record> {
        match &self.state {
            State::Observed(record) => Some(record),
            State::Publishing(pending) => pending.record().ok(),
            _ => None,
        }
    }
    /// State changes before awaiting transport. Dropping this future while a send
    /// is pending leaves Sending, never Ready. observed_now is sampled once.
    pub async fn send<'a>(
        &mut self,
        transport: &mut impl Transport,
        current_safety: impl FnOnce() -> Result<Safety<'a>>,
        observed_now: impl FnOnce() -> u64,
    ) -> Result<()> {
        if !matches!(self.state, State::Ready(_)) {
            return Err(Error::Unready("attempt cannot send again".into()));
        }
        let State::Ready(prepared) = std::mem::replace(&mut self.state, State::Sending) else {
            unreachable!()
        };
        let (request, permit) = *prepared;
        let observation = match submit(request, permit, transport, current_safety).await {
            Ok(Delivery::Response(response)) => Observation::Response {
                status: response.status,
                body: response.body,
            },
            Ok(Delivery::Unknown(reason)) => Observation::Unknown { reason },
            Err(error) => Observation::Unknown {
                reason: error.to_string(),
            },
        };
        self.state = State::Observed(Record {
            submission_hash: self.marker.hash()?,
            observed_at_ns: observed_now(),
            observation,
        });
        Ok(())
    }
    /// Call only after the borrowing send future has ended or been dropped.
    /// Recognizing interruption is itself an observation, not a new send attempt.
    pub fn record_interruption(&mut self, observed_at_ns: u64) -> Result<()> {
        if !matches!(self.state, State::Sending) {
            return Err(Error::Unready("attempt is not interrupted in send".into()));
        }
        self.state = State::Observed(Record {
            submission_hash: self.marker.hash()?,
            observed_at_ns,
            observation: Observation::Unknown {
                reason: "send interrupted; broker reconciliation required".into(),
            },
        });
        Ok(())
    }
    /// Validation failure preserves raw evidence and its original timestamp.
    /// Persistence cancellation leaves the exact pending record available to retry.
    pub async fn publish(
        &mut self,
        publisher: &mut impl crate::order_journal::OutcomePublisher,
    ) -> Result<Committed> {
        if let State::Observed(record) = &self.state {
            record.require(&self.marker)?;
            let pending = Pending::new(
                &self.marker,
                record.observed_at_ns,
                record.observation.clone(),
            )?;
            self.state = State::Publishing(pending);
        }
        let State::Publishing(pending) = &mut self.state else {
            return Err(Error::Unready("attempt has no publishable outcome".into()));
        };
        let committed = crate::order_journal::commit_outcome(
            pending,
            &self.authorization,
            &self.marker,
            publisher,
        )
        .await?;
        self.state = State::Complete;
        Ok(committed)
    }
}
