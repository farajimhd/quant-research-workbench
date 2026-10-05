import { useEffect, useState } from "react";
import { SavedLabelResearch } from "./SavedLabelResearch";
import { MarketTeacherPreview } from "./MarketTeacherPreview";
import { useResearchState } from "./researchState";
import "./TeacherResearch.css";

export function ResearchWorkspacePage() {
  const [savedPath, setPath] = useResearchState("path", "saved-labels");
  const path = savedPath === "market-preview" ? "market-preview" : "saved-labels";
  const [savedVisited, setSavedVisited] = useState(path === "saved-labels");
  const [marketVisited, setMarketVisited] = useState(path === "market-preview");
  useEffect(() => { if (savedPath !== path) setPath(path); }, [savedPath, path, setPath]);
  return <div className="research-path-shell"><nav className="research-path-nav" aria-label="Research paths">
    <button className={`button ${path === "saved-labels" ? "primary" : "secondary"} compact`} aria-pressed={path === "saved-labels"} onClick={() => { setSavedVisited(true); setPath("saved-labels"); }}>1a labels</button>
    <button className={`button ${path === "market-preview" ? "primary" : "secondary"} compact`} aria-pressed={path === "market-preview"} onClick={() => { setMarketVisited(true); setPath("market-preview"); }}>1b labels & grouping</button>
  </nav><div className="research-path-content" hidden={path !== "saved-labels"}>{savedVisited && <SavedLabelResearch />}</div>
    <div className="research-path-content" hidden={path !== "market-preview"}>{marketVisited && <MarketTeacherPreview />}</div>
  </div>;
}
