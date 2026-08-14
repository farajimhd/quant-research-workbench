use crate::capability_catalog::{computation_capability_catalog, ExecutionScope};
use crate::indicator_catalog::{indicator_catalog, IndicatorCatalogEntry};
use crate::signal_catalog::{signal_catalog, SignalMethodEntry};
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

pub const DEFINITION_CATALOG_SCHEMA_VERSION: u16 = 1;

#[derive(Clone, Debug, Serialize)]
pub struct RegistryPresentation {
    pub kind_label: &'static str,
    pub icon: &'static str,
    pub accent: &'static str,
}

#[derive(Clone, Debug, Serialize)]
pub struct RegistryParameterDefinition {
    pub parameter_id: &'static str,
    pub label: &'static str,
    pub value_type: &'static str,
    pub allowed_values: Vec<String>,
    pub multiple: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct QmdRegistryDefinition {
    pub registry_id: String,
    pub kind: &'static str,
    pub label: String,
    pub description: String,
    pub owner: &'static str,
    pub version: u16,
    pub status: &'static str,
    pub tags: Vec<String>,
    pub configurable: bool,
    pub configuration_mode: &'static str,
    pub input_field_ids: Vec<String>,
    pub output_field_ids: Vec<String>,
    pub execution_scopes: Vec<ExecutionScope>,
    pub parameters: Vec<RegistryParameterDefinition>,
    pub producer_id: Option<String>,
    pub presentation: RegistryPresentation,
}

#[derive(Clone, Debug, Serialize)]
pub struct QmdDefinitionCatalog {
    pub schema_version: u16,
    pub authority: &'static str,
    pub provider: &'static str,
    pub definitions: Vec<QmdRegistryDefinition>,
}

fn readable_label(value: &str) -> String {
    value
        .split(['_', '.', '-'])
        .filter(|part| !part.is_empty())
        .map(|part| {
            let mut chars = part.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn field_id(raw: &str) -> String {
    if raw.contains('.') {
        raw.to_string()
    } else {
        format!("qmd.field.{raw}")
    }
}

fn signal_field_id(signal_key: &str, raw: &str) -> String {
    format!("signal.{signal_key}.{raw}")
}

fn status_for_capability(status: &str) -> &'static str {
    match status {
        "implemented" | "reference_only" => "implemented",
        "planned_realtime" | "strategy_specific" | "offline_only" => "planned",
        _ => "integration_pending",
    }
}

fn configuration_parameters(
    configurable: bool,
    scopes: &[ExecutionScope],
    timeframes: &[&str],
) -> Vec<RegistryParameterDefinition> {
    if !configurable {
        return Vec::new();
    }
    let mut parameters = vec![RegistryParameterDefinition {
        parameter_id: "execution_scope",
        label: "Execution scope",
        value_type: "category",
        allowed_values: scopes
            .iter()
            .map(|scope| {
                serde_json::to_value(scope)
                    .unwrap()
                    .as_str()
                    .unwrap()
                    .to_string()
            })
            .collect(),
        multiple: false,
    }];
    if !timeframes.is_empty() {
        parameters.push(RegistryParameterDefinition {
            parameter_id: "timeframes",
            label: "Timeframes",
            value_type: "category",
            allowed_values: timeframes
                .iter()
                .map(|value| (*value).to_string())
                .collect(),
            multiple: true,
        });
    }
    parameters
}

fn indicator_description(entry: &IndicatorCatalogEntry) -> String {
    entry.rationale.to_string()
}

fn signal_description(entry: &SignalMethodEntry) -> String {
    entry.rationale.to_string()
}

fn field_definition(
    registry_id: String,
    producer_id: Option<String>,
    status: &'static str,
) -> QmdRegistryDefinition {
    QmdRegistryDefinition {
        label: readable_label(registry_id.rsplit('.').next().unwrap_or(&registry_id)),
        description: "Typed QMD value available through its registered producer and causal clock."
            .to_string(),
        registry_id,
        kind: "field",
        owner: "qmd_core",
        version: 1,
        status,
        tags: vec!["qmd".to_string(), "typed_value".to_string()],
        configurable: true,
        configuration_mode: "select_reference",
        input_field_ids: Vec::new(),
        output_field_ids: Vec::new(),
        execution_scopes: Vec::new(),
        parameters: Vec::new(),
        producer_id,
        presentation: RegistryPresentation {
            kind_label: "Field",
            icon: "database",
            accent: "blue",
        },
    }
}

pub fn definition_catalog() -> QmdDefinitionCatalog {
    let indicators = indicator_catalog()
        .iter()
        .map(|entry| (entry.key, entry))
        .collect::<BTreeMap<_, _>>();
    let signals = signal_catalog()
        .iter()
        .map(|entry| (entry.key, entry))
        .collect::<BTreeMap<_, _>>();
    let mut definitions = Vec::new();
    let mut fields = BTreeMap::<String, (Option<String>, &'static str)>::new();

    for capability in computation_capability_catalog() {
        let configurable = matches!(
            capability.configuration_policy,
            crate::capability_catalog::ConfigurationPolicy::Configurable
        );
        if capability.kind == "primitive" {
            let output_field_ids = capability
                .outputs
                .iter()
                .map(|raw| field_id(raw))
                .collect::<Vec<_>>();
            for field in &output_field_ids {
                fields.entry(field.clone()).or_insert((
                    Some(format!("qmd.processing_step.{}", capability.key)),
                    "implemented",
                ));
            }
            definitions.push(QmdRegistryDefinition {
                registry_id: format!("qmd.processing_step.{}", capability.key),
                kind: "processing_step",
                label: capability.label.to_string(),
                description:
                    "Required QMD event-path processing with compiled implementation authority."
                        .to_string(),
                owner: "qmd_core",
                version: capability.implementation_version,
                status: "implemented",
                tags: vec!["qmd".to_string(), "universal_ingest".to_string()],
                configurable: false,
                configuration_mode: "locked",
                input_field_ids: capability.inputs.iter().map(|raw| field_id(raw)).collect(),
                output_field_ids,
                execution_scopes: capability.allowed_scopes,
                parameters: Vec::new(),
                producer_id: None,
                presentation: RegistryPresentation {
                    kind_label: "Processing step",
                    icon: "cable",
                    accent: "cyan",
                },
            });
            continue;
        }

        if capability.kind == "indicator_family" {
            let entry = indicators[capability.key];
            let registry_id = format!("qmd.derivation.{}", capability.key);
            let output_field_ids = capability
                .outputs
                .iter()
                .map(|raw| field_id(raw))
                .collect::<Vec<_>>();
            let input_field_ids = capability
                .inputs
                .iter()
                .map(|raw| field_id(raw))
                .collect::<Vec<_>>();
            let status = status_for_capability(capability.implementation_status);
            for field in &input_field_ids {
                fields.entry(field.clone()).or_insert((None, status));
            }
            for field in &output_field_ids {
                fields
                    .entry(field.clone())
                    .or_insert((Some(registry_id.clone()), status));
            }
            definitions.push(QmdRegistryDefinition {
                registry_id,
                kind: "derivation",
                label: capability.label.to_string(),
                description: indicator_description(entry),
                owner: "qmd_core",
                version: capability.implementation_version,
                status,
                tags: vec![
                    "qmd".to_string(),
                    "indicator".to_string(),
                    format!("{:?}", entry.category).to_lowercase(),
                ],
                configurable,
                configuration_mode: if configurable {
                    "parameterized_reference"
                } else {
                    "locked"
                },
                input_field_ids,
                output_field_ids,
                execution_scopes: capability.allowed_scopes.clone(),
                parameters: configuration_parameters(
                    configurable,
                    &capability.allowed_scopes,
                    capability.timeframes,
                ),
                producer_id: None,
                presentation: RegistryPresentation {
                    kind_label: "Derivation",
                    icon: "sigma",
                    accent: "violet",
                },
            });
            continue;
        }

        if capability.kind == "market_observation" {
            let entry = signals[capability.key];
            let registry_id = format!("qmd.signal.{}", capability.key);
            let input_field_ids = entry
                .required_bar_fields
                .iter()
                .chain(entry.required_indicator_fields.iter())
                .chain(entry.required_reference_fields.iter())
                .map(|raw| field_id(raw))
                .collect::<Vec<_>>();
            let output_field_ids = entry
                .emits
                .iter()
                .map(|raw| signal_field_id(entry.key, raw))
                .collect::<Vec<_>>();
            for field in &input_field_ids {
                fields.entry(field.clone()).or_insert((None, "implemented"));
            }
            for field in &output_field_ids {
                fields
                    .entry(field.clone())
                    .or_insert((Some(registry_id.clone()), "implemented"));
            }
            definitions.push(QmdRegistryDefinition {
                registry_id,
                kind: "signal",
                label: capability.label.to_string(),
                description: signal_description(entry),
                owner: "qmd_core",
                version: entry.signal_version,
                status: "implemented",
                tags: vec![
                    "qmd".to_string(),
                    "market_signal".to_string(),
                    format!("{:?}", entry.category).to_lowercase(),
                ],
                configurable,
                configuration_mode: "parameterized_reference",
                input_field_ids,
                output_field_ids,
                execution_scopes: capability.allowed_scopes.clone(),
                parameters: configuration_parameters(
                    configurable,
                    &capability.allowed_scopes,
                    capability.timeframes,
                ),
                producer_id: None,
                presentation: RegistryPresentation {
                    kind_label: "Signal",
                    icon: "activity",
                    accent: "rose",
                },
            });
        }
    }

    let referenced_inputs = definitions
        .iter()
        .flat_map(|definition| definition.input_field_ids.iter().cloned())
        .collect::<BTreeSet<_>>();
    for field in referenced_inputs {
        fields.entry(field).or_insert((None, "implemented"));
    }
    definitions.extend(
        fields
            .into_iter()
            .map(|(id, (producer, status))| field_definition(id, producer, status)),
    );
    definitions.sort_by(|left, right| left.registry_id.cmp(&right.registry_id));

    QmdDefinitionCatalog {
        schema_version: DEFINITION_CATALOG_SCHEMA_VERSION,
        authority: "qmd_core_definition_registry",
        provider: "qmd-gateway",
        definitions,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn definitions_have_unique_ids_and_closed_relationships() {
        let catalog = definition_catalog();
        let ids = catalog
            .definitions
            .iter()
            .map(|definition| definition.registry_id.clone())
            .collect::<BTreeSet<_>>();
        assert_eq!(ids.len(), catalog.definitions.len());
        for definition in &catalog.definitions {
            assert!(definition.version > 0);
            assert!(!definition.label.is_empty());
            for field_id in definition
                .input_field_ids
                .iter()
                .chain(definition.output_field_ids.iter())
            {
                assert!(ids.contains(field_id), "missing FieldDefinition {field_id}");
            }
        }
    }

    #[test]
    fn configuration_modes_preserve_qmd_scope_authority() {
        let catalog = definition_catalog();
        assert!(catalog.definitions.iter().any(|definition| {
            definition.kind == "processing_step"
                && !definition.configurable
                && definition.configuration_mode == "locked"
        }));
        assert!(catalog.definitions.iter().any(|definition| {
            definition.kind == "derivation"
                && definition.configurable
                && definition.configuration_mode == "parameterized_reference"
        }));
        assert!(catalog
            .definitions
            .iter()
            .any(|definition| { definition.kind == "signal" && definition.configurable }));
    }
}
