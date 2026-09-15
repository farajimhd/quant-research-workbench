//! Resolve quote dependencies to one consistent provider policy per startup session.
use super::Request;
use arte_core::{coverage::Dependency, dependency_plan::Plan, Error, Result};
use std::collections::BTreeMap;

#[derive(Clone)]
pub struct Binding {
    pub instrument: u64,
    pub quote_implementation_hash: String,
    pub request: Request,
}
fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
/// Bindings describe the common target session window. Distinct provider versions
/// or windows must use separate startup plans, not silent union/latest selection.
pub fn requests(dependencies: &Plan, bindings: Vec<Binding>) -> Result<Vec<Request>> {
    if dependencies.nodes.len() > 100_000 || bindings.len() > 100_000 {
        return Err(Error::Capacity("quote policy dependency plan bound".into()));
    }
    let mut instruments = BTreeMap::new();
    for binding in bindings {
        binding.request.use_interval.validate()?;
        if binding.instrument == 0
            || binding.request.provider == 0
            || !valid_hash(&binding.quote_implementation_hash)
            || !valid_hash(&binding.request.hash)
        {
            return Err(Error::Invalid(
                "quote policy dependency binding identity".into(),
            ));
        }
        if instruments.insert(binding.instrument, binding).is_some() {
            return Err(Error::Conflict(
                "duplicate instrument quote policy binding".into(),
            ));
        }
    }
    let mut providers: BTreeMap<u16, Request> = BTreeMap::new();
    for node in &dependencies.nodes {
        if node.key.dependency != Dependency::Quotes {
            continue;
        }
        let binding = instruments
            .remove(&node.key.instrument)
            .ok_or_else(|| Error::Unready("quote dependency policy binding missing".into()))?;
        if node.implementation_hash != binding.quote_implementation_hash
            || node.intervals.is_empty()
            || node.consumers.is_empty()
        {
            return Err(Error::Conflict(
                "quote dependency implementation or shape differs".into(),
            ));
        }
        for interval in &node.intervals {
            interval.validate()?;
            if interval.start < binding.request.use_interval.start
                || interval.end > binding.request.use_interval.end
            {
                return Err(Error::Unready(
                    "quote policy binding does not cover dependency window".into(),
                ));
            }
        }
        let request = binding.request;
        if let Some(previous) = providers.get(&request.provider) {
            if previous.hash != request.hash || previous.use_interval != request.use_interval {
                return Err(Error::Conflict(
                    "provider quote policy bindings disagree".into(),
                ));
            }
        } else {
            providers.insert(request.provider, request);
            if providers.len() > 256 {
                return Err(Error::Capacity("quote policy provider budget".into()));
            }
        }
    }
    if !instruments.is_empty() {
        return Err(Error::Invalid(
            "quote policy binding is outside dependency plan".into(),
        ));
    }
    Ok(providers.into_values().collect())
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{
        coverage::Interval,
        dependency_plan::{Key, Node},
    };
    fn fixture() -> (Plan, Vec<Binding>) {
        let nodes = [1, 2]
            .into_iter()
            .map(|instrument| Node {
                key: Key {
                    instrument,
                    dependency: Dependency::Quotes,
                },
                implementation_hash: "a".repeat(64),
                intervals: vec![Interval {
                    start: 100,
                    end: 200,
                }],
                consumers: ["strategy".into()].into(),
                inputs: vec![],
            })
            .collect();
        let bindings = [1, 2]
            .into_iter()
            .map(|instrument| Binding {
                instrument,
                quote_implementation_hash: "a".repeat(64),
                request: Request {
                    provider: 1,
                    hash: "b".repeat(64),
                    use_interval: Interval {
                        start: 100,
                        end: 300,
                    },
                },
            })
            .collect();
        (Plan { nodes }, bindings)
    }
    #[test]
    fn all_instruments_require_matching_bindings_but_shared_providers_load_once() {
        let (plan, bindings) = fixture();
        assert_eq!(requests(&plan, bindings.clone()).unwrap().len(), 1);
        assert!(requests(&plan, bindings[..1].to_vec()).is_err());
        let mut extra = bindings.clone();
        extra.push(bindings[0].clone());
        assert!(requests(&plan, extra).is_err());
        for kind in 0..5 {
            let mut changed = bindings.clone();
            match kind {
                0 => changed[1].request.hash = "c".repeat(64),
                1 => changed[1].request.use_interval.end = 400,
                2 => changed[1].quote_implementation_hash = "c".repeat(64),
                3 => changed[1].request.use_interval.end = 199,
                _ => changed[1].instrument = 3,
            }
            assert!(requests(&plan, changed).is_err());
        }
    }
    #[test]
    fn non_quote_dependencies_do_not_trigger_policy_loading() {
        let (mut plan, bindings) = fixture();
        for node in &mut plan.nodes {
            node.key.dependency = Dependency::Trades;
        }
        assert!(requests(&plan, vec![]).unwrap().is_empty());
        assert!(requests(&plan, bindings).is_err());
    }
}
