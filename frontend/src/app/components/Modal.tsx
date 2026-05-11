import type { ReactNode } from "react";
import { X } from "lucide-react";

export function Modal({ children, className, onClose, title }: { children: ReactNode; className?: string; onClose: () => void; title: string }) {
  const panelClassName = className ? `modal-panel ${className}` : "modal-panel";
  return (
    <div className="modal-backdrop" role="presentation">
      <div className={panelClassName} role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-header">
          <h2>{title}</h2>
          <button className="icon-button" onClick={onClose} type="button" aria-label="Close">
            <X size={16} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
