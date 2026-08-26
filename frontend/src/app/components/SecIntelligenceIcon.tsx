import { FileText, LoaderCircle, Sparkles, TriangleAlert } from "lucide-react";

export type SecIconRecency = "hot" | "cold" | "older";

export function SecIntelligenceIcon({ count, failed = false, pending = false, recency = "older", reviewed = false, synthesized = false }: { count: number; failed?: boolean; pending?: boolean; recency?: SecIconRecency; reviewed?: boolean; synthesized?: boolean }) {
  return <span aria-hidden="true" className="sec-intelligence-icon" data-recency={recency} data-synthesized={synthesized}>
    <FileText className="sec-intelligence-document" fill={synthesized ? "currentColor" : "none"} />
    {reviewed ? <Sparkles className="sec-intelligence-review-mark" /> : null}
    {pending ? <LoaderCircle className="sec-intelligence-state-mark" data-state="pending" /> : null}
    {failed ? <TriangleAlert className="sec-intelligence-state-mark" data-state="failed" /> : null}
    {count > 1 ? <b className="sec-intelligence-count">{count > 99 ? "99+" : count}</b> : null}
  </span>;
}
