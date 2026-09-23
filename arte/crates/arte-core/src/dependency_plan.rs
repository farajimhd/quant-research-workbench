//! Versioned startup dependency closure. Plans work; does not certify readiness.
use crate::{
    content_hash,
    coverage::{Dependency, Interval},
    execution_interval::ExecutionInterval,
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Input {
    pub dependency: Dependency,
    pub extra_history_ns: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Definition {
    pub dependency: Dependency,
    pub implementation_hash: String,
    pub execution_interval: ExecutionInterval,
    pub inputs: Vec<Input>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Request {
    pub strategy_instance: String,
    pub instrument: u64,
    pub dependency: Dependency,
    pub interval: Interval,
}
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Key {
    pub instrument: u64,
    pub dependency: Dependency,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Node {
    pub key: Key,
    pub implementation_hash: String,
    pub execution_interval: ExecutionInterval,
    pub intervals: Vec<Interval>,
    pub consumers: BTreeSet<String>,
    pub inputs: Vec<Input>,
}
#[derive(Debug, Clone, Serialize)]
pub struct Plan {
    pub nodes: Vec<Node>,
}
impl Plan {
    pub fn id(&self) -> Result<String> {
        content_hash(&("dependency-plan-v1", self))
    }
}
fn visit(
    dependency: &Dependency,
    definitions: &BTreeMap<Dependency, Definition>,
    active: &mut BTreeSet<Dependency>,
    visited: &mut BTreeSet<Dependency>,
    order: &mut Vec<Dependency>,
) -> Result<()> {
    if visited.contains(dependency) {
        return Ok(());
    }
    if active.len() >= 256 || !active.insert(dependency.clone()) {
        return Err(Error::Invalid(
            "cyclic or excessive dependency depth".into(),
        ));
    }
    let definition = definitions.get(dependency).ok_or_else(|| {
        Error::Unready(format!("dependency implementation missing: {dependency:?}"))
    })?;
    if definition.implementation_hash.len() != 64
        || !definition
            .implementation_hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(Error::Invalid(
            "dependency implementation must have a pinned hash".into(),
        ));
    }
    for input in &definition.inputs {
        visit(&input.dependency, definitions, active, visited, order)?;
    }
    active.remove(dependency);
    visited.insert(dependency.clone());
    order.push(dependency.clone());
    Ok(())
}
fn merge(intervals: &mut Vec<Interval>, next: Interval) -> Result<()> {
    next.validate()?;
    intervals.push(next);
    intervals.sort_by_key(|i| (i.start, i.end));
    let mut merged: Vec<Interval> = Vec::new();
    for interval in intervals.iter() {
        if let Some(last) = merged.last_mut().filter(|last| interval.start <= last.end) {
            last.end = last.end.max(interval.end);
        } else {
            merged.push(*interval);
        }
    }
    if merged.len() > 4096 {
        return Err(Error::Capacity("dependency interval budget".into()));
    }
    *intervals = merged;
    Ok(())
}
/// Output is dependency-first. Disjoint requested intervals are not bridged.
/// extra_history_ns must come from the algorithm's declared warmup contract.
pub fn build(
    definitions: Vec<Definition>,
    requests: &[Request],
    maximum_nodes: usize,
) -> Result<Plan> {
    if definitions.len() > 1024
        || requests.is_empty()
        || requests.len() > 100000
        || maximum_nodes == 0
        || maximum_nodes > 100000
    {
        return Err(Error::Invalid("dependency plan bounds".into()));
    }
    let mut registry = BTreeMap::new();
    for mut definition in definitions {
        if definition.inputs.len() > 256 {
            return Err(Error::Capacity("dependency edge budget".into()));
        }
        definition.execution_interval.validate()?;
        definition.inputs.sort_by(|a, b| {
            a.dependency
                .cmp(&b.dependency)
                .then(a.extra_history_ns.cmp(&b.extra_history_ns))
        });
        if registry
            .insert(definition.dependency.clone(), definition)
            .is_some()
        {
            return Err(Error::Conflict("duplicate dependency definition".into()));
        }
    }
    let roots: BTreeSet<_> = requests.iter().map(|r| r.dependency.clone()).collect();
    let (mut active, mut visited, mut order) = (BTreeSet::new(), BTreeSet::new(), Vec::new());
    for root in roots {
        visit(&root, &registry, &mut active, &mut visited, &mut order)?;
    }
    let mut nodes: BTreeMap<Key, Node> = BTreeMap::new();
    for request in requests {
        if request.instrument == 0 || request.strategy_instance.is_empty() {
            return Err(Error::Invalid("dependency request scope missing".into()));
        }
        let key = Key {
            instrument: request.instrument,
            dependency: request.dependency.clone(),
        };
        let definition = &registry[&request.dependency];
        let node = nodes.entry(key.clone()).or_insert_with(|| Node {
            key,
            implementation_hash: definition.implementation_hash.clone(),
            execution_interval: definition.execution_interval,
            intervals: vec![],
            consumers: BTreeSet::new(),
            inputs: definition.inputs.clone(),
        });
        merge(&mut node.intervals, request.interval)?;
        node.consumers.insert(request.strategy_instance.clone());
        if nodes.len() > maximum_nodes {
            return Err(Error::Capacity("dependency node budget".into()));
        }
    }
    for dependency in order.iter().rev() {
        let parents: Vec<_> = nodes
            .values()
            .filter(|n| &n.key.dependency == dependency)
            .cloned()
            .collect();
        for parent in parents {
            for input in parent.inputs {
                let key = Key {
                    instrument: parent.key.instrument,
                    dependency: input.dependency.clone(),
                };
                let definition = &registry[&input.dependency];
                let node = nodes.entry(key.clone()).or_insert_with(|| Node {
                    key,
                    implementation_hash: definition.implementation_hash.clone(),
                    execution_interval: definition.execution_interval,
                    intervals: vec![],
                    consumers: BTreeSet::new(),
                    inputs: definition.inputs.clone(),
                });
                for interval in &parent.intervals {
                    let start = interval
                        .start
                        .checked_sub(input.extra_history_ns)
                        .ok_or_else(|| {
                            Error::Invalid("dependency history precedes clock epoch".into())
                        })?;
                    merge(
                        &mut node.intervals,
                        Interval {
                            start,
                            end: interval.end,
                        },
                    )?;
                }
                node.consumers.extend(parent.consumers.iter().cloned());
                if nodes.len() > maximum_nodes {
                    return Err(Error::Capacity("dependency node budget".into()));
                }
            }
        }
    }
    let ranks: BTreeMap<_, _> = order.into_iter().enumerate().map(|(i, d)| (d, i)).collect();
    let mut nodes: Vec<_> = nodes.into_values().collect();
    nodes.sort_by_key(|n| (ranks[&n.key.dependency], n.key.instrument));
    Ok(Plan { nodes })
}

#[cfg(test)]
mod tests {
    use super::*;
    fn definition(dependency: Dependency, inputs: Vec<Input>) -> Definition {
        Definition {
            dependency,
            implementation_hash: "a".repeat(64),
            execution_interval: ExecutionInterval::Events,
            inputs,
        }
    }
    fn input(dependency: Dependency, extra_history_ns: u64) -> Input {
        Input {
            dependency,
            extra_history_ns,
        }
    }
    fn request(
        strategy: &str,
        instrument: u64,
        dependency: Dependency,
        start: u64,
        end: u64,
    ) -> Request {
        Request {
            strategy_instance: strategy.into(),
            instrument,
            dependency,
            interval: Interval { start, end },
        }
    }
    #[test]
    fn shared_history_is_merged_without_cross_instrument_expansion() {
        let definitions = vec![
            definition(Dependency::Trades, vec![]),
            definition(Dependency::Quotes, vec![]),
            definition(Dependency::Bars(60), vec![input(Dependency::Trades, 5)]),
            definition(
                Dependency::Indicator("ema".into()),
                vec![input(Dependency::Bars(60), 10)],
            ),
        ];
        let requests = vec![
            request("first", 1, Dependency::Indicator("ema".into()), 100, 200),
            request("second", 1, Dependency::Bars(60), 180, 250),
            request("first", 1, Dependency::Indicator("ema".into()), 400, 500),
            request("other", 2, Dependency::Quotes, 100, 200),
        ];
        let plan = build(definitions.clone(), &requests, 4).unwrap();
        assert_eq!(plan.nodes.len(), 4);
        let trades = plan
            .nodes
            .iter()
            .find(|n| n.key.dependency == Dependency::Trades)
            .unwrap();
        assert_eq!(trades.key.instrument, 1);
        assert_eq!(
            trades.intervals,
            vec![
                Interval {
                    start: 85,
                    end: 250
                },
                Interval {
                    start: 385,
                    end: 500
                }
            ]
        );
        assert_eq!(
            trades.consumers,
            BTreeSet::from(["first".into(), "second".into()])
        );
        assert!(!plan
            .nodes
            .iter()
            .any(|n| n.key.instrument == 2 && n.key.dependency != Dependency::Quotes));
        for (index, node) in plan.nodes.iter().enumerate() {
            for input in &node.inputs {
                assert!(plan.nodes[..index]
                    .iter()
                    .any(|n| n.key.instrument == node.key.instrument
                        && n.key.dependency == input.dependency));
            }
        }
        let mut reversed = requests.clone();
        reversed.reverse();
        let mut reversed_definitions = definitions.clone();
        reversed_definitions.reverse();
        assert_eq!(
            plan.id().unwrap(),
            build(reversed_definitions, &reversed, 4)
                .unwrap()
                .id()
                .unwrap()
        );
        assert!(matches!(
            build(definitions, &requests, 3),
            Err(Error::Capacity(_))
        ));
    }
    #[test]
    fn missing_cyclic_and_unpinned_dependencies_fail_closed() {
        let requests = [request("s", 1, Dependency::Bars(60), 100, 200)];
        assert!(matches!(
            build(vec![], &requests, 10),
            Err(Error::Unready(_))
        ));
        let cycle = vec![
            definition(Dependency::Bars(60), vec![input(Dependency::Trades, 0)]),
            definition(Dependency::Trades, vec![input(Dependency::Bars(60), 0)]),
        ];
        assert!(matches!(
            build(cycle, &requests, 10),
            Err(Error::Invalid(_))
        ));
        let mut unpinned = definition(Dependency::Bars(60), vec![]);
        unpinned.implementation_hash = "unknown".into();
        assert!(matches!(
            build(vec![unpinned], &requests, 10),
            Err(Error::Invalid(_))
        ));
        let duplicate = definition(Dependency::Bars(60), vec![]);
        assert!(matches!(
            build(vec![duplicate.clone(), duplicate], &requests, 10),
            Err(Error::Conflict(_))
        ));
    }
    #[test]
    fn invalid_clock_scope_and_history_are_rejected() {
        let definitions = vec![
            definition(Dependency::Bars(60), vec![input(Dependency::Trades, 101)]),
            definition(Dependency::Trades, vec![]),
        ];
        for request in [
            request("s", 1, Dependency::Bars(60), 100, 200),
            request("s", 1, Dependency::Trades, 200, 200),
            request("", 1, Dependency::Trades, 100, 200),
            request("s", 0, Dependency::Trades, 100, 200),
        ] {
            assert!(matches!(
                build(definitions.clone(), &[request], 10),
                Err(Error::Invalid(_))
            ));
        }
    }
    #[test]
    fn computation_cadence_is_required_and_changes_plan_identity() {
        let request = [request(
            "signal",
            1,
            Dependency::MarketSignal("stream".into()),
            100,
            200,
        )];
        let mut definition = definition(Dependency::MarketSignal("stream".into()), vec![]);
        let event_plan = build(vec![definition.clone()], &request, 1).unwrap();
        assert_eq!(
            event_plan.nodes[0].execution_interval,
            ExecutionInterval::Events
        );
        definition.execution_interval = ExecutionInterval::Fixed(100_000_000);
        let bar_plan = build(vec![definition.clone()], &request, 1).unwrap();
        assert_ne!(event_plan.id().unwrap(), bar_plan.id().unwrap());
        assert_eq!(
            bar_plan.nodes[0].execution_interval,
            ExecutionInterval::Fixed(100_000_000)
        );
        definition.execution_interval = ExecutionInterval::Fixed(50_000_000);
        assert!(build(vec![definition], &request, 1).is_err());
        let missing = serde_json::json!({
            "dependency": {"MarketSignal":"stream"},
            "implementation_hash": "a".repeat(64),
            "inputs": []
        });
        assert!(serde_json::from_value::<Definition>(missing).is_err());
    }
}
