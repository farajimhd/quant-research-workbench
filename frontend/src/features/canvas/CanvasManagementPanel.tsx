import { Check, ExternalLink, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import { useState } from "react";

import { MAIN_CANVAS_ID, type CanvasRegistry } from "../../app/canvasWorkspace";

export type CanvasManagementPanelProps = {
  availableCanvasIds?: Set<string>;
  currentCanvasId?: string;
  onCreate?: () => void;
  onOpen: (id: string) => void;
  onRemove?: (id: string) => void;
  onRename?: (id: string, label: string) => void;
  registry: CanvasRegistry;
};

export default function CanvasManagementPanel({ availableCanvasIds, currentCanvasId, onCreate, onOpen, onRemove, onRename, registry }: CanvasManagementPanelProps) {
  const configurationMode = Boolean(onCreate && onRemove);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftLabel, setDraftLabel] = useState("");
  const [pendingRemoveId, setPendingRemoveId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const defaultCanvas = registry.canvases.find((canvas) => canvas.id === MAIN_CANVAS_ID) ?? { id: MAIN_CANVAS_ID, label: "Main" };
  const personalCanvases = registry.canvases.filter((canvas) => canvas.id !== MAIN_CANVAS_ID && canvas.label.toLowerCase().includes(search.trim().toLowerCase()));
  const renderCanvas = (canvas: { id: string; label: string }, sharedDefault = false) => {
    const available = configurationMode || availableCanvasIds?.has(canvas.id) || (sharedDefault && Boolean(registry.defaultState));
    const current = canvas.id === currentCanvasId;
    const disabled = configurationMode ? sharedDefault : !available;
    const editing = editingId === canvas.id;
    const confirmingRemove = pendingRemoveId === canvas.id;
    return <article key={canvas.id} data-current={current ? "true" : "false"} data-main={sharedDefault ? "true" : "false"}>
      {editing ? <form className="canvas-manager-rename" onSubmit={(event) => { event.preventDefault(); onRename?.(canvas.id, draftLabel); setEditingId(null); }}><label><span>Canvas name</span><input aria-label={`Rename ${canvas.label}`} autoFocus maxLength={80} onChange={(event) => setDraftLabel(event.target.value)} value={draftLabel} /></label><button aria-label="Save canvas name" className="toolbar-button compact" disabled={!draftLabel.trim()} type="submit"><Check size={13} /></button><button aria-label="Cancel canvas rename" className="toolbar-button compact" onClick={() => setEditingId(null)} type="button"><X size={13} /></button></form> : <>
        <button aria-label={disabled ? `${canvas.label} is unavailable` : `Open ${canvas.label}`} className="canvas-manager-open" disabled={disabled} onClick={() => onOpen(canvas.id)} title={disabled ? (configurationMode ? "Shared draft default" : "No saved layout was captured") : "Open Canvas in a new page"} type="button"><span><strong>{canvas.label}</strong><small>{sharedDefault ? "Shared draft · publication required" : current ? "Current personal canvas" : available ? "Personal canvas" : "Unavailable"}</small></span>{disabled || current ? null : <ExternalLink size={13} />}</button>
        {sharedDefault || !onRename ? null : <button aria-label={`Rename ${canvas.label}`} className="toolbar-button compact" onClick={() => { setDraftLabel(canvas.label); setEditingId(canvas.id); setPendingRemoveId(null); }} title="Rename canvas" type="button"><Pencil size={13} /></button>}
        {sharedDefault || !onRemove ? null : confirmingRemove ? <span className="canvas-manager-remove-confirm"><button className="button danger compact" onClick={() => { onRemove(canvas.id); setPendingRemoveId(null); }} type="button">Remove</button><button aria-label="Cancel remove canvas" className="toolbar-button compact" onClick={() => setPendingRemoveId(null)} type="button"><X size={13} /></button></span> : <button aria-label={`Remove ${canvas.label}`} className="toolbar-button compact" onClick={() => { setPendingRemoveId(canvas.id); setEditingId(null); }} title="Remove canvas" type="button"><Trash2 size={13} /></button>}
      </>}
    </article>;
  };
  return <section aria-label="Canvas manager" className="canvas-manager-strip">
    <header><div><strong>Workspace persistence</strong><small>{configurationMode ? "One shared draft default plus browser-profile personal canvases" : "Published profile and this runtime overlay"}</small></div></header>
    <div className="canvas-manager-default"><span>Shared default</span>{renderCanvas(defaultCanvas, true)}</div>
    <div className="canvas-manager-personal-header"><div><strong>My canvases</strong><small>Persisted only in this browser profile</small></div>{onCreate ? <button aria-label="New personal canvas" className="button secondary compact" onClick={onCreate} type="button"><Plus size={13} /> New</button> : null}</div>
    {registry.canvases.length > 7 ? <label className="canvas-manager-search"><Search aria-hidden="true" size={14} /><input aria-label="Search personal canvases" onChange={(event) => setSearch(event.target.value)} placeholder="Search canvases" type="search" value={search} /></label> : null}
    <div className="canvas-manager-items">{personalCanvases.length ? personalCanvases.map((canvas) => renderCanvas(canvas)) : <p>{search ? "No matching personal canvases." : "No personal canvases yet."}</p>}</div>
  </section>;
}
