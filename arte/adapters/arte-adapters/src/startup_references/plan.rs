//! Map explicit previous-close dependencies to pinned startup loads.
use super::Request;
use arte_core::{coverage::Dependency, dependency_plan::Plan, Error, Result};
use std::collections::BTreeMap;

#[derive(Clone)]
pub struct Binding {
    pub request: Request,
    pub implementation_hash: String,
}
/// One target session per instrument in a startup plan. Multi-session backtests
/// must resolve their per-session plans, not extend a reference across sessions.
pub fn requests(dependencies: &Plan, bindings: Vec<Binding>) -> Result<Vec<Request>> {
    if bindings.len() > 4096 || dependencies.nodes.len() > 4096 {
        return Err(Error::Capacity("reference dependency plan bound".into()));
    }
    let mut registry = BTreeMap::new();
    for binding in bindings {
        binding.request.use_interval.validate()?;
        binding
            .request
            .requirement
            .validate(binding.request.scope)?;
        if registry
            .insert(binding.request.scope.instrument, binding)
            .is_some()
        {
            return Err(Error::Conflict(
                "duplicate instrument reference binding".into(),
            ));
        }
    }
    let mut result = Vec::new();
    for node in &dependencies.nodes {
        if node.key.dependency != Dependency::PreviousClose {
            continue;
        }
        let binding = registry
            .remove(&node.key.instrument)
            .ok_or_else(|| Error::Unready("previous-close dependency binding missing".into()))?;
        if node.implementation_hash != binding.implementation_hash
            || node.implementation_hash.len() != 64
            || !node
                .implementation_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || node.intervals.is_empty()
            || node.consumers.is_empty()
        {
            return Err(Error::Conflict(
                "previous-close dependency implementation or shape".into(),
            ));
        }
        for interval in &node.intervals {
            interval.validate()?;
            if interval.start < binding.request.use_interval.start
                || interval.end > binding.request.use_interval.end
            {
                return Err(Error::Unready(
                    "previous-close binding does not cover requested use interval".into(),
                ));
            }
        }
        result.push(binding.request);
    }
    if !registry.is_empty() {
        return Err(Error::Invalid(
            "unused reference binding outside dependency plan".into(),
        ));
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{
        coverage::Interval,
        dependency_plan::{Key, Node},
        event_order::Scope,
        reference_data::PreviousCloseRequirement,
    };
    use std::collections::BTreeSet;
    fn fixture() -> (Plan, Binding) {
        (
            Plan {
                nodes: vec![Node {
                    key: Key {
                        instrument: 1,
                        dependency: Dependency::PreviousClose,
                    },
                    implementation_hash: "a".repeat(64),
                    execution_interval: arte_core::execution_interval::ExecutionInterval::Events,
                    intervals: vec![Interval {
                        start: 100,
                        end: 200,
                    }],
                    consumers: BTreeSet::from(["candidate".into()]),
                    inputs: vec![],
                }],
            },
            Binding {
                request: Request {
                    use_interval: Interval {
                        start: 100,
                        end: 300,
                    },
                    scope: Scope {
                        provider: 1,
                        instrument: 1,
                        session: 20260915,
                    },
                    requirement: PreviousCloseRequirement {
                        session: 20260914,
                        record_hash: "b".repeat(64),
                    },
                },
                implementation_hash: "a".repeat(64),
            },
        )
    }
    #[test]
    fn exact_dependency_binding_and_interval_are_required() {
        let (mut plan, binding) = fixture();
        assert_eq!(requests(&plan, vec![binding.clone()]).unwrap().len(), 1);
        assert!(requests(&plan, vec![]).is_err());
        assert!(requests(&plan, vec![binding.clone(), binding.clone()]).is_err());
        let mut changed = binding.clone();
        changed.implementation_hash = "c".repeat(64);
        assert!(requests(&plan, vec![changed]).is_err());
        plan.nodes[0].intervals[0].end = 301;
        assert!(requests(&plan, vec![binding]).is_err());
    }
    #[test]
    fn unrelated_references_are_not_treated_as_previous_close() {
        let (mut plan, binding) = fixture();
        plan.nodes[0].key.dependency = Dependency::Reference;
        assert!(requests(&plan, vec![]).unwrap().is_empty());
        assert!(requests(&plan, vec![binding]).is_err());
    }
}
