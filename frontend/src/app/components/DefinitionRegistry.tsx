import { createContext, useContext, useMemo, type ReactNode } from "react";

export type RegistryPresentation = {
  accent: string;
  icon: string;
  kind_label: string;
};

export type RegistryTypeDefinition = {
  accent: string;
  configuration_mode: string;
  description: string;
  icon: string;
  kind: string;
  label: string;
  user_facing: boolean;
};

export type RegistryDefinition = {
  configurable: boolean;
  configuration_binding_id?: string;
  configuration_mode: string;
  description: string;
  documentation?: {
    available_when: string;
    calculation_summary: string;
    entity_grain: string;
    freshness_summary: string;
    input_field_ids: string[];
    null_behavior: string;
    source_summary: string;
    timeframes: string[];
    unit: string;
    update_cadence: string;
    value_type: string;
  };
  kind: string;
  label: string;
  owner: string;
  presentation_label?: string;
  execution_scopes?: string[];
  input_field_ids?: string[];
  output_field_ids?: string[];
  parameters?: Array<{
    default?: unknown;
    description?: string;
    label?: string;
    name: string;
    required?: boolean;
    type?: string;
    unit?: string;
  }>;
  producer_id?: string;
  presentation: RegistryPresentation;
  registry_id: string;
  relationships?: Record<string, string[]>;
  status: string;
  tags: string[];
  version: number;
};

export type ConfigurationBindingDefinition = {
  binding_id: string;
  configuration_mode: string;
  configuration_path: string;
  editable_fields: string[];
  identity_field: string;
  kind: string;
  reference_fields: string[];
};

export type InformationRegistry = {
  aliases: Array<{ alias_id: string; registry_id: string }>;
  authority: string;
  configuration_bindings: ConfigurationBindingDefinition[];
  content_hash: string;
  counts: {
    aliases: number;
    configurable: number;
    configuration_bindings: number;
    definitions: number;
    types: number;
  };
  definitions: RegistryDefinition[];
  qmd_authority: string;
  schema_version: number;
  types: RegistryTypeDefinition[];
};

type DefinitionRegistryContextValue = {
  bindings: Map<string, ConfigurationBindingDefinition>;
  definitions: Map<string, RegistryDefinition>;
  resolve: (registryId: string) => RegistryDefinition | undefined;
  types: Map<string, RegistryTypeDefinition>;
};

const DefinitionRegistryContext = createContext<DefinitionRegistryContextValue | null>(null);

export function validateInformationRegistry(payload: InformationRegistry): InformationRegistry {
  if (payload.schema_version !== 1 || payload.authority !== "application_information_registry") {
    throw new Error("The application information registry contract is unavailable or unsupported.");
  }
  if (payload.qmd_authority !== "qmd_core_definition_registry") {
    throw new Error("QMD is not the active definition authority.");
  }
  if (!payload.types.length || !payload.definitions.length) {
    throw new Error("The application information registry is empty.");
  }
  const typeIds = new Set<string>();
  for (const definition of payload.types) {
    if (!definition.kind || typeIds.has(definition.kind)) throw new Error(`Invalid registry type: ${definition.kind || "missing kind"}`);
    typeIds.add(definition.kind);
  }
  const definitionIds = new Set<string>();
  for (const definition of payload.definitions) {
    if (!definition.registry_id || definitionIds.has(definition.registry_id)) throw new Error(`Invalid registry definition: ${definition.registry_id || "missing identity"}`);
    if (!typeIds.has(definition.kind)) throw new Error(`Unknown registry kind ${definition.kind} for ${definition.registry_id}`);
    definitionIds.add(definition.registry_id);
  }
  return payload;
}

export function DefinitionRegistryProvider({ children, registry }: { children: ReactNode; registry: InformationRegistry }) {
  const value = useMemo<DefinitionRegistryContextValue>(() => {
    const checked = validateInformationRegistry(registry);
    const types = new Map(checked.types.map((row) => [row.kind, row]));
    const definitions = new Map(checked.definitions.map((row) => [row.registry_id, row]));
    const bindings = new Map(checked.configuration_bindings.map((row) => [row.binding_id, row]));
    const aliases = new Map(checked.aliases.map((row) => [row.alias_id, row.registry_id]));
    return {
      bindings,
      definitions,
      resolve: (registryId: string) => definitions.get(aliases.get(registryId) ?? registryId),
      types,
    };
  }, [registry]);
  return <DefinitionRegistryContext.Provider value={value}>{children}</DefinitionRegistryContext.Provider>;
}

export function useRegistryPresentation(kind: string, registryId?: string) {
  const registry = useContext(DefinitionRegistryContext);
  if (!registry) throw new Error("DefinitionRegistryProvider is required for registry-backed presentation.");
  const definition = registryId ? registry.resolve(registryId) : undefined;
  if (registryId && !definition) throw new Error(`Unknown registry definition: ${registryId}`);
  const resolvedKind = definition?.kind ?? kind;
  const type = registry.types.get(resolvedKind);
  if (!type) throw new Error(`Unknown registry presentation kind: ${kind}`);
  const binding = definition?.configuration_binding_id ? registry.bindings.get(definition.configuration_binding_id) : undefined;
  return {
    accent: definition?.presentation.accent ?? type.accent,
    binding,
    configurable: definition?.configurable ?? type.configuration_mode !== "locked",
    configurationMode: definition?.configuration_mode ?? type.configuration_mode,
    definition,
    icon: definition?.presentation.icon ?? type.icon,
    kind: resolvedKind,
    kindLabel: definition?.presentation.kind_label ?? type.label,
  };
}
