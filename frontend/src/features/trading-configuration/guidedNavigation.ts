import { Boxes, BriefcaseBusiness, GitBranch, LayoutGrid, Network, Send, ShieldCheck } from "lucide-react";

import type { TradingConfigurationSection } from "../../app/routes";
import { readableLabel } from "./components/ConfigurationFields";
import type { Draft } from "./contracts";
import { releaseReadiness, type Revision } from "./release";
import { canvasApprovalSnapshot, percent, stableStringify } from "./utilities";
export type ConfigurationExperience = "guided" | "expert";
export type OmsGuidedStage = "execution" | "protection";
export type GuidedStep = TradingConfigurationSection | OmsGuidedStage | "canvas";

export function navigateGuidedStep(step: GuidedStep, onOmsStageChange: (value: OmsGuidedStage) => void) {
  if (step === "execution" || step === "protection") {
    window.localStorage.setItem("trading-configuration-oms-stage", step);
    onOmsStageChange(step);
  }
  window.location.hash = pageForGuidedStep(step);
}

export function pageForGuidedStep(step: GuidedStep) {
  if (step === "canvas") return "canvas-configuration";
  if (step === "execution" || step === "protection") return "oms-configuration";
  return pageForSection(step as TradingConfigurationSection);
}

export function reviewRows(draft: Draft, approved: Revision | null) {
  const profile = draft.strategy.profiles.find((row) => row.profile_id === draft.strategy.default_profile_id) ?? draft.strategy.profiles[0];
  const deployment = draft.assignments.deployments.find((row) => row.enabled) ?? draft.assignments.deployments[0];
  const mandate = draft.portfolio.mandates.find((row) => row.run_plan_id === deployment?.run_plan_id) ?? draft.portfolio.mandates[0];
  const oms = draft.oms.profiles.find((row) => row.profile_id === deployment?.oms_profile_id) ?? draft.oms.profiles[0];
  const execution = draft.oms.execution_policies.find((row) => row.policy_id === oms?.settings.entry_execution_policy_id) ?? draft.oms.execution_policies[0];
  const protection = draft.oms.protection_profiles.find((row) => row.profile_id === oms?.settings.protection_profile_id) ?? draft.oms.protection_profiles[0];
  const account = draft.accounts.bindings.find((row) => row.account_key === mandate?.account_key) ?? draft.accounts.bindings[0];
  const checks = releaseReadiness(draft);
  const ready = (label: string) => Boolean(checks.find((check) => check.label === label)?.ready);
  const inherited = <K extends keyof Draft>(key: K) => Boolean(approved && stableStringify(draft[key]) === stableStringify(approved.payload[key]));
  const state = (key: keyof Draft, valid: boolean, recommended: boolean): "Inherited" | "Invalid" | "Using recommended" | "Customized" => !valid ? "Invalid" : inherited(key) ? "Inherited" : recommended ? "Using recommended" : "Customized";
  return [
    { icon: GitBranch, label: "Strategy", selection: profile?.name ?? "Missing", state: state("strategy", Boolean(profile), Boolean(profile?.protected)), step: "strategy" as GuidedStep },
    { icon: Boxes, label: "Accounts", selection: account ? `${account.name} · ${account.modes.map(readableLabel).join(", ")}` : "Missing", state: state("accounts", ready("Accounts") && ready("Paper and Live bindings"), false), step: "accounts" as GuidedStep },
    { icon: BriefcaseBusiness, label: "Portfolio", selection: mandate ? `${account?.name ?? mandate.account_key} · ${percent(mandate.maximum_planned_risk_fraction)} risk` : "Missing", state: state("portfolio", ready("Account mandates"), false), step: "portfolio" as GuidedStep },
    { icon: Send, label: "Execution", selection: execution ? readableLabel(execution.name) : "Missing", state: state("oms", Boolean(execution), execution?.origin === "system"), step: "execution" as GuidedStep },
    { icon: ShieldCheck, label: "Protection", selection: protection?.name ?? "Missing", state: state("oms", Boolean(protection?.mandatory_catastrophic_backstop), protection?.origin === "system"), step: "protection" as GuidedStep },
    { icon: Network, label: "Run Plan", selection: deployment?.name ?? "Missing", state: state("assignments", Boolean(deployment && ready("Runtime compilation") && ready("QMD Watchlists") && ready("Run Plan dependencies")), false), step: "assignments" as GuidedStep },
    { icon: LayoutGrid, label: "Canvas", selection: canvasApprovalSnapshot().ready ? `${canvasApprovalSnapshot().containerCount} containers` : "Missing", state: canvasApprovalSnapshot().ready ? "Customized" : "Invalid", step: "canvas" as GuidedStep },
  ];
}

export function pageForSection(section: TradingConfigurationSection) {
  if (section === "strategy") return "strategy-configuration";
  if (section === "assignments") return "assignment-configuration";
  if (section === "portfolio") return "portfolio-configuration";
  if (section === "oms") return "oms-configuration";
  if (section === "accounts") return "account-configuration";
  return "revision-configuration";
}
