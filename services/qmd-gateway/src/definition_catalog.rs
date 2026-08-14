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
pub struct RegistryDocumentation {
    pub source_summary: String,
    pub calculation_summary: String,
    pub input_field_ids: Vec<String>,
    pub timeframes: Vec<String>,
    pub value_type: String,
    pub unit: String,
    pub entity_grain: String,
    pub update_cadence: String,
    pub available_when: String,
    pub freshness_summary: String,
    pub null_behavior: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct QmdRegistryDefinition {
    pub registry_id: String,
    pub kind: &'static str,
    pub label: String,
    pub presentation_label: String,
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
    pub documentation: RegistryDocumentation,
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
        .enumerate()
        .map(|(index, part)| {
            if index > 0
                && matches!(
                    part,
                    "and" | "at" | "by" | "for" | "from" | "of" | "per" | "to"
                )
            {
                return part.to_string();
            }
            match part.to_ascii_lowercase().as_str() {
                "ad" => return "AD".to_string(),
                "adx" => return "ADX".to_string(),
                "alma" => return "ALMA".to_string(),
                "apo" => return "APO".to_string(),
                "atr" => return "ATR".to_string(),
                "avg" => return "Average".to_string(),
                "bps" => return "BPS".to_string(),
                "cci" => return "CCI".to_string(),
                "cdl" => return "CDL".to_string(),
                "clickhouse" => return "ClickHouse".to_string(),
                "cmf" => return "CMF".to_string(),
                "cmo" => return "CMO".to_string(),
                "conid" => return "CONID".to_string(),
                "dema" => return "DEMA".to_string(),
                "di" => return "DI".to_string(),
                "dm" => return "DM".to_string(),
                "ema" => return "EMA".to_string(),
                "eom" => return "EOM".to_string(),
                "hma" => return "HMA".to_string(),
                "ht" => return "HT".to_string(),
                "id" => return "ID".to_string(),
                "ibkr" => return "IBKR".to_string(),
                "ipo" => return "IPO".to_string(),
                "kama" => return "KAMA".to_string(),
                "kst" => return "KST".to_string(),
                "kvo" => return "KVO".to_string(),
                "level1" => return "Level 1".to_string(),
                "luld" => return "LULD".to_string(),
                "ma" => return "MA".to_string(),
                "macd" => return "MACD".to_string(),
                "mfi" => return "MFI".to_string(),
                "mom" => return "Momentum".to_string(),
                "ms" => return "ms".to_string(),
                "natr" => return "NATR".to_string(),
                "nbbo" => return "NBBO".to_string(),
                "nvi" => return "NVI".to_string(),
                "obv" => return "OBV".to_string(),
                "ofi" => return "OFI".to_string(),
                "pct" => return "%".to_string(),
                "ppo" => return "PPO".to_string(),
                "psar" => return "PSAR".to_string(),
                "pvi" => return "PVI".to_string(),
                "pvt" => return "PVT".to_string(),
                "qmd" => return "QMD".to_string(),
                "rest" => return "REST".to_string(),
                "roc" => return "ROC".to_string(),
                "rsi" => return "RSI".to_string(),
                "sec" => return "SEC".to_string(),
                "sip" => return "SIP".to_string(),
                "sma" => return "SMA".to_string(),
                "std" => return "Standard Deviation".to_string(),
                "talib" => return "TA-Lib".to_string(),
                "tf" => return "Timeframe".to_string(),
                "utc" => return "UTC".to_string(),
                "vs" => return "vs".to_string(),
                "vwap" => return "VWAP".to_string(),
                "xbrl" => return "XBRL".to_string(),
                "zscore" => return "Z-Score".to_string(),
                _ => {}
            }
            let mut chars = part.chars();
            match chars.next() {
                Some(first) => first.to_uppercase().collect::<String>() + chars.as_str(),
                None => String::new(),
            }
        })
        .collect::<Vec<_>>()
        .join(" ")
}

fn field_presentation_label(registry_id: &str) -> String {
    if let Some(signal_path) = registry_id.strip_prefix("signal.") {
        return readable_label(signal_path);
    }
    let leaf = registry_id.rsplit('.').next().unwrap_or(registry_id);
    match leaf {
        "ad" => "Accumulation/Distribution".to_string(),
        "adosc" => "Chaikin A/D Oscillator".to_string(),
        "open" => "Bar Open".to_string(),
        "high" => "Bar High".to_string(),
        "low" => "Bar Low".to_string(),
        "close" => "Bar Close".to_string(),
        "volume" => "Bar Volume".to_string(),
        "ht_dcperiod" => "Hilbert Transform Dominant Cycle Period".to_string(),
        "ht_dcphase" => "Hilbert Transform Dominant Cycle Phase".to_string(),
        "ht_phasor" => "Hilbert Transform Phasor".to_string(),
        "ht_sine" => "Hilbert Transform Sine".to_string(),
        "ht_trendline" => "Hilbert Transform Trendline".to_string(),
        "ht_trendmode" => "Hilbert Transform Trend Mode".to_string(),
        _ => readable_label(leaf),
    }
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

fn documentation(
    source_summary: String,
    calculation_summary: String,
    input_field_ids: Vec<String>,
    timeframes: &[&str],
    value_type: &str,
    unit: &str,
    entity_grain: &str,
    update_cadence: &str,
    available_when: String,
) -> RegistryDocumentation {
    RegistryDocumentation {
        source_summary,
        calculation_summary,
        input_field_ids,
        timeframes: timeframes
            .iter()
            .map(|value| (*value).to_string())
            .collect(),
        value_type: value_type.to_string(),
        unit: unit.to_string(),
        entity_grain: entity_grain.to_string(),
        update_cadence: update_cadence.to_string(),
        available_when,
        freshness_summary:
            "Freshness follows the producer's causal market clock and implementation revision."
                .to_string(),
        null_behavior: "Unavailable required inputs do not produce a substituted value."
            .to_string(),
    }
}

fn field_definition(
    registry_id: String,
    producer_id: Option<String>,
    status: &'static str,
    producer_documentation: Option<RegistryDocumentation>,
) -> QmdRegistryDefinition {
    let registered_documentation = producer_documentation
        .map(|mut producer| {
            producer.value_type = "producer_defined".to_string();
            producer.unit = "producer_defined".to_string();
            producer
        })
        .unwrap_or_else(|| {
            documentation(
                "A typed input accepted by QMD from its registered market or reference source."
                    .to_string(),
                "Uses the causally available source value without an additional field-level calculation."
                    .to_string(),
                Vec::new(),
                &[],
                "producer_defined",
                "producer_defined",
                "security_timeframe",
                "producer cadence",
                "After the source value is accepted at the QMD market clock.".to_string(),
            )
        });
    QmdRegistryDefinition {
        label: readable_label(registry_id.rsplit('.').next().unwrap_or(&registry_id)),
        presentation_label: field_presentation_label(&registry_id),
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
        documentation: registered_documentation,
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
            let input_field_ids = capability
                .inputs
                .iter()
                .map(|raw| field_id(raw))
                .collect::<Vec<_>>();
            let registered_documentation = documentation(
                "Canonical quote, trade, identity, sequence, and market-clock inputs accepted by QMD."
                    .to_string(),
                format!(
                    "{} transforms {} into {}.",
                    capability.label,
                    capability.inputs.join(", "),
                    capability.outputs.join(", ")
                ),
                input_field_ids.clone(),
                capability.timeframes,
                "record",
                "producer_defined",
                "market_event",
                capability.cadence,
                format!(
                    "After {} completes for the accepted market event.",
                    capability.label
                ),
            );
            definitions.push(QmdRegistryDefinition {
                registry_id: format!("qmd.processing_step.{}", capability.key),
                kind: "processing_step",
                label: capability.label.to_string(),
                presentation_label: capability.label.to_string(),
                description:
                    "Required QMD event-path processing with compiled implementation authority."
                        .to_string(),
                owner: "qmd_core",
                version: capability.implementation_version,
                status: "implemented",
                tags: vec!["qmd".to_string(), "universal_ingest".to_string()],
                configurable: false,
                configuration_mode: "locked",
                input_field_ids,
                output_field_ids,
                execution_scopes: capability.allowed_scopes,
                parameters: Vec::new(),
                producer_id: None,
                presentation: RegistryPresentation {
                    kind_label: "Processing step",
                    icon: "cable",
                    accent: "cyan",
                },
                documentation: registered_documentation,
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
            let registered_documentation = documentation(
                format!(
                    "QMD {} inputs: {}.",
                    capability.label,
                    entry.inputs.join(", ")
                ),
                entry.rationale.to_string(),
                input_field_ids.clone(),
                capability.timeframes,
                "number",
                "producer_defined",
                "security_timeframe",
                capability.cadence,
                format!(
                    "After the required inputs and warm-up for {} are causally complete.",
                    capability.label
                ),
            );
            definitions.push(QmdRegistryDefinition {
                registry_id,
                kind: "derivation",
                label: capability.label.to_string(),
                presentation_label: capability.label.to_string(),
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
                documentation: registered_documentation,
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
            let calculation_summary = format!(
                "Triggers when {} Confirmation: {} Rejected when {}",
                entry.trigger_rules.join("; "),
                entry.confirmation_rules.join("; "),
                entry.reject_rules.join("; ")
            );
            let registered_documentation = documentation(
                entry.input_basis.to_string(),
                calculation_summary,
                input_field_ids.clone(),
                entry.working_timeframes,
                "event",
                "signal_state",
                "security_event",
                entry.publication_cadence,
                format!(
                    "When {} and its confirmations are causally satisfied.",
                    entry.label
                ),
            );
            definitions.push(QmdRegistryDefinition {
                registry_id,
                kind: "signal",
                label: capability.label.to_string(),
                presentation_label: capability.label.to_string(),
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
                documentation: registered_documentation,
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
    let producer_documentation = definitions
        .iter()
        .map(|definition| {
            (
                definition.registry_id.clone(),
                definition.documentation.clone(),
            )
        })
        .collect::<BTreeMap<_, _>>();
    definitions.extend(fields.into_iter().map(|(id, (producer, status))| {
        let documentation = producer
            .as_ref()
            .and_then(|producer_id| producer_documentation.get(producer_id))
            .cloned();
        field_definition(id, producer, status, documentation)
    }));
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
            assert!(
                !definition.presentation_label.trim().is_empty(),
                "missing presentation label for {}",
                definition.registry_id
            );
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
        let data_definitions = catalog
            .definitions
            .iter()
            .filter(|definition| matches!(definition.kind, "field" | "derivation" | "signal"))
            .collect::<Vec<_>>();
        let presentation_labels = data_definitions
            .iter()
            .map(|definition| definition.presentation_label.as_str())
            .collect::<BTreeSet<_>>();
        assert_eq!(presentation_labels.len(), data_definitions.len());
        let ambiguous = BTreeSet::from([
            "Date",
            "Days to Event",
            "Score",
            "Confidence",
            "Direction",
            "Clock",
            "Status",
            "Payload",
            "Vector",
            "Value",
            "State",
            "Count",
            "Close",
            "Open",
            "High",
            "Low",
        ]);
        for definition in data_definitions {
            assert!(
                !ambiguous.contains(definition.presentation_label.as_str()),
                "context-free presentation label {} for {}",
                definition.presentation_label,
                definition.registry_id
            );
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

    #[test]
    fn definitions_publish_operator_source_and_calculation_documentation() {
        let catalog = definition_catalog();
        for definition in &catalog.definitions {
            assert!(
                !definition.documentation.source_summary.trim().is_empty(),
                "missing source documentation for {}",
                definition.registry_id
            );
            assert!(
                !definition
                    .documentation
                    .calculation_summary
                    .trim()
                    .is_empty(),
                "missing calculation documentation for {}",
                definition.registry_id
            );
            assert!(
                !definition.documentation.available_when.trim().is_empty(),
                "missing availability documentation for {}",
                definition.registry_id
            );
        }
        let derived_field = catalog
            .definitions
            .iter()
            .find(|definition| definition.kind == "field" && definition.producer_id.is_some())
            .expect("expected a producer-backed QMD field");
        assert!(!derived_field.documentation.input_field_ids.is_empty());
        assert_eq!(
            catalog
                .definitions
                .iter()
                .find(|definition| definition.registry_id == "qmd.field.close")
                .expect("close field")
                .presentation_label,
            "Bar Close"
        );
        assert!(catalog
            .definitions
            .iter()
            .filter(|definition| {
                definition.registry_id.starts_with("signal.")
                    && definition.registry_id.ends_with(".score")
            })
            .all(|definition| definition.presentation_label != "Score"));
    }
}
