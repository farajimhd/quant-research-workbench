import type { WorkspaceWindowLayout } from "./components/WorkspaceCanvas";

export const WORKSPACE_GROUP_PREFIX = "workspace-group-";

export type WorkspaceGroup = {
  childIds: string[];
  closed: boolean;
  fullscreen: boolean;
  id: string;
  minimized: boolean;
  title?: string;
  z: number;
};

export type WorkspaceBounds = Pick<WorkspaceWindowLayout, "h" | "w" | "x" | "y">;

export function isWorkspaceGroupId(id: string) {
  return id.startsWith(WORKSPACE_GROUP_PREFIX);
}

export function createWorkspaceGroupId(groups: Record<string, WorkspaceGroup>) {
  let id = `${WORKSPACE_GROUP_PREFIX}${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  while (groups[id]) id = `${WORKSPACE_GROUP_PREFIX}${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
  return id;
}

export function workspaceParentMap(groups: Record<string, WorkspaceGroup>) {
  const parents: Record<string, string> = {};
  for (const group of Object.values(groups)) {
    for (const childId of group.childIds) parents[childId] = group.id;
  }
  return parents;
}

export function workspaceRootNodeIds(openIds: string[], groups: Record<string, WorkspaceGroup>) {
  const parents = workspaceParentMap(groups);
  const roots = [
    ...openIds.filter((id) => !parents[id]),
    ...Object.keys(groups).filter((id) => !parents[id]),
  ];
  return [...new Set(roots)];
}

export function workspaceDescendantContainerIds(nodeId: string, groups: Record<string, WorkspaceGroup>, visited = new Set<string>()): string[] {
  if (!isWorkspaceGroupId(nodeId)) return [nodeId];
  if (visited.has(nodeId)) return [];
  const group = groups[nodeId];
  if (!group) return [];
  const nextVisited = new Set(visited).add(nodeId);
  return group.childIds.flatMap((childId) => workspaceDescendantContainerIds(childId, groups, nextVisited));
}

export function workspaceNodeBounds(
  nodeId: string,
  layouts: Record<string, WorkspaceWindowLayout>,
  groups: Record<string, WorkspaceGroup>,
): WorkspaceBounds | null {
  const memberLayouts = workspaceDescendantContainerIds(nodeId, groups)
    .map((id) => layouts[id])
    .filter((layout): layout is WorkspaceWindowLayout => Boolean(layout));
  if (!memberLayouts.length) return null;
  const x = Math.min(...memberLayouts.map((layout) => layout.x));
  const y = Math.min(...memberLayouts.map((layout) => layout.y));
  const right = Math.max(...memberLayouts.map((layout) => layout.x + layout.w));
  const bottom = Math.max(...memberLayouts.map((layout) => layout.y + layout.h));
  return { h: bottom - y, w: right - x, x, y };
}

export function normalizeWorkspaceGroups(
  value: unknown,
  openIds: string[],
): Record<string, WorkspaceGroup> {
  if (!value || typeof value !== "object") return {};
  const open = new Set(openIds);
  const candidates = value as Record<string, Partial<WorkspaceGroup>>;
  const groups: Record<string, WorkspaceGroup> = {};
  for (const [id, candidate] of Object.entries(candidates)) {
    if (!isWorkspaceGroupId(id) || !candidate || !Array.isArray(candidate.childIds)) continue;
    groups[id] = {
      childIds: [...new Set(candidate.childIds.filter((childId): childId is string => typeof childId === "string" && childId !== id))],
      closed: Boolean(candidate.closed),
      fullscreen: Boolean(candidate.fullscreen),
      id,
      minimized: Boolean(candidate.minimized),
      title: typeof candidate.title === "string" && candidate.title.trim() ? candidate.title.trim() : undefined,
      z: Number.isFinite(Number(candidate.z)) ? Number(candidate.z) : 1,
    };
  }

  // Remove invalid references, cycles, duplicate parentage, and groups that cannot
  // form a compound surface. Children are claimed in stable object order.
  const claimed = new Set<string>();
  const valid: Record<string, WorkspaceGroup> = {};
  const reaches = (nodeId: string, targetId: string, visited = new Set<string>()): boolean => {
    if (nodeId === targetId) return true;
    if (visited.has(nodeId)) return false;
    const group = groups[nodeId];
    if (!group) return false;
    const nextVisited = new Set(visited).add(nodeId);
    return group.childIds.some((childId) => reaches(childId, targetId, nextVisited));
  };
  const resolves = (nodeId: string, ancestry: Set<string>): boolean => {
    if (open.has(nodeId)) return true;
    const group = groups[nodeId];
    if (!group || ancestry.has(nodeId)) return false;
    const next = new Set(ancestry).add(nodeId);
    return group.childIds.some((childId) => resolves(childId, next));
  };
  for (const group of Object.values(groups)) {
    const childIds = group.childIds.filter((childId) => !claimed.has(childId) && !reaches(childId, group.id) && resolves(childId, new Set([group.id])));
    if (childIds.length < 2) continue;
    childIds.forEach((childId) => claimed.add(childId));
    valid[group.id] = { ...group, childIds };
  }
  return pruneWorkspaceGroups(valid, openIds);
}

export function pruneWorkspaceGroups(groups: Record<string, WorkspaceGroup>, openIds: string[]) {
  const open = new Set(openIds);
  let next = Object.fromEntries(Object.entries(groups).map(([id, group]) => [id, { ...group, childIds: [...group.childIds] }])) as Record<string, WorkspaceGroup>;
  let changed = true;
  while (changed) {
    changed = false;
    for (const [id, group] of Object.entries(next)) {
      const childIds = group.childIds.filter((childId) => open.has(childId) || Boolean(next[childId]));
      if (childIds.length !== group.childIds.length) {
        next[id] = { ...group, childIds };
        changed = true;
      }
      if (childIds.length < 2) {
        const replacement = childIds[0];
        delete next[id];
        for (const [parentId, parent] of Object.entries(next)) {
          if (!parent.childIds.includes(id)) continue;
          next[parentId] = {
            ...parent,
            childIds: replacement
              ? parent.childIds.map((childId) => childId === id ? replacement : childId)
              : parent.childIds.filter((childId) => childId !== id),
          };
        }
        changed = true;
      }
    }
  }
  return next;
}

export function createWorkspaceGroup(
  childIds: string[],
  groups: Record<string, WorkspaceGroup>,
  z: number,
) {
  const id = createWorkspaceGroupId(groups);
  return { ...groups, [id]: { childIds: [...new Set(childIds)], closed: false, fullscreen: false, id, minimized: false, z } };
}

export function addWorkspaceNodesToGroup(
  groupId: string,
  childIds: string[],
  groups: Record<string, WorkspaceGroup>,
) {
  const group = groups[groupId];
  if (!group) return groups;
  return {
    ...groups,
    [groupId]: { ...group, childIds: [...new Set([...group.childIds, ...childIds.filter((id) => id !== groupId)])] },
  };
}

export function removeWorkspaceNodeFromGroup(
  groupId: string,
  childId: string,
  groups: Record<string, WorkspaceGroup>,
  openIds: string[],
) {
  const group = groups[groupId];
  if (!group) return groups;
  return pruneWorkspaceGroups({
    ...groups,
    [groupId]: { ...group, childIds: group.childIds.filter((id) => id !== childId) },
  }, openIds);
}

export function ungroupWorkspaceGroup(groupId: string, groups: Record<string, WorkspaceGroup>, openIds: string[]) {
  if (!groups[groupId]) return groups;
  const next = { ...groups };
  delete next[groupId];
  for (const [parentId, parent] of Object.entries(next)) {
    if (!parent.childIds.includes(groupId)) continue;
    next[parentId] = {
      ...parent,
      childIds: parent.childIds.flatMap((childId) => childId === groupId ? groups[groupId].childIds : [childId]),
    };
  }
  return pruneWorkspaceGroups(next, openIds);
}

export function transformWorkspaceNodeLayouts(
  nodeId: string,
  layouts: Record<string, WorkspaceWindowLayout>,
  groups: Record<string, WorkspaceGroup>,
  nextBounds: WorkspaceBounds,
) {
  const currentBounds = workspaceNodeBounds(nodeId, layouts, groups);
  if (!currentBounds || currentBounds.w <= 0 || currentBounds.h <= 0) return layouts;
  const scaleX = nextBounds.w / currentBounds.w;
  const scaleY = nextBounds.h / currentBounds.h;
  const descendants = new Set(workspaceDescendantContainerIds(nodeId, groups));
  return Object.fromEntries(Object.entries(layouts).map(([id, layout]) => {
    if (!descendants.has(id)) return [id, layout];
    return [id, {
      ...layout,
      fullscreen: false,
      h: layout.h * scaleY,
      minimized: false,
      w: layout.w * scaleX,
      x: nextBounds.x + (layout.x - currentBounds.x) * scaleX,
      y: nextBounds.y + (layout.y - currentBounds.y) * scaleY,
    }];
  }));
}
