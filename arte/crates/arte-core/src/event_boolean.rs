//! Event-cadence Boolean evidence from the shared causal scheduler.
//! This is deliberately distinct from the fixed-bar Boolean catalogue.
use crate::{
    content_hash,
    coverage::Interval,
    event_order::Scope,
    execution_interval::{ExecutionContract, ExecutionInterval, Route},
    market_structure::scheduler::{Boundary, Kind},
    Error, Result,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
pub mod ledger;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Transition {
    /// Zero-based index in the complete event-evaluation stream.
    pub event_index: u64,
    pub boundary_id: String,
    pub source_sequence: u64,
    pub evaluated_at_ns: u64,
    pub value: Option<bool>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Product {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
    pub interval: Interval,
    pub definition_hash: String,
    pub event_count: u64,
    /// Digest of every evaluated event boundary, including unchanged values.
    pub source_hash: String,
    pub evaluation_hash: String,
    pub transitions: Vec<Transition>,
}

impl Product {
    pub fn validate(&self) -> Result<()> {
        self.interval.validate()?;
        if self.provider == 0
            || self.instrument == 0
            || !(19000101..=29991231).contains(&self.session)
            || !hash_valid(&self.definition_hash)
            || !hash_valid(&self.source_hash)
            || !hash_valid(&self.evaluation_hash)
            || self.event_count > 100_000_000
            || self.transitions.len() > 10_000_000
            || self.transitions.len() as u64 > self.event_count
        {
            return Err(Error::Invalid(
                "event Boolean product identity or capacity".into(),
            ));
        }
        let mut last = None;
        for transition in &self.transitions {
            if transition.event_index >= self.event_count
                || !hash_valid(&transition.boundary_id)
                || transition.source_sequence == 0
                || transition.evaluated_at_ns == 0
                || last.is_some_and(|prior: &Transition| {
                    transition.event_index <= prior.event_index
                        || transition.source_sequence <= prior.source_sequence
                        || transition.evaluated_at_ns < prior.evaluated_at_ns
                        || transition.value == prior.value
                })
                || (last.is_none() && transition.value.is_none())
            {
                return Err(Error::Conflict(
                    "event Boolean transition order or state".into(),
                ));
            }
            last = Some(transition);
        }
        Ok(())
    }
    pub fn hash(&self) -> Result<String> {
        self.validate()?;
        content_hash(&("arte.event-boolean.product.v1", self))
    }
}

pub struct Builder {
    scope: Scope,
    interval: Interval,
    route: Route,
    definition_hash: String,
    maximum_events: u64,
    maximum_transitions: usize,
    last_sequence: u64,
    last_source_ns: u64,
    last_evaluated_ns: u64,
    state: Option<bool>,
    count: u64,
    source_digest: Sha256,
    evaluation_digest: Sha256,
    transitions: Vec<Transition>,
}

fn hash_valid(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn source_domain(scope: Scope, interval: Interval, definition_hash: &str) -> Result<String> {
    content_hash(&(
        "arte.event-boolean.domain.v1",
        (scope.provider, scope.instrument, scope.session),
        (interval.start, interval.end),
        definition_hash,
    ))
}

fn source_item(
    boundary: &Boundary<'_>,
    scope: Scope,
    interval: Interval,
    last_sequence: u64,
    last_source_ns: u64,
    last_evaluated_ns: u64,
) -> Result<(String, u64)> {
    let (observation, eligibility) = match &boundary.kind {
        Kind::Trade {
            observation,
            eligible,
        } => (*observation, Some(*eligible)),
        Kind::Quote { observation } => (*observation, None),
        Kind::Completed { .. } => {
            return Err(Error::Invalid(
                "event Boolean requires event boundary".into(),
            ))
        }
    };
    observation.validate()?;
    if observation.key.provider != scope.provider
        || observation.key.instrument != scope.instrument
        || observation.key.session != scope.session
        || observation.sip.ns < interval.start
        || observation.sip.ns >= interval.end
        || !hash_valid(boundary.id)
        || boundary.sequence <= last_sequence
        || observation.sip.ns < last_source_ns
        || boundary.evaluated_at_ns < last_evaluated_ns
        || observation.available_at_ns > boundary.evaluated_at_ns
    {
        return Err(Error::Conflict(
            "event Boolean boundary scope or clocks".into(),
        ));
    }
    let item = content_hash(&(
        "arte.event-boolean.source-item.v1",
        boundary.id,
        boundary.sequence,
        content_hash(observation)?,
        eligibility,
        boundary.evaluated_at_ns,
    ))?;
    Ok((item, observation.sip.ns))
}

impl Builder {
    pub fn new(
        scope: Scope,
        interval: Interval,
        definition: &ExecutionContract,
        maximum_events: u64,
        maximum_transitions: usize,
    ) -> Result<Self> {
        crate::event_order::Buffer::new(scope, 1, 0)?;
        interval.validate()?;
        let route = Route::new(definition)?;
        if route.interval() != ExecutionInterval::Events {
            return Err(Error::Invalid("event Boolean definition clock".into()));
        }
        if maximum_events == 0
            || maximum_events > 100_000_000
            || maximum_transitions == 0
            || maximum_transitions > 10_000_000
            || maximum_transitions as u64 > maximum_events
        {
            return Err(Error::Capacity("event Boolean evaluation budget".into()));
        }
        let definition_hash = definition.hash()?;
        let domain = source_domain(scope, interval, &definition_hash)?;
        let mut source_digest = Sha256::new();
        source_digest.update(b"arte.event-boolean.source.v1");
        source_digest.update(domain.as_bytes());
        let mut evaluation_digest = Sha256::new();
        evaluation_digest.update(b"arte.event-boolean.evaluations.v1");
        evaluation_digest.update(domain.as_bytes());
        Ok(Self {
            scope,
            interval,
            route,
            definition_hash,
            maximum_events,
            maximum_transitions,
            last_sequence: 0,
            last_source_ns: 0,
            last_evaluated_ns: 0,
            state: None,
            count: 0,
            source_digest,
            evaluation_digest,
            transitions: Vec::new(),
        })
    }

    /// Call once for each acknowledged event boundary. The value is the
    /// computation's result at that boundary, with None meaning unknown.
    /// The caller must independently verify the final source count and digest.
    pub fn observe(&mut self, boundary: &Boundary<'_>, value: Option<bool>) -> Result<()> {
        if !boundary.due_for(self.route) {
            return Err(Error::Invalid(
                "event Boolean requires event boundary".into(),
            ));
        }
        if self.count == self.maximum_events {
            return Err(Error::Capacity("event Boolean evaluation budget".into()));
        }
        let (source_item, source_ns) = source_item(
            boundary,
            self.scope,
            self.interval,
            self.last_sequence,
            self.last_source_ns,
            self.last_evaluated_ns,
        )?;
        let evaluation_item = content_hash(&(&source_item, value))?;
        let index = self.count;
        if self.state != value {
            if self.transitions.len() == self.maximum_transitions {
                return Err(Error::Capacity("event Boolean transition budget".into()));
            }
            self.transitions.push(Transition {
                event_index: index,
                boundary_id: boundary.id.to_owned(),
                source_sequence: boundary.sequence,
                evaluated_at_ns: boundary.evaluated_at_ns,
                value,
            });
        }
        self.source_digest.update(source_item.as_bytes());
        self.evaluation_digest.update(evaluation_item.as_bytes());
        self.count += 1;
        self.last_sequence = boundary.sequence;
        self.last_source_ns = source_ns;
        self.last_evaluated_ns = boundary.evaluated_at_ns;
        self.state = value;
        Ok(())
    }

    /// A verified source ledger supplies expected count/hash. A caller cannot
    /// turn an omitted evaluation into complete coverage by sealing early.
    pub(crate) fn seal(self, expected_events: u64, expected_source_hash: &str) -> Result<Product> {
        let source_hash = format!("{:x}", self.source_digest.finalize());
        if self.count != expected_events || source_hash != expected_source_hash {
            return Err(Error::Unready(
                "event Boolean source ledger or evaluation count differs".into(),
            ));
        }
        let product = Product {
            provider: self.scope.provider,
            instrument: self.scope.instrument,
            session: self.scope.session,
            interval: self.interval,
            definition_hash: self.definition_hash,
            event_count: self.count,
            source_hash,
            evaluation_hash: format!("{:x}", self.evaluation_digest.finalize()),
            transitions: self.transitions,
        };
        product.validate()?;
        Ok(product)
    }

    pub fn seal_verified(self, source: &ledger::SourceProof) -> Result<Product> {
        if self.scope != source.scope()
            || self.interval != source.interval()
            || self.definition_hash != source.definition_hash()
        {
            return Err(Error::Conflict(
                "event Boolean verified source domain differs".into(),
            ));
        }
        self.seal(source.event_count(), source.source_hash())
    }

    pub fn event_count(&self) -> u64 {
        self.count
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        events::{Decimal, EventKey, EventKind, Observation, Payload, SourceTime},
        execution_interval::ExecutableKind,
    };
    fn scope() -> Scope {
        Scope {
            provider: 1,
            instrument: 10,
            session: 20260922,
        }
    }
    fn definition() -> ExecutionContract {
        ExecutionContract {
            kind: ExecutableKind::SignalStream,
            id: "event-signal".into(),
            implementation_hash: "a".repeat(64),
            interval: ExecutionInterval::Events,
        }
    }
    fn interval() -> Interval {
        Interval {
            start: 100,
            end: 400,
        }
    }
    fn observation(sequence: u64, at: u64) -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 10,
                session: 20260922,
                kind: EventKind::Trade,
                sequence,
            },
            payload: Payload::Trade {
                price: Decimal {
                    atoms: 1000,
                    scale: 2,
                },
                size: Decimal { atoms: 1, scale: 0 },
                exchange: 1,
                trade_id: sequence.to_string(),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: SourceTime {
                ns: at,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: at + 1,
            receipt: None,
        }
    }
    #[test]
    fn event_signal_keeps_unknown_and_requires_independent_source_ledger() {
        let mut builder = Builder::new(scope(), interval(), &definition(), 3, 3).unwrap();
        let events = [
            observation(1, 100),
            observation(2, 200),
            observation(3, 300),
        ];
        let ids = ["b".repeat(64), "c".repeat(64), "d".repeat(64)];
        let mut source_digest = Sha256::new();
        source_digest.update(b"arte.event-boolean.source.v1");
        source_digest.update(
            content_hash(&(
                "arte.event-boolean.domain.v1",
                (scope().provider, scope().instrument, scope().session),
                (interval().start, interval().end),
                definition().hash().unwrap(),
            ))
            .unwrap()
            .as_bytes(),
        );
        for (index, event) in events.iter().enumerate() {
            let source_item = content_hash(&(
                "arte.event-boolean.source-item.v1",
                ids[index].as_str(),
                index as u64 + 1,
                content_hash(event).unwrap(),
                Some(true),
                event.available_at_ns,
            ))
            .unwrap();
            source_digest.update(source_item.as_bytes());
            builder
                .observe(
                    &Boundary {
                        id: &ids[index],
                        sequence: index as u64 + 1,
                        evaluated_at_ns: event.available_at_ns,
                        kind: Kind::Trade {
                            observation: event,
                            eligible: true,
                        },
                    },
                    [None, Some(true), Some(true)][index],
                )
                .unwrap();
        }
        assert_eq!(builder.event_count(), 3);
        let source_hash = format!("{:x}", source_digest.finalize());
        assert!(builder.seal(2, &source_hash).is_err());

        let mut builder = Builder::new(scope(), interval(), &definition(), 3, 3).unwrap();
        for (index, event) in events.iter().enumerate() {
            builder
                .observe(
                    &Boundary {
                        id: &ids[index],
                        sequence: index as u64 + 1,
                        evaluated_at_ns: event.available_at_ns,
                        kind: Kind::Trade {
                            observation: event,
                            eligible: true,
                        },
                    },
                    [None, Some(true), Some(true)][index],
                )
                .unwrap();
        }
        let product = builder.seal(3, &source_hash).unwrap();
        assert_eq!(product.event_count, 3);
        assert_eq!(product.transitions.len(), 1);
        assert_eq!(product.transitions[0].event_index, 1);
        assert_eq!(product.transitions[0].value, Some(true));
        assert_eq!(product.hash().unwrap().len(), 64);
        let mut tampered = product.clone();
        tampered.transitions[0].value = None;
        assert!(tampered.hash().is_err());

        let mut changed_policy = Builder::new(scope(), interval(), &definition(), 3, 3).unwrap();
        for (index, event) in events.iter().enumerate() {
            changed_policy
                .observe(
                    &Boundary {
                        id: &ids[index],
                        sequence: index as u64 + 1,
                        evaluated_at_ns: event.available_at_ns,
                        kind: Kind::Trade {
                            observation: event,
                            eligible: index != 1,
                        },
                    },
                    [None, Some(true), Some(true)][index],
                )
                .unwrap();
        }
        assert!(changed_policy.seal(3, &source_hash).is_err());
    }
    #[test]
    fn wrong_clock_and_repeated_boundary_fail_closed() {
        let mut fixed = definition();
        fixed.interval = ExecutionInterval::Fixed(100_000_000);
        assert!(Builder::new(scope(), interval(), &fixed, 2, 2).is_err());
        let mut builder = Builder::new(scope(), interval(), &definition(), 1, 1).unwrap();
        let event = observation(1, 100);
        let boundary = Boundary {
            id: &"b".repeat(64),
            sequence: 1,
            evaluated_at_ns: 101,
            kind: Kind::Trade {
                observation: &event,
                eligible: true,
            },
        };
        builder.observe(&boundary, Some(false)).unwrap();
        assert!(builder.observe(&boundary, Some(true)).is_err());
    }
    #[test]
    fn out_of_interval_and_transition_overflow_do_not_advance_evaluation() {
        let mut builder = Builder::new(scope(), interval(), &definition(), 2, 1).unwrap();
        let outside = observation(1, 400);
        assert!(builder
            .observe(
                &Boundary {
                    id: &"b".repeat(64),
                    sequence: 1,
                    evaluated_at_ns: 401,
                    kind: Kind::Trade {
                        observation: &outside,
                        eligible: true,
                    },
                },
                Some(true),
            )
            .is_err());
        assert_eq!(builder.event_count(), 0);
        let first = observation(1, 100);
        builder
            .observe(
                &Boundary {
                    id: &"b".repeat(64),
                    sequence: 1,
                    evaluated_at_ns: 101,
                    kind: Kind::Trade {
                        observation: &first,
                        eligible: true,
                    },
                },
                Some(true),
            )
            .unwrap();
        let second = observation(2, 200);
        assert!(builder
            .observe(
                &Boundary {
                    id: &"c".repeat(64),
                    sequence: 2,
                    evaluated_at_ns: 201,
                    kind: Kind::Trade {
                        observation: &second,
                        eligible: true,
                    },
                },
                Some(false),
            )
            .is_err());
        assert_eq!(builder.event_count(), 1);
    }
}
