import {
  BarChart3,
  BriefcaseBusiness,
  Building2,
  FileSearch,
  ListChecks,
  LayoutGrid,
  Newspaper,
  PanelTopOpen,
  RefreshCcw,
  ScanSearch,
  ScrollText,
  Settings2,
  ShoppingCart,
  X,
} from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";

import type { CanvasWorkspaceState } from "../canvasWorkspace";
import {
  addWorkspaceNodesToGroup,
  createWorkspaceGroup,
  isWorkspaceGroupId,
  normalizeWorkspaceGroups,
  pruneWorkspaceGroups,
  removeWorkspaceNodeFromGroup,
  transformWorkspaceNodeLayouts,
  ungroupWorkspaceGroup,
  workspaceDescendantContainerIds,
  workspaceNodeBounds,
  workspaceParentMap,
  workspaceRootNodeIds,
  type WorkspaceGroup,
} from "../workspaceGroups";
import {
  containersForMode,
  defaultContainersForMode,
  sourceBindingForContainer,
  type TradingWorkspaceMode,
  type WorkspaceContainerDefinition,
  type WorkspaceContainerId,
} from "../tradingWorkspace";
import {
  WorkspaceWindow,
  WorkspaceGroupedMember,
  WorkspaceGroupWindow,
  type WorkspaceCanvasTarget,
  type WorkspaceWindowLayout,
  type WorkspaceWindowMeta,
} from "./WorkspaceCanvas";

type TradingWorkspaceProps = {
  allowMultipleInstances?: boolean;
  clockLabel: string;
  canPopOut?: boolean;
  canvasTargets?: WorkspaceCanvasTarget[];
  compact?: boolean;
  defaultOpenIds?: string[];
  defaultStateOverride?: CanvasWorkspaceState | null;
  definitionsOverride?: readonly WorkspaceContainerDefinition[];
  historicalSourceReady: boolean;
  initialStateOverride?: CanvasWorkspaceState | null;
  layoutPreset?: "focus" | "global" | "mode";
  linkColorForContainer?: (definition: WorkspaceContainerDefinition, instanceId: string) => string | undefined;
  linkLabelForContainer?: (definition: WorkspaceContainerDefinition, instanceId: string) => string | undefined;
  metaForContainer?: (definition: WorkspaceContainerDefinition, instanceId: string) => WorkspaceWindowMeta;
  mode: TradingWorkspaceMode;
  onContainerAdded?: (instanceId: string, definition: WorkspaceContainerDefinition) => void;
  onMoveContainerToCanvas?: (id: string, canvasId: string, layout: WorkspaceWindowLayout) => void;
  onMoveGroupToCanvas?: (id: string, canvasId: string, state: CanvasWorkspaceState) => void;
  onPopOutContainer?: (id: string, layout: WorkspaceWindowLayout) => void;
  onPopOutGroup?: (id: string, state: CanvasWorkspaceState) => void;
  onStateChange?: (state: CanvasWorkspaceState) => void;
  renderContainer?: (definition: WorkspaceContainerDefinition, instanceId: string) => ReactNode;
  runLabel: string;
  runStatus: "completed" | "idle" | "running" | "unavailable";
  sourceLabel?: string;
  showHealth?: boolean;
  statusLabel?: string;
  storageKeyOverride?: string;
  titleBarActionsForContainer?: (definition: WorkspaceContainerDefinition, instanceId: string) => ReactNode;
  titleForContainer?: (definition: WorkspaceContainerDefinition, instanceId: string) => string;
  workspaceBadge?: string;
  commandBarVisible?: boolean;
  managementContent?: ReactNode;
  managementOpen?: boolean;
  onManagementClose?: () => void;
};

const DEFAULT_CANVAS_TARGETS: WorkspaceCanvasTarget[] = [{ color: "var(--primary)", id: "main", isCurrent: true, label: "Main" }];
export const TRADING_WORKSPACE_LAYOUT_VERSION = 5;

function groupSelectionAction(selectedNodeIds: string[], groups: Record<string, WorkspaceGroup>) {
  if (selectedNodeIds.length < 2) return "Select one more";
  const selectedGroups = selectedNodeIds.filter((id) => Boolean(groups[id]));
  if (selectedGroups.length === 1) return `Add ${selectedNodeIds.length - 1} to group`;
  if (selectedGroups.length > 1) return `Create parent group (${selectedNodeIds.length})`;
  return `Create group (${selectedNodeIds.length})`;
}

function groupSelectionInstruction(selectedNodeIds: string[], groups: Record<string, WorkspaceGroup>) {
  if (selectedNodeIds.length < 2) return "Select another container or group, then confirm.";
  const selectedGroups = selectedNodeIds.filter((id) => Boolean(groups[id]));
  if (selectedGroups.length === 1) return "The other selections will join the selected group.";
  if (selectedGroups.length > 1) return "The selected groups will become one parent group.";
  return "Ready to merge under one title bar.";
}

export function TradingWorkspace({
  allowMultipleInstances = false,
  clockLabel,
  canPopOut = false,
  canvasTargets = DEFAULT_CANVAS_TARGETS,
  compact = false,
  defaultOpenIds,
  defaultStateOverride,
  definitionsOverride,
  historicalSourceReady,
  initialStateOverride,
  layoutPreset = "mode",
  linkColorForContainer,
  linkLabelForContainer,
  metaForContainer,
  mode,
  onMoveContainerToCanvas,
  onMoveGroupToCanvas,
  onContainerAdded,
  onPopOutContainer,
  onPopOutGroup,
  onStateChange,
  renderContainer,
  runLabel,
  runStatus,
  sourceLabel = "QMD History",
  showHealth = true,
  statusLabel,
  storageKeyOverride,
  titleBarActionsForContainer,
  titleForContainer,
  workspaceBadge,
  commandBarVisible = true,
  managementContent,
  managementOpen = false,
  onManagementClose,
}: TradingWorkspaceProps) {
  const contentHostsRef = useRef(new Map<string, HTMLDivElement>());

  function contentHost(id: string) {
    let host = contentHostsRef.current.get(id);
    if (!host) {
      host = document.createElement("div");
      host.className = "workspace-persistent-content-host";
      host.dataset.workspaceContentHost = id;
      contentHostsRef.current.set(id, host);
    }
    return host;
  }
  const definitions = useMemo(() => [...(definitionsOverride ?? containersForMode(mode))], [definitionsOverride, mode]);
  const definitionById = useMemo(() => new Map(definitions.map((definition) => [definition.id, definition])), [definitions]);
  const storageKey = storageKeyOverride ?? `quant-research-workbench.trading-workspace.${mode}`;
  const initial = useMemo(
    () => initialStateOverride ?? readWorkspaceState(storageKey, mode, definitions, defaultOpenIds, layoutPreset),
    [defaultOpenIds, definitions, initialStateOverride, layoutPreset, mode, storageKey],
  );
  const [openIds, setOpenIds] = useState<string[]>(initial.openIds);
  const [instances, setInstances] = useState<Record<string, WorkspaceContainerId>>(initial.instances);
  const [layouts, setLayouts] = useState<Record<string, WorkspaceWindowLayout>>(initial.layouts);
  const [groups, setGroups] = useState<Record<string, WorkspaceGroup>>(initial.groups ?? {});
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const canvasRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const state = { groups, instances, layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION, layouts, openIds };
    window.localStorage.setItem(storageKey, JSON.stringify(state));
    onStateChange?.(state);
  }, [groups, instances, layouts, onStateChange, openIds, storageKey]);

  useEffect(() => {
    const activeIds = new Set(openIds);
    contentHostsRef.current.forEach((host, id) => {
      if (activeIds.has(id)) return;
      host.remove();
      contentHostsRef.current.delete(id);
    });
  }, [openIds]);

  useEffect(() => {
    const syncStoredState = (event: StorageEvent) => {
      if (event.key !== storageKey || !event.newValue) return;
      const next = parseWorkspaceState(event.newValue, definitions);
      if (!next) return;
      setOpenIds(next.openIds);
      setLayouts(next.layouts);
      setInstances(next.instances);
      setGroups(next.groups);
      setSelectedNodeIds([]);
    };
    window.addEventListener("storage", syncStoredState);
    return () => window.removeEventListener("storage", syncStoredState);
  }, [definitions, storageKey]);

  const allRootNodeIds = useMemo(() => workspaceRootNodeIds(openIds, groups), [groups, openIds]);
  const rootNodeIds = useMemo(() => allRootNodeIds.filter((id) => !groups[id]?.closed), [allRootNodeIds, groups]);
  const visibleContainerIds = useMemo(() => new Set(rootNodeIds.flatMap((id) => workspaceDescendantContainerIds(id, groups))), [groups, rootNodeIds]);

  function highestLayer() {
    return Math.max(0, ...Object.values(layouts).map((layout) => layout.z), ...Object.values(groups).map((group) => group.z));
  }

  function rootForNode(id: string) {
    const parents = workspaceParentMap(groups);
    let root = id;
    const visited = new Set<string>();
    while (parents[root] && !visited.has(root)) {
      visited.add(root);
      root = parents[root];
    }
    return root;
  }

  function focusContainer(id: string) {
    const rootId = rootForNode(id);
    const z = highestLayer() + 1;
    if (isWorkspaceGroupId(rootId) && groups[rootId]) {
      setGroups((current) => ({ ...current, [rootId]: { ...current[rootId], closed: false, minimized: false, z } }));
      return;
    }
    setLayouts((current) => current[rootId] ? ({ ...current, [rootId]: { ...current[rootId], minimized: false, z } }) : current);
  }

  function toggleNodeSelection(id: string) {
    const rootId = rootForNode(id);
    setSelectedNodeIds((current) => current.includes(rootId) ? current.filter((candidate) => candidate !== rootId) : [...current, rootId]);
  }

  function groupSelectedNodes() {
    const selected = selectedNodeIds.filter((id) => rootNodeIds.includes(id));
    if (selected.length < 2) return;
    const selectedGroups = selected.filter((id) => Boolean(groups[id]));
    const nextZ = highestLayer() + 1;
    const descendants = new Set(selected.flatMap((id) => workspaceDescendantContainerIds(id, groups)));
    setLayouts((current) => Object.fromEntries(Object.entries(current).map(([id, layout]) => [id, descendants.has(id) ? { ...layout, fullscreen: false, minimized: false } : layout])));
    if (selectedGroups.length === 1) {
      const groupId = selectedGroups[0];
      const additions = selected.filter((id) => id !== groupId);
      setGroups((current) => ({
        ...addWorkspaceNodesToGroup(groupId, additions, current),
        [groupId]: { ...current[groupId], childIds: [...new Set([...current[groupId].childIds, ...additions])], closed: false, fullscreen: false, minimized: false, z: nextZ },
      }));
      setSelectedNodeIds([groupId]);
      return;
    }
    const nextGroups = createWorkspaceGroup(selected, groups, nextZ);
    const groupId = Object.keys(nextGroups).find((id) => !groups[id])!;
    setGroups(nextGroups);
    setSelectedNodeIds([groupId]);
  }

  useEffect(() => {
    if (!selectedNodeIds.length) return undefined;
    const handleGroupingKeys = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [contenteditable='true']")) return;
      if (event.key === "Escape") {
        event.preventDefault();
        setSelectedNodeIds([]);
        return;
      }
      if (event.key === "Enter" && selectedNodeIds.filter((id) => rootNodeIds.includes(id)).length >= 2) {
        event.preventDefault();
        groupSelectedNodes();
      }
    };
    document.addEventListener("keydown", handleGroupingKeys);
    return () => document.removeEventListener("keydown", handleGroupingKeys);
  }, [groups, layouts, openIds, rootNodeIds, selectedNodeIds]);

  function ungroupNode(groupId: string) {
    setGroups((current) => ungroupWorkspaceGroup(groupId, current, openIds));
    setSelectedNodeIds([]);
  }

  function detachNode(groupId: string, childId: string) {
    setGroups((current) => removeWorkspaceNodeFromGroup(groupId, childId, current, openIds));
    setSelectedNodeIds([]);
  }

  function closeGroup(groupId: string) {
    const rootId = rootForNode(groupId);
    if (!groups[rootId]) return;
    setGroups((current) => ({
      ...current,
      [rootId]: { ...current[rootId], closed: true, fullscreen: false, minimized: false },
    }));
    setSelectedNodeIds((current) => current.filter((id) => id !== rootId));
  }

  function showGroup(groupId: string) {
    const rootId = rootForNode(groupId);
    if (!groups[rootId]) return;
    const z = highestLayer() + 1;
    setGroups((current) => ({
      ...current,
      [rootId]: { ...current[rootId], closed: false, minimized: false, z },
    }));
  }

  function renameGroup(groupId: string, title: string) {
    const nextTitle = title.trim();
    if (!groups[groupId] || !nextTitle) return;
    setGroups((current) => ({ ...current, [groupId]: { ...current[groupId], title: nextTitle } }));
  }

  function closeContainer(id: string) {
    const nextOpenIds = openIds.filter((candidate) => candidate !== id);
    setOpenIds(nextOpenIds);
    setInstances((current) => Object.fromEntries(Object.entries(current).filter(([candidate]) => candidate !== id)) as Record<string, WorkspaceContainerId>);
    setLayouts((current) => Object.fromEntries(Object.entries(current).filter(([candidate]) => candidate !== id)));
    setGroups((current) => pruneWorkspaceGroups(current, nextOpenIds));
    setSelectedNodeIds((current) => current.filter((candidate) => candidate !== id));
  }

  function updateGroupLayout(id: string, patch: Partial<WorkspaceWindowLayout>) {
    const bounds = workspaceNodeBounds(id, layouts, groups);
    const group = groups[id];
    if (!bounds || !group) return;
    if (patch.x !== undefined || patch.y !== undefined || patch.w !== undefined || patch.h !== undefined) {
      setLayouts((current) => transformWorkspaceNodeLayouts(id, current, groups, {
        h: patch.h ?? bounds.h,
        w: patch.w ?? bounds.w,
        x: patch.x ?? bounds.x,
        y: patch.y ?? bounds.y,
      }));
    }
    const statePatch = Object.fromEntries(Object.entries(patch).filter(([key]) => ["fullscreen", "minimized", "z"].includes(key))) as Partial<WorkspaceGroup>;
    if (Object.keys(statePatch).length) setGroups((current) => ({ ...current, [id]: { ...current[id], ...statePatch } }));
  }

  function addContainer(id: WorkspaceContainerId) {
    if (!allowMultipleInstances && openIds.includes(id)) {
      focusContainer(id);
      setLibraryOpen(false);
      onManagementClose?.();
      return;
    }
    const instanceId = allowMultipleInstances ? nextContainerInstanceId(id, Object.keys(instances)) : id;
    const nextIds = [...openIds, instanceId];
    setOpenIds(nextIds);
    setInstances((current) => ({ ...current, [instanceId]: id }));
    setLayouts((current) => layoutPreset === "focus"
      ? createFocusLayouts(nextIds)
      : { ...current, [instanceId]: createAddedLayout(current, openIds.length) });
    onContainerAdded?.(instanceId, definitionById.get(id)!);
    setLibraryOpen(false);
    onManagementClose?.();
  }

  function updateLayout(id: string, patch: Partial<WorkspaceWindowLayout>) {
    setLayouts((current) => ({ ...current, [id]: { ...current[id], ...patch } }));
  }

  function resetLayout() {
    if (defaultStateOverride) {
      setOpenIds([...defaultStateOverride.openIds]);
      setLayouts(cloneLayouts(defaultStateOverride.layouts));
      setInstances({ ...defaultStateOverride.instances });
      setGroups(cloneGroups(defaultStateOverride.groups ?? {}));
      setSelectedNodeIds([]);
      return;
    }
    const nextIds = defaultOpenIds ?? defaultContainersForMode(mode);
    setOpenIds(nextIds);
    const nextInstances = Object.fromEntries(nextIds.map((id) => [id, instanceKind(id, {}, definitionById)])) as Record<string, WorkspaceContainerId>;
    setInstances(nextInstances);
    setLayouts(createLayoutsForPreset(layoutPreset, mode, nextIds, nextInstances));
    setGroups({});
    setSelectedNodeIds([]);
  }

  function resetContainer(id: string) {
    const kind = instanceKind(id, instances, definitionById);
    const defaultLayout = defaultStateOverride?.layouts[id]
      ?? createLayoutsForPreset(layoutPreset, mode, [id], { [id]: kind })[id]
      ?? createAddedLayout({}, 0, layoutPreset === "focus");
    setOpenIds((current) => current.includes(id) ? current : [...current, id]);
    setLayouts((current) => ({ ...current, [id]: { ...defaultLayout, z: Math.max(1, defaultLayout.z) } }));
  }

  function moveContainer(id: string, canvasId: string) {
    const layout = layouts[id];
    if (!layout || !onMoveContainerToCanvas) return;
    onMoveContainerToCanvas(id, canvasId, layout);
    closeContainer(id);
  }

  function workspaceNodeState(nodeId: string): CanvasWorkspaceState {
    const memberIds = workspaceDescendantContainerIds(nodeId, groups);
    const memberSet = new Set(memberIds);
    const groupIds = descendantGroupIds(nodeId, groups);
    const groupSet = new Set(groupIds);
    return {
      groups: Object.fromEntries(Object.entries(groups).filter(([id]) => groupSet.has(id)).map(([id, group]) => [id, { ...group, childIds: [...group.childIds] }])),
      instances: Object.fromEntries(Object.entries(instances).filter(([id]) => memberSet.has(id))) as Record<string, WorkspaceContainerId>,
      layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
      layouts: Object.fromEntries(Object.entries(layouts).filter(([id]) => memberSet.has(id)).map(([id, layout]) => [id, { ...layout, fullscreen: false, minimized: false }])),
      openIds: memberIds,
    };
  }

  function removeWorkspaceNode(nodeId: string) {
    const memberIds = new Set(workspaceDescendantContainerIds(nodeId, groups));
    const groupIds = new Set(descendantGroupIds(nodeId, groups));
    const nextOpenIds = openIds.filter((id) => !memberIds.has(id));
    setOpenIds(nextOpenIds);
    setInstances((current) => Object.fromEntries(Object.entries(current).filter(([id]) => !memberIds.has(id))) as Record<string, WorkspaceContainerId>);
    setLayouts((current) => Object.fromEntries(Object.entries(current).filter(([id]) => !memberIds.has(id))));
    setGroups((current) => pruneWorkspaceGroups(Object.fromEntries(Object.entries(current).filter(([id]) => !groupIds.has(id))), nextOpenIds));
    setSelectedNodeIds([]);
  }

  function moveGroup(groupId: string, canvasId: string) {
    if (!onMoveGroupToCanvas) return;
    onMoveGroupToCanvas(groupId, canvasId, workspaceNodeState(groupId));
    removeWorkspaceNode(groupId);
  }

  function popOutGroup(groupId: string) {
    if (!onPopOutGroup) return;
    onPopOutGroup(groupId, workspaceNodeState(groupId));
    removeWorkspaceNode(groupId);
  }

  const hasFullscreen = rootNodeIds.some((id) => groups[id]?.fullscreen || layouts[id]?.fullscreen);
  const minHeight = workspaceRootMinHeight(rootNodeIds, layouts, groups, compact);

  useEffect(() => {
    if (!hasFullscreen) return;
    canvasRef.current?.scrollTo({ left: 0, top: 0 });
  }, [hasFullscreen]);

  function containerView(id: string) {
    const kind = instanceKind(id, instances, definitionById);
    const definition = definitionById.get(kind);
    if (!definition) return null;
    const meta = metaForContainer?.(definition, id) ?? containerMeta(definition, mode, historicalSourceReady, runStatus);
    const title = titleForContainer?.(definition, id) ?? definition.title;
    return {
      content: renderContainer ? renderContainer(definition, id) : <ContainerStandby definition={definition} meta={meta} mode={mode} />,
      definition,
      icon: containerIcon(kind),
      kind,
      linkColor: linkColorForContainer?.(definition, id),
      linkLabel: linkLabelForContainer?.(definition, id),
      meta,
      title,
      titleBarActions: titleBarActionsForContainer?.(definition, id),
    };
  }

  function groupTitle(groupId: string) {
    const group = groups[groupId];
    if (group?.title) return group.title;
    const descendants = workspaceDescendantContainerIds(groupId, groups);
    const leaderId = [...descendants].sort((a, b) => {
      const left = layouts[a];
      const right = layouts[b];
      if (!left || !right) return 0;
      if (Math.abs(left.y - right.y) >= 1) return left.y - right.y;
      return right.w * right.h - left.w * left.h;
    })[0];
    const leaderTitle = leaderId ? containerView(leaderId)?.title : undefined;
    return `${leaderTitle ?? "Container group"}${descendants.length > 1 ? ` + ${descendants.length - 1}` : ""}`;
  }

  const managedGroups = Object.values(groups).map((group) => {
    const rootId = rootForNode(group.id);
    const parentId = workspaceParentMap(groups)[group.id];
    return {
      closed: Boolean(groups[rootId]?.closed),
      id: group.id,
      isRoot: rootId === group.id,
      memberCount: workspaceDescendantContainerIds(group.id, groups).length,
      parentTitle: parentId ? groupTitle(parentId) : undefined,
      title: groupTitle(group.id),
    };
  });

  function renderRootNode(id: string) {
    if (groups[id]) {
      const group = groups[id];
      const bounds = workspaceNodeBounds(id, layouts, groups);
      if (!bounds) return null;
      const descendants = workspaceDescendantContainerIds(id, groups);
      const layout: WorkspaceWindowLayout = { ...bounds, fullscreen: group.fullscreen, minimized: group.minimized, z: group.z };
      const minWidth = minimumGroupDimension(descendants, layouts, bounds, "w");
      const minHeight = minimumGroupDimension(descendants, layouts, bounds, "h");
      const menuItems = group.childIds.map((childId) => {
        const view = groups[childId] ? null : containerView(childId);
        return {
          actions: view?.titleBarActions,
          id: childId,
          isGroup: Boolean(groups[childId]),
          kind: view?.kind,
          title: groups[childId] ? groupTitle(childId) : view?.title ?? childId,
        };
      });
      return <WorkspaceGroupWindow
        canPopOut={canPopOut && Boolean(onPopOutGroup)}
        canvasTargets={canvasTargets}
        compact={compact}
        fullscreenRightInset={managementOpen ? "min(360px, 92%)" : 0}
        id={id}
        key={id}
        layout={layout}
        memberCount={descendants.length}
        menuItems={menuItems}
        minHeight={minHeight}
        minWidth={minWidth}
        onClose={closeGroup}
        onCloseMember={closeContainer}
        onDetachMember={(childId) => detachNode(id, childId)}
        onFocus={focusContainer}
        onLayoutChange={updateGroupLayout}
        onMoveToCanvas={moveGroup}
        onPopOut={popOutGroup}
        onSelectionToggle={toggleNodeSelection}
        onUngroup={ungroupNode}
        onUngroupMember={ungroupNode}
        selected={selectedNodeIds.includes(id)}
        title={groupTitle(id)}
      >
        {descendants.map((memberId) => {
          const view = containerView(memberId);
          const memberBounds = layouts[memberId];
          if (!view || !memberBounds) return null;
          return <WorkspaceGroupedMember bounds={memberBounds} groupBounds={bounds} id={memberId} key={memberId} kind={view.kind} onFocus={() => focusContainer(id)} title={view.title}><WorkspaceContentSlot host={contentHost(memberId)} /></WorkspaceGroupedMember>;
        })}
      </WorkspaceGroupWindow>;
    }

    const view = containerView(id);
    const layout = layouts[id];
    if (!view || !layout) return null;
    return <WorkspaceWindow
      canPopOut={canPopOut}
      canvasTargets={canvasTargets}
      compact={compact}
      icon={view.icon}
      id={id}
      key={id}
      kind={view.kind}
      layout={layout}
      linkColor={view.linkColor}
      titleBarActions={view.titleBarActions}
      linkLabel={view.linkLabel}
      meta={view.meta}
      fullscreenRightInset={managementOpen ? "min(360px, 92%)" : 0}
      onClose={closeContainer}
      onFocus={focusContainer}
      onLayoutChange={updateLayout}
      onMoveToCanvas={(windowId, targetCanvasId) => moveContainer(windowId, targetCanvasId)}
      onPopOut={() => onPopOutContainer?.(id, layout)}
      onReset={() => resetContainer(id)}
      onSelectionToggle={toggleNodeSelection}
      selected={selectedNodeIds.includes(id)}
      title={view.title}
    ><WorkspaceContentSlot host={contentHost(id)} /></WorkspaceWindow>;
  }

  return (
    <div className="trading-workspace-shell" data-command-bar-visible={commandBarVisible ? "true" : "false"} data-library-open={libraryOpen ? "true" : "false"} data-management-open={managementOpen ? "true" : "false"} data-workspace-mode={mode}>
      {openIds.filter((id) => visibleContainerIds.has(id)).map((id) => {
        const view = containerView(id);
        return view ? createPortal(view.content, contentHost(id), id) : null;
      })}
      {commandBarVisible ? <section className="trading-workspace-command" aria-label="Workspace context and controls">
        <div className="trading-workspace-identity">
          <span className="trading-mode-badge" data-mode={mode}>{workspaceBadge ?? modeLabel(mode)}</span>
          <div>
            <strong>{runLabel}</strong>
            <small>{clockLabel}</small>
          </div>
        </div>
        {showHealth ? <div className="trading-workspace-health">
          <span className="workspace-health-item" data-status={historicalSourceReady ? "ready" : "error"}>
            <i aria-hidden="true" /> {sourceLabel} {historicalSourceReady ? "ready" : "offline"}
          </span>
          <span className="workspace-health-item" data-status={runStatus === "running" ? "ready" : "idle"}>
            <i aria-hidden="true" /> {statusLabel ?? (runStatus === "running" ? "Run active" : "No active run")}
          </span>
        </div> : null}
        <div className="trading-workspace-actions">
          <button className="button secondary compact" onClick={() => setLibraryOpen((value) => !value)} type="button">
            {libraryOpen ? <X size={14} /> : <PanelTopOpen size={14} />} Containers
          </button>
          <button className="button secondary compact" onClick={resetLayout} type="button">
            <RefreshCcw size={14} /> Reset layout
          </button>
        </div>
      </section> : null}

      {libraryOpen ? <>
        <button aria-label="Close container library" className="workspace-container-library-scrim" onClick={() => setLibraryOpen(false)} type="button" />
        <WorkspaceContainerLibrary allowMultipleInstances={allowMultipleInstances} definitions={definitions} instances={instances} mode={mode} openIds={openIds} onAdd={addContainer} />
      </> : null}

      {managementOpen ? <>
        <button aria-label="Close canvas management" className="workspace-management-scrim" onClick={onManagementClose} type="button" />
        <aside aria-label="Canvas management" className="workspace-management-sidebar">
          <header><strong>Canvas management</strong><button aria-label="Close canvas management" className="toolbar-button compact" onClick={onManagementClose} type="button"><X size={13} /></button></header>
          {managementContent}
          <WorkspaceGroupManager groups={managedGroups} onClose={closeGroup} onRename={renameGroup} onShow={showGroup} />
          <WorkspaceContainerLibrary allowMultipleInstances={allowMultipleInstances} definitions={definitions} instances={instances} mode={mode} openIds={openIds} onAdd={addContainer} />
          <button className="button secondary compact workspace-management-reset" onClick={resetLayout} type="button"><RefreshCcw size={13} /> Reset layout</button>
        </aside>
      </> : null}

      <section
        className="trading-workspace-canvas live-workspace"
        data-has-fullscreen={hasFullscreen ? "true" : "false"}
        data-workspace-canvas
        ref={canvasRef}
      >
        <div className="trading-workspace-plane" style={{ minHeight: hasFullscreen ? "100%" : minHeight }}>
          {selectedNodeIds.length ? <div aria-label="Container group selection" aria-live="polite" className="workspace-group-selection-bar" role="region">
            <div className="workspace-group-selection-copy">
              <span><LayoutGrid size={14} /><strong>Grouping</strong><em>{selectedNodeIds.length} selected</em></span>
              <small>{groupSelectionInstruction(selectedNodeIds, groups)}</small>
            </div>
            <button className="button primary compact workspace-group-confirm" disabled={selectedNodeIds.length < 2} onClick={groupSelectedNodes} type="button">{groupSelectionAction(selectedNodeIds, groups)}</button>
            <span className="workspace-group-selection-keys" aria-hidden="true">Enter to group · Esc to cancel</span>
            <button aria-label="Clear group selection" className="toolbar-button compact" onClick={() => setSelectedNodeIds([])} title="Cancel grouping" type="button"><X size={12} /></button>
          </div> : null}
          <div className="trading-workspace-watermark" aria-hidden="true">
            <span>{workspaceBadge ?? modeLabel(mode)}</span>
            <small>container workspace</small>
          </div>
          {rootNodeIds.map(renderRootNode)}
        </div>
      </section>
    </div>
  );
}

function WorkspaceContentSlot({ host }: { host: HTMLDivElement }) {
  const slotRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    const slot = slotRef.current;
    if (!slot) return undefined;
    slot.appendChild(host);
    return () => {
      if (host.parentElement === slot) host.remove();
    };
  }, [host]);

  return <div className="workspace-content-slot" ref={slotRef} />;
}

type ManagedWorkspaceGroup = {
  closed: boolean;
  id: string;
  isRoot: boolean;
  memberCount: number;
  parentTitle?: string;
  title: string;
};

function WorkspaceGroupManager({
  groups,
  onClose,
  onRename,
  onShow,
}: {
  groups: ManagedWorkspaceGroup[];
  onClose: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onShow: (id: string) => void;
}) {
  return <section aria-label="Workspace groups" className="workspace-group-manager">
    <header><strong>Groups</strong><small>{groups.length} saved</small></header>
    {groups.length ? <div className="workspace-group-manager-list">{groups.map((group) => (
      <WorkspaceGroupManagerRow group={group} key={group.id} onClose={onClose} onRename={onRename} onShow={onShow} />
    ))}</div> : <p>No saved groups. Select containers on the Canvas to create one.</p>}
  </section>;
}

function WorkspaceGroupManagerRow({
  group,
  onClose,
  onRename,
  onShow,
}: {
  group: ManagedWorkspaceGroup;
  onClose: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onShow: (id: string) => void;
}) {
  const [draft, setDraft] = useState(group.title);
  useEffect(() => setDraft(group.title), [group.title]);
  const canSave = Boolean(draft.trim()) && draft.trim() !== group.title;
  return <article className="workspace-group-manager-row" data-closed={group.closed ? "true" : "false"} data-root={group.isRoot ? "true" : "false"}>
    <form onSubmit={(event) => { event.preventDefault(); if (canSave) onRename(group.id, draft); }}>
      <label><span>Group name</span><input aria-label={`Rename ${group.title}`} maxLength={64} onChange={(event) => setDraft(event.target.value)} value={draft} /></label>
      <button className="button secondary compact" disabled={!canSave} type="submit">Save</button>
    </form>
    <div className="workspace-group-manager-meta">
      <span>{group.memberCount} container{group.memberCount === 1 ? "" : "s"}</span>
      <small>{group.isRoot ? (group.closed ? "Closed" : "Open on Canvas") : `Nested in ${group.parentTitle ?? "group"}`}</small>
      {group.isRoot && !group.closed
        ? <button aria-label={`Close ${group.title} from Manage`} className="toolbar-button compact" onClick={() => onClose(group.id)} title="Close group" type="button"><X size={12} /></button>
        : <button aria-label={`Show ${group.title} on Canvas`} className="button secondary compact" onClick={() => onShow(group.id)} type="button">Show</button>}
    </div>
  </article>;
}

function WorkspaceContainerLibrary({
  allowMultipleInstances,
  definitions,
  instances,
  mode,
  onAdd,
  openIds,
}: {
  allowMultipleInstances: boolean;
  definitions: WorkspaceContainerDefinition[];
  instances: Record<string, WorkspaceContainerId>;
  mode: TradingWorkspaceMode;
  onAdd: (id: WorkspaceContainerId) => void;
  openIds: string[];
}) {
  return (
    <section className="workspace-container-library" aria-label="Container library">
      <header>
        <strong>Containers</strong>
        <small>{definitions.length} available</small>
      </header>
      <div className="workspace-container-library-grid">
        {definitions.map((definition) => {
          const binding = sourceBindingForContainer(definition, mode);
          const openCount = openIds.filter((id) => instanceKind(id, instances) === definition.id).length;
          return (
            <article key={definition.id}>
              <div className="workspace-library-icon">{containerIcon(definition.id)}</div>
              <div className="workspace-library-copy">
                <strong>{definition.title}</strong>
                <small>{binding.summary}{openCount ? ` · ${openCount} open` : ""}</small>
              </div>
              <button className="button secondary compact" onClick={() => onAdd(definition.id)} type="button">
                {!allowMultipleInstances && openCount ? "Focus" : "Add"}
              </button>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ContainerStandby({ definition, meta, mode }: { definition: WorkspaceContainerDefinition; meta: WorkspaceWindowMeta; mode: TradingWorkspaceMode }) {
  const binding = sourceBindingForContainer(definition, mode);
  return (
    <div className="workspace-container-standby">
      <div className="workspace-container-standby-icon">{containerIcon(definition.id)}</div>
      <strong>{definition.title} is ready for a run</strong>
      <p>{definition.description}</p>
      <div className="workspace-source-stack">
        {binding.layers.map((layer, index) => (
          <div key={layer.id}>
            <span>{index === 0 ? "Primary" : "Supporting"}</span>
            <strong>{layer.label}</strong>
            <small>{layer.updateModel.replace("-", " ")} · {layer.timeBasis.replaceAll("-", " ")}</small>
          </div>
        ))}
      </div>
      <small className="workspace-standby-status">{meta.status === "error" ? "The required source is unavailable." : "Content begins at the run clock and remains stable while the container refreshes."}</small>
    </div>
  );
}

function containerMeta(
  definition: WorkspaceContainerDefinition,
  mode: TradingWorkspaceMode,
  historicalSourceReady: boolean,
  runStatus: TradingWorkspaceProps["runStatus"],
): WorkspaceWindowMeta {
  const binding = sourceBindingForContainer(definition, mode);
  const dependsOnHistory = binding.layers.some((layer) => layer.id === "qmd-history");
  const status = dependsOnHistory && !historicalSourceReady ? "error" : runStatus === "running" ? "ready" : "idle";
  return {
    detail: binding.layers.map((layer) => `${layer.label}: ${layer.description}`).join("\n"),
    freshness: runStatus === "running" ? "at run clock" : "waiting for run",
    sourceLabel: binding.layers.map((layer) => layer.label).join(" + "),
    status,
  };
}

function readWorkspaceState(
  storageKey: string,
  mode: TradingWorkspaceMode,
  definitions: WorkspaceContainerDefinition[],
  defaultOpenIds: string[] | undefined,
  layoutPreset: "focus" | "global" | "mode",
): CanvasWorkspaceState {
  const defaultIds = defaultOpenIds ?? defaultContainersForMode(mode);
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) throw new Error("no saved layout");
    const parsed = parseWorkspaceState(raw, definitions);
    if (!parsed) throw new Error("stale layout");
    return parsed;
  } catch {
    const instances = Object.fromEntries(defaultIds.map((id) => [id, instanceKind(id, {}, new Map(definitions.map((definition) => [definition.id, definition])))])) as Record<string, WorkspaceContainerId>;
    return { groups: {}, instances, layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION, layouts: createLayoutsForPreset(layoutPreset, mode, defaultIds, instances), openIds: defaultIds };
  }
}

function parseWorkspaceState(raw: string, definitions: WorkspaceContainerDefinition[]): CanvasWorkspaceState | null {
  try {
    const parsed = JSON.parse(raw) as Partial<CanvasWorkspaceState>;
    if (![3, 4, TRADING_WORKSPACE_LAYOUT_VERSION].includes(Number(parsed.layoutVersion)) || !parsed.layouts || !Array.isArray(parsed.openIds)) return null;
    const definitionById = new Map(definitions.map((definition) => [definition.id, definition]));
    const parsedInstances = parsed.instances && typeof parsed.instances === "object" ? parsed.instances : {};
    const openIds = parsed.openIds.filter((id) => definitionById.has(instanceKind(id, parsedInstances, definitionById)));
    const instances = Object.fromEntries(openIds.map((id) => [id, instanceKind(id, parsedInstances, definitionById)])) as Record<string, WorkspaceContainerId>;
    const layouts = Object.fromEntries(openIds.flatMap((id) => parsed.layouts?.[id] ? [[id, parsed.layouts[id]]] : []));
    const groups = normalizeWorkspaceGroups(parsed.groups, openIds);
    return { groups, instances, layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION, layouts, openIds };
  } catch {
    return null;
  }
}

function createLayoutsForPreset(preset: "focus" | "global" | "mode", mode: TradingWorkspaceMode, ids: string[], instances: Record<string, WorkspaceContainerId> = {}) {
  if (preset === "focus") return createFocusLayouts(ids);
  return preset === "global" ? createGlobalLayouts(ids, instances) : createHistoricalLayouts(mode, ids, instances);
}

function createGlobalLayouts(ids: string[], instances: Record<string, WorkspaceContainerId> = {}): Record<string, WorkspaceWindowLayout> {
  const width = availableWorkspaceWidth();
  const margin = 0;
  const gap = 2;
  const columnWidth = Math.floor((width - margin * 2 - gap) / 2);
  const placements: Record<WorkspaceContainerId, Omit<WorkspaceWindowLayout, "fullscreen" | "minimized" | "z">> = {
    scanner: { h: 250, w: columnWidth, x: margin, y: 0 },
    chart: { h: 410, w: columnWidth, x: margin + columnWidth + gap, y: 0 },
    portfolio: { h: 230, w: columnWidth, x: margin, y: 252 },
    orders: { h: 230, w: columnWidth, x: margin + columnWidth + gap, y: 412 },
    fills: { h: 220, w: columnWidth, x: margin, y: 484 },
    strategy: { h: 220, w: columnWidth, x: margin + columnWidth + gap, y: 644 },
    news: { h: 290, w: columnWidth, x: margin, y: 706 },
    sec: { h: 290, w: columnWidth, x: margin + columnWidth + gap, y: 866 },
    xbrl: { h: 290, w: columnWidth, x: margin, y: 998 },
    journal: { h: 290, w: columnWidth, x: margin + columnWidth + gap, y: 1158 },
  };
  return Object.fromEntries(ids.map((id, index) => {
    const kind = instanceKind(id, instances);
    const placement = placements[kind] ?? createAddedLayout({}, index);
    return [id, { ...placement, fullscreen: false, minimized: false, z: index + 1 }];
  }));
}

export function createFocusLayouts(ids: string[]): Record<string, WorkspaceWindowLayout> {
  const width = availableWorkspaceWidth(true);
  const height = Math.max(320, availableWorkspaceHeight() - 62);
  if (ids.length === 1) return { [ids[0]]: { fullscreen: true, h: height, minimized: false, w: width, x: 0, y: 0, z: 1 } };
  const gap = 2;
  const columnWidth = Math.floor((width - gap) / 2);
  return Object.fromEntries(ids.map((id, index) => [id, {
    fullscreen: false,
    h: Math.max(280, Math.floor((height - gap) / Math.ceil(ids.length / 2))),
    minimized: false,
    w: columnWidth,
    x: index % 2 === 0 ? 0 : columnWidth + gap,
    y: Math.floor(index / 2) * (Math.max(280, Math.floor((height - gap) / Math.ceil(ids.length / 2))) + gap),
    z: index + 1,
  }]));
}

function createHistoricalLayouts(mode: TradingWorkspaceMode, ids: string[], instances: Record<string, WorkspaceContainerId> = {}): Record<string, WorkspaceWindowLayout> {
  const width = availableWorkspaceWidth();
  const margin = 12;
  const gap = 10;
  const leftWidth = Math.round((width - margin * 2 - gap) * 0.4);
  const rightWidth = width - margin * 2 - gap - leftWidth;
  const rightX = margin + leftWidth + gap;
  const placements: Partial<Record<WorkspaceContainerId, Omit<WorkspaceWindowLayout, "fullscreen" | "minimized" | "z">>> = mode === "backtest"
    ? {
        strategy: { h: 270, w: leftWidth, x: margin, y: 84 },
        portfolio: { h: 260, w: leftWidth, x: margin, y: 364 },
        orders: { h: 270, w: rightWidth, x: rightX, y: 84 },
        fills: { h: 260, w: rightWidth, x: rightX, y: 364 },
        journal: { h: 290, w: width - margin * 2, x: margin, y: 634 },
      }
    : {
        scanner: { h: 330, w: leftWidth, x: margin, y: 84 },
        portfolio: { h: 230, w: leftWidth, x: margin, y: 424 },
        chart: { h: 570, w: rightWidth, x: rightX, y: 84 },
        news: { h: 280, w: leftWidth, x: margin, y: 664 },
        orders: { h: 280, w: rightWidth, x: rightX, y: 664 },
      };
  return Object.fromEntries(ids.map((id, index) => {
    const placement = placements[instanceKind(id, instances)] ?? createAddedLayout({}, index);
    return [id, { ...placement, fullscreen: false, minimized: false, z: index + 1 }];
  }));
}

function nextContainerInstanceId(kind: WorkspaceContainerId, openIds: string[]) {
  if (!openIds.includes(kind)) return kind;
  let counter = 2;
  while (openIds.includes(`${kind}-${counter}`)) counter += 1;
  return `${kind}-${counter}`;
}

function instanceKind(
  instanceId: string,
  instances: Record<string, WorkspaceContainerId>,
  definitions?: Map<WorkspaceContainerId, WorkspaceContainerDefinition>,
): WorkspaceContainerId {
  const explicit = instances[instanceId];
  if (explicit) return explicit;
  if (definitions?.has(instanceId as WorkspaceContainerId)) return instanceId as WorkspaceContainerId;
  return instanceId.replace(/-\d+$/, "") as WorkspaceContainerId;
}

function createAddedLayout(layouts: Record<string, WorkspaceWindowLayout>, index: number, focus = false): WorkspaceWindowLayout {
  const highest = Math.max(0, ...Object.values(layouts).map((layout) => layout.z));
  const offset = (index % 5) * 18;
  const width = availableWorkspaceWidth(focus);
  return focus
    ? { fullscreen: true, h: Math.max(320, availableWorkspaceHeight() - 62), minimized: false, w: width, x: 0, y: 0, z: highest + 1 }
    : { fullscreen: false, h: 320, minimized: false, w: Math.min(560, width - 36), x: 18 + offset, y: 18 + offset, z: highest + 1 };
}

function availableWorkspaceWidth(noSidebar = false) {
  if (typeof window === "undefined") return 1180;
  const storedScale = Number(window.localStorage.getItem("quant-research-workbench.ui-scale"));
  const scale = Number.isFinite(storedScale) && storedScale > 0 ? storedScale : 1;
  const scaledViewportWidth = window.innerWidth / scale;
  const shellWidth = noSidebar ? 0 : 256;
  const contentPadding = noSidebar ? 0 : 48;
  return Math.max(680, Math.floor(scaledViewportWidth - shellWidth - contentPadding));
}

function availableWorkspaceHeight() {
  if (typeof window === "undefined") return 900;
  const storedScale = Number(window.localStorage.getItem("quant-research-workbench.ui-scale"));
  const scale = Number.isFinite(storedScale) && storedScale > 0 ? storedScale : 1;
  return Math.floor(window.innerHeight / scale);
}

function cloneLayouts(layouts: Record<string, WorkspaceWindowLayout>) {
  return Object.fromEntries(Object.entries(layouts).map(([id, layout]) => [id, { ...layout }]));
}

function cloneGroups(groups: Record<string, WorkspaceGroup>) {
  return Object.fromEntries(Object.entries(groups).map(([id, group]) => [id, { ...group, childIds: [...group.childIds] }]));
}

function descendantGroupIds(nodeId: string, groups: Record<string, WorkspaceGroup>, visited = new Set<string>()): string[] {
  if (!groups[nodeId] || visited.has(nodeId)) return [];
  const nextVisited = new Set(visited).add(nodeId);
  return [nodeId, ...groups[nodeId].childIds.flatMap((childId) => descendantGroupIds(childId, groups, nextVisited))];
}

function minimumGroupDimension(
  descendantIds: string[],
  layouts: Record<string, WorkspaceWindowLayout>,
  bounds: { h: number; w: number },
  axis: "h" | "w",
) {
  const minimumMemberSize = axis === "w" ? 220 : 140;
  const total = bounds[axis];
  return Math.max(axis === "w" ? 320 : 240, ...descendantIds.map((id) => {
    const size = layouts[id]?.[axis] ?? total;
    const ratio = total > 0 ? size / total : 1;
    return ratio > 0 ? minimumMemberSize / ratio : minimumMemberSize;
  }));
}

function workspaceRootMinHeight(
  rootNodeIds: string[],
  layouts: Record<string, WorkspaceWindowLayout>,
  groups: Record<string, WorkspaceGroup>,
  compact: boolean,
) {
  const viewportHeight = typeof window === "undefined" ? 1024 : window.innerHeight;
  const baseHeight = Math.max(viewportHeight, compact ? 960 : 900);
  return rootNodeIds.reduce((height, id) => {
    const bounds = workspaceNodeBounds(id, layouts, groups);
    if (!bounds) return height;
    const group = groups[id];
    const fullscreen = group?.fullscreen ?? layouts[id]?.fullscreen;
    if (fullscreen) return height;
    const minimized = group?.minimized ?? layouts[id]?.minimized;
    const nodeHeight = minimized ? (compact ? 24 : 44) : bounds.h;
    return Math.max(height, bounds.y + nodeHeight + (compact ? 2 : 24));
  }, baseHeight);
}

function containerIcon(id: WorkspaceContainerId) {
  const icons = {
    chart: BarChart3,
    fills: ListChecks,
    journal: ScrollText,
    news: Newspaper,
    orders: ShoppingCart,
    portfolio: BriefcaseBusiness,
    scanner: ScanSearch,
    sec: FileSearch,
    strategy: Settings2,
    xbrl: Building2,
  } satisfies Record<WorkspaceContainerId, typeof BarChart3>;
  const Icon = icons[id];
  return <Icon aria-hidden="true" size={14} />;
}

function modeLabel(mode: TradingWorkspaceMode) {
  if (mode === "backtest_debug") return "Backtest debug";
  return mode.charAt(0).toUpperCase() + mode.slice(1);
}
