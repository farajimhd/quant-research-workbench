import { clearConfigurationSession, readConfigurationSession, writeConfigurationSession } from "../../app/configurationSession";
import { dataFieldRuleDefinitions, type DataFieldDefinition } from "../../pages/DataConfigurationPages";
import type { TradingActionsConfiguration } from "../../pages/TradingActionsPage";
import type {
  Draft,
  Mandate,
  RuleExpression,
  RuleSetDefinition,
  RuntimeMode,
  SignalStreamConfig,
  StrategyLifecycle,
  StrategyProfile,
  StrategyRunPlan,
  WatchlistConfig,
  WatchUniverse,
} from "./contracts";
export function normalizeDraft(payload: any): Draft {
  const runPlans = payload?.run_plans ?? payload?.assignments ?? { plans: [], universes: [] };
  const strategy = payload?.strategy ?? {};
  const marketDiscovery = payload?.market_discovery ?? { security_universe: {}, core_scan: { inclusion_rule_sets: [], inclusion_operator: "all", ranking_field: "market.liquidity_rank", ranking_direction: "descending", maximum_size: 250, columns: [] }, calculation_catalog: [], classifications: [], field_catalog: [], column_catalog: [], rule_sets: [], watchlists: [], signal_streams: [] };
  const normalizeRuleSet = (ruleSet: any) => ({
    ...ruleSet,
    conditions: (ruleSet.conditions ?? []).map((condition: any) => ({
      ...condition,
      left_interval: condition.left_interval ?? condition.left_timeframe ?? "",
      right_interval: condition.right_interval ?? condition.right_timeframe ?? "",
      comparator: ({
        equal: "equals",
        greater_than_or_equal: "greater_or_equal",
        less_than_or_equal: "less_or_equal",
      } as Record<string, string>)[condition.comparator] ?? condition.comparator,
    })),
  });
  const normalizeDataField = (dataField: any): DataFieldDefinition => {
    const sourceId = String(dataField.outputs?.[0]?.source_id ?? "");
    const legacyInterval = String(dataField.context?.timeframes?.[0] ?? "");
    const fixedWindow = /(?:^|[_.])(\d+)(ms|s|m|h|d)$/.exec(sourceId.toLowerCase());
    const currentSources = new Set(["market.last_price", "market.spread_bps", "market.event_at", "market.event_age_ms", "market.quality_state", "market.quality_flags", "market.degradation_reason", "market.liquidity_rank", "market.liquidity_score"]);
    const anchoredSources = new Set(["market.previous_close", "market.change_pct", "market.volume", "market.relative_volume", "indicator.vwap.value"]);
    const dimension = dataField.context?.dimension_kind
      ? dataField.context
      : fixedWindow
        ? { dimension_kind: "rolling_window", window: `${fixedWindow[1]}${fixedWindow[2]}`, window_configurable: false }
        : anchoredSources.has(sourceId)
          ? { anchor: "market_session", dimension_kind: "anchored" }
          : currentSources.has(sourceId) || legacyInterval === "event"
            ? { as_of: "evaluation_clock", dimension_kind: "as_of" }
            : legacyInterval && !["session", "filing", "settlement", "1d"].includes(legacyInterval)
              ? { available_intervals: [legacyInterval], dimension_kind: "interval", interval_required_when_used: true }
              : { as_of: legacyInterval === "filing" ? "latest_available_filing" : legacyInterval === "settlement" ? "latest_available_settlement" : "latest_available_publication", dimension_kind: "as_of" };
    return {
      ...dataField,
      context: {
        ...dimension,
        allowed_scopes: dataField.context?.allowed_scopes ?? [],
        available_intervals: dataField.context?.available_intervals ?? dimension.available_intervals ?? [],
        execution_scope: dataField.context?.execution_scope ?? "focused",
        update_cadence: dataField.context?.update_cadence ?? "producer cadence",
      },
      outputs: (dataField.outputs ?? []).map((output: any) => ({ ...output, context_interval: "" })),
    };
  };
  const normalizedDataFields: DataFieldDefinition[] = (marketDiscovery.data_fields ?? []).map(normalizeDataField);
  const dataFieldContextByRef = new Map<string, DataFieldDefinition["context"]>(normalizedDataFields.flatMap((dataField: DataFieldDefinition) => dataField.outputs.map((output) => [output.field_ref, dataField.context] as const)));
  const normalizeProfile = (profile: any) => {
    const phaseModes = {
      initial_entry: "automatic",
      manage: "automatic",
      reentry: profile.lifecycle?.reentry?.enabled === false ? "manual" : "automatic",
      exit: "automatic",
      ...(profile.lifecycle?.phase_modes ?? {}),
    };
    const profileWithoutComposition = { ...profile };
    delete profileWithoutComposition.composition;
    delete profileWithoutComposition.rule_set_catalog;
    const side = profile.lifecycle?.trading_behavior?.side === "short" ? "short" : "long";
    const initialEntry = profile.lifecycle?.initial_entry ?? {};
    const reentry = profile.lifecycle?.reentry ?? {};
    const exit = profile.lifecycle?.exit ?? { rule_sets: [] };
    return {
      ...profileWithoutComposition,
      action_policy_ids: profile.action_policy_ids ?? [],
      capabilities: profile.capabilities ?? [],
      rule_set_ids: Array.from(new Set((profile.rule_set_ids?.length ? profile.rule_set_ids : collectLifecycleRuleSetIds(profile.lifecycle)).map(String))),
      publication_status: profile.publication_status ?? (profile.origin === "system" ? "template" : "draft"),
      derived_from_profile_id: profile.derived_from_profile_id ?? "",
      lifecycle: {
        ...profile.lifecycle,
        phase_modes: phaseModes,
        initial_entry: {
          ...initialEntry,
          action_id: initialEntry.action_id ?? `position.enter_${side}`,
          add_steps: (initialEntry.add_steps ?? []).map((step: any) => ({ ...step, action_id: step.action_id ?? `position.add_${side}` })),
        },
        reentry: { ...reentry, action_id: reentry.action_id ?? `position.enter_${side}`, enabled: phaseModes.reentry === "automatic" },
        exit: { ...exit, rule_sets: (exit.rule_sets ?? []).map((route: any) => ({ ...route, action_id: route.action_id ?? `position.${route.action === "reduce" ? "reduce" : "exit"}_${side}` })) },
        trading_behavior: {
          eligible_sessions: profile.lifecycle?.trading_behavior?.eligible_sessions ?? ["regular"],
          side: profile.lifecycle?.trading_behavior?.side ?? "long",
        },
      },
    };
  };
  return {
    ...payload,
    market_discovery: {
      ...marketDiscovery,
      data_fields: normalizedDataFields,
      column_catalog: (marketDiscovery.column_catalog ?? []).map((column: any) => ({
        ...column,
        available_intervals: column.available_intervals ?? dataFieldContextByRef.get(column.field_ref)?.available_intervals ?? [],
      })),
      core_scan: {
        ...marketDiscovery.core_scan,
        inclusion_rule_sets: marketDiscovery.core_scan?.inclusion_rule_sets ?? [],
        inclusion_operator: marketDiscovery.core_scan?.inclusion_operator ?? "all",
        ranking_field: marketDiscovery.core_scan?.ranking_field === "liquidity-rank" ? "market.liquidity_rank" : marketDiscovery.core_scan?.ranking_field ?? "market.liquidity_rank",
        ranking_direction: marketDiscovery.core_scan?.ranking_direction
          ?? ((marketDiscovery.core_scan?.ranking_field ?? "market.liquidity_rank") === "market.liquidity_rank" ? "ascending" : "descending"),
        maximum_size: marketDiscovery.core_scan?.maximum_size ?? 250,
        columns: marketDiscovery.core_scan?.columns ?? [],
      },
      rule_sets: (marketDiscovery.rule_sets ?? []).map(normalizeRuleSet),
      watchlists: (marketDiscovery.watchlists ?? []).map((watchlist: WatchlistConfig & { calculations?: string[] }) => {
        const { calculations: _legacyCalculations, ...referenceWatchlist } = watchlist;
        return { ...referenceWatchlist, exclusion_rule_sets: [], ranking_field: watchlist.ranking_field === "liquidity-rank" ? "market.liquidity_rank" : watchlist.ranking_field };
      }),
      signal_streams: (marketDiscovery.signal_streams ?? []).map((stream: Partial<SignalStreamConfig>) => ({
        ...stream,
        signal_stream_id: String(stream.signal_stream_id ?? ""),
        revision: Number(stream.revision ?? 1),
        name: String(stream.name ?? "Signal Stream"),
        description: String(stream.description ?? "Immutable occurrences emitted from configured Rule Sets."),
        enabled: stream.enabled !== false,
        source_scan_id: String(stream.source_scan_id ?? marketDiscovery.core_scan?.scan_id ?? "qmd-core-scan"),
        inclusion_rule_sets: stream.inclusion_rule_sets ?? [],
        inclusion_operator: stream.inclusion_operator ?? "all",
        columns: stream.columns ?? [],
        refresh_interval_ms: Number(stream.refresh_interval_ms ?? marketDiscovery.core_scan?.refresh_interval_ms ?? 1000),
        trigger_policy: "false_to_true",
        rearm_policy: stream.rearm_policy ?? "after_false",
        cooldown_ms: Number(stream.cooldown_ms ?? 0),
        maximum_events: Number(stream.maximum_events ?? 5000),
        watchlist_routes: stream.watchlist_routes ?? [],
      })),
    },
    strategy: {
      ...strategy,
      capability_catalog: [],
      profile_templates: (strategy.profile_templates ?? []).map(normalizeProfile),
      profiles: (strategy.profiles ?? []).map(normalizeProfile),
    },
    trading_actions: payload?.trading_actions ?? { definitions: [], policies: [] },
    sessions: payload?.sessions ? {
      ...payload.sessions,
      profiles: payload.sessions.profiles ?? [],
      execution_routes: (payload.sessions.execution_routes ?? []).map((route: any) => ({
        ...route,
        modes: route.modes ?? (payload.accounts?.bindings ?? []).find((account: any) => account.account_key === route.account_key)?.modes ?? [],
      })),
      strategy_deployments: payload.sessions.strategy_deployments ?? [],
    } : { profiles: [], execution_routes: [], strategy_deployments: [] },
    assignments: {
      deployments: (runPlans.plans ?? runPlans.deployments ?? []).map((runPlan: any) => {
        const legacy = (strategy.profiles ?? []).find((profile: any) => profile.profile_id === runPlan.profile_id)?.composition ?? {};
        const modes: RuntimeMode[] = runPlan.allowed_environments ?? legacy.allowed_environments ?? ["replay"];
        return {
          ...runPlan,
          watchlist_ids: runPlan.watchlist_ids ?? [legacy.watchlist_id ?? "core-candidates"],
          signal_stream_ids: runPlan.signal_stream_ids ?? marketDiscovery.signal_streams?.filter((row: SignalStreamConfig) => row.enabled).slice(0, 1).map((row: SignalStreamConfig) => row.signal_stream_id) ?? [],
          activation: runPlan.activation ?? { event_policy: "new_occurrences", watchlist_policy: "any_selected" },
          enablement: runPlan.enablement ?? { state: runPlan.enabled === false ? "disabled" : "enabled", scope: "persistent", effective_session: "" },
          canvas_profile_id: runPlan.canvas_profile_id ?? "current-canvas",
          data_plan_ids: runPlan.data_plan_ids ?? Object.fromEntries(modes.map((mode) => [mode, mode === "paper" || mode === "live" ? "qmd.scanner.snapshot.v1" : "market.historical_scanner_materialization.v1"])),
          source_revision_policy: runPlan.source_revision_policy ?? "require_complete",
          allowed_environments: modes,
        };
      }),
      universes: runPlans.universes ?? [],
    },
  } as Draft;
}

export function serializeDraft(draft: Draft) {
  const { assignments, ...rest } = draft;
  const profiles = rest.strategy.profiles.map((profile) => {
    const { capabilities: _legacyCapabilities, rule_set_ids: _legacyRuleSetIds, ...referenceOnlyProfile } = profile;
    return referenceOnlyProfile;
  });
  const profileTemplates = rest.strategy.profile_templates.map((profile) => {
    const { capabilities: _legacyCapabilities, rule_set_ids: _legacyRuleSetIds, ...referenceOnlyProfile } = profile;
    return referenceOnlyProfile;
  });
  return { ...rest, strategy: { ...rest.strategy, profile_templates: profileTemplates, profiles }, run_plans: { plans: assignments.deployments, universes: assignments.universes } };
}

export function serializeSessionDraft(draft: Draft) {
  const serialized = serializeDraft(draft);
  const {
    atomic_fields: _atomicFields,
    calculation_catalog: _calculationCatalog,
    classifications: _classifications,
    column_catalog: _columnCatalog,
    data_field_plan: _dataFieldPlan,
    data_fields: _dataFields,
    field_catalog: _fieldCatalog,
    security_universe: _securityUniverse,
    ...editableMarketDiscovery
  } = serialized.market_discovery;
  const {
    capability_catalog: _capabilityCatalog,
    definitions: _definitions,
    input_catalog: _inputCatalog,
    ...editableStrategy
  } = serialized.strategy;
  return { ...serialized, market_discovery: editableMarketDiscovery, strategy: editableStrategy };
}

export function deduplicateRuleSets(ruleSets: RuleSetDefinition[]): RuleSetDefinition[] {
  const byId = new Map<string, RuleSetDefinition>();
  ruleSets.forEach((ruleSet) => {
    const ruleSetId = ruleSet.rule_set_id.trim();
    if (ruleSetId) byId.set(ruleSetId, ruleSet);
  });
  return [...byId.values()];
}

export function collectRuleExpressionIds(expression?: RuleExpression): string[] {
  if (!expression) return [];
  if (expression.kind === "rule_set") return expression.rule_set_id ? [expression.rule_set_id] : [];
  return expression.children.flatMap(collectRuleExpressionIds);
}

export function collectLifecycleRuleSetIds(lifecycle?: StrategyLifecycle): string[] {
  if (!lifecycle) return [];
  const initial = lifecycle.initial_entry;
  const reentry = lifecycle.reentry?.rules;
  const expressions = [
    initial?.opportunity?.expression,
    initial?.confirmation?.expression,
    initial?.blockers?.expression,
    ...(initial?.add_steps ?? []).map((step) => step.rules.expression),
    reentry?.opportunity?.expression,
    reentry?.confirmation?.expression,
    reentry?.blockers?.expression,
    ...(lifecycle.exit?.rule_sets ?? []).map((route) => route.rules.expression),
  ];
  return Array.from(new Set(expressions.flatMap(collectRuleExpressionIds))).sort();
}

export function normalizeStrategyProfileReferences(profile: StrategyProfile): StrategyProfile {
  return { ...profile, rule_set_ids: collectLifecycleRuleSetIds(profile.lifecycle) };
}

export function reconcileStrategyProfiles(baseProfiles: StrategyProfile[], sessionProfiles: StrategyProfile[]): StrategyProfile[] {
  const sessionById = new Map(sessionProfiles.map((profile) => [profile.profile_id, profile]));
  const protectedProfiles = baseProfiles.map((profile) => profile.protected || profile.origin === "system"
    ? profile
    : sessionById.get(profile.profile_id) ?? profile);
  return [
    ...protectedProfiles,
    ...sessionProfiles.filter((profile) => profile.origin === "user" && !baseProfiles.some((base) => base.profile_id === profile.profile_id)),
  ].map(normalizeStrategyProfileReferences);
}

export function reconcileTradingActions(base: TradingActionsConfiguration, session: TradingActionsConfiguration): TradingActionsConfiguration {
  const atomicPolicyIds = new Set(base.policies.filter((policy) => policy.atomic).map((policy) => policy.policy_id));
  return {
    definitions: base.definitions,
    policies: [
      ...base.policies,
      ...(session?.policies ?? []).filter((policy) => policy.origin === "user" && !policy.atomic && !atomicPolicyIds.has(policy.policy_id)),
    ].filter((policy, index, rows) => rows.findIndex((candidate) => candidate.policy_id === policy.policy_id) === index),
  };
}

export function reconcileSignalStreams(baseRows: SignalStreamConfig[], sessionRows: SignalStreamConfig[]): SignalStreamConfig[] {
  const baseIds = new Set(baseRows.map((row) => row.signal_stream_id));
  const sessionById = new Map(sessionRows.map((row) => [row.signal_stream_id, row]));
  return [
    ...baseRows.map((row) => {
      const saved = sessionById.get(row.signal_stream_id);
      if (!saved) return row;
      if (row.origin === "system") {
        return {
          ...row,
          enabled: saved.enabled,
          columns: saved.columns?.length ? saved.columns : row.columns,
          column_intervals: saved.column_intervals ?? row.column_intervals,
          column_aggregations: saved.column_aggregations ?? row.column_aggregations,
        };
      }
      return { ...row, ...saved };
    }),
    ...sessionRows.filter((row) => row.origin === "user" && !baseIds.has(row.signal_stream_id)),
  ];
}

export function reconcileRunPlans(baseRows: StrategyRunPlan[], sessionRows: StrategyRunPlan[]): StrategyRunPlan[] {
  const baseIds = new Set(baseRows.map((row) => row.run_plan_id));
  const sessionById = new Map(sessionRows.map((row) => [row.run_plan_id, row]));
  return [
    ...baseRows.map((row) => {
      const saved = sessionById.get(row.run_plan_id);
      if (!saved) return row;
      return {
        ...row,
        enabled: saved.enabled,
        enablement: saved.enablement,
      };
    }),
    ...sessionRows.filter((row) => !baseIds.has(row.run_plan_id)),
  ];
}

export function reconcileUniverses(baseRows: WatchUniverse[], sessionRows: WatchUniverse[]): WatchUniverse[] {
  const baseIds = new Set(baseRows.map((row) => row.universe_id));
  const sessionById = new Map(sessionRows.map((row) => [row.universe_id, row]));
  return [
    ...baseRows.map((row) => ({ ...row, ...(sessionById.get(row.universe_id) ?? {}) })),
    ...sessionRows.filter((row) => !baseIds.has(row.universe_id)),
  ];
}

export function reconcileMandates(baseRows: Mandate[], sessionRows: Mandate[]): Mandate[] {
  const baseIds = new Set(baseRows.map((row) => row.mandate_id));
  const sessionById = new Map(sessionRows.map((row) => [row.mandate_id, row]));
  return [
    ...baseRows.map((row) => ({ ...row, ...(sessionById.get(row.mandate_id) ?? {}) })),
    ...sessionRows.filter((row) => !baseIds.has(row.mandate_id)),
  ];
}

export function readSessionConfiguration(base: Draft): Draft {
  try {
    const storedDraft = readConfigurationSession<unknown>();
    if (!storedDraft) return base;
    const session = normalizeDraft(storedDraft);
    const protectedIds = new Set(base.market_discovery.rule_sets.filter((row) => row.protected || row.origin === "system").map((row) => row.rule_set_id));
    const storedCustomRuleSets = session.market_discovery.rule_sets.filter((row) => row.origin === "user" && !row.protected && !protectedIds.has(row.rule_set_id));
    const reconciledRuleSets = deduplicateRuleSets([
      ...base.market_discovery.rule_sets,
      ...storedCustomRuleSets,
    ]);
    const sessionWatchlists = new Map(session.market_discovery.watchlists.map((row) => [row.watchlist_id, row]));
    const reconciledWatchlists = [
      ...base.market_discovery.watchlists.map((row) => ({ ...row, ...(sessionWatchlists.get(row.watchlist_id) ?? {}) })),
      ...session.market_discovery.watchlists.filter((row) => row.origin === "user" && !base.market_discovery.watchlists.some((baseRow) => baseRow.watchlist_id === row.watchlist_id)),
    ];
    const reconciled = {
      ...base,
      ...session,
      schema_version: base.schema_version,
      market_discovery: {
        ...base.market_discovery,
        ...session.market_discovery,
        // Catalogs are backend/QMD authority. Preserve user-authored rules,
        // Watchlists, and selections, but never freeze an older catalog in a
        // browser session after the registry gains a field or calculation.
        atomic_fields: base.market_discovery.atomic_fields,
        classifications: base.market_discovery.classifications,
        data_field_plan: base.market_discovery.data_field_plan,
        data_fields: base.market_discovery.data_fields,
        field_catalog: base.market_discovery.field_catalog,
        column_catalog: base.market_discovery.column_catalog,
        calculation_catalog: base.market_discovery.calculation_catalog,
        security_universe: base.market_discovery.security_universe,
        core_scan: {
          ...base.market_discovery.core_scan,
          ...session.market_discovery.core_scan,
          scan_id: session.market_discovery.core_scan.scan_id || base.market_discovery.core_scan.scan_id,
          name: session.market_discovery.core_scan.name || base.market_discovery.core_scan.name,
          description: session.market_discovery.core_scan.description || base.market_discovery.core_scan.description,
          columns: session.market_discovery.core_scan.columns.length ? session.market_discovery.core_scan.columns : base.market_discovery.core_scan.columns,
        },
        rule_sets: reconciledRuleSets,
        watchlists: reconciledWatchlists,
        signal_streams: reconcileSignalStreams(
          base.market_discovery.signal_streams,
          session.market_discovery.signal_streams,
        ),
      },
      strategy: {
        ...session.strategy,
        // Profiles are user draft state. Executor definitions and their input
        // contracts are backend authority and must not be frozen by an older
        // browser session when a new strategy implementation is installed.
        capability_catalog: [],
        definitions: base.strategy.definitions,
        input_catalog: base.strategy.input_catalog,
        profiles: reconcileStrategyProfiles(base.strategy.profiles, session.strategy.profiles),
      },
      trading_actions: reconcileTradingActions(base.trading_actions, session.trading_actions),
      assignments: {
        deployments: reconcileRunPlans(base.assignments.deployments, session.assignments.deployments),
        universes: reconcileUniverses(base.assignments.universes, session.assignments.universes),
      },
      portfolio: {
        ...base.portfolio,
        ...session.portfolio,
        mandates: reconcileMandates(base.portfolio.mandates, session.portfolio.mandates),
      },
    };
    writeSessionConfiguration(reconciled);
    return reconciled;
  } catch {
    clearConfigurationSession();
    return base;
  }
}

export function writeSessionConfiguration(draft: Draft) {
  // Backend/QMD catalogs are restored by readSessionConfiguration and must not
  // consume the browser's limited session storage on every draft edit.
  writeConfigurationSession(serializeSessionDraft(draft));
}
