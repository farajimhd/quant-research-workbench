import { useEffect, useState } from "react";
import { SavedLabelResearch } from "./SavedLabelResearch";
import { MarketTeacherPreview } from "./MarketTeacherPreview";
import { V66LabelResearch } from "./V66LabelResearch";
import { BarShardResearch } from "./BarShardResearch";
import { Phase2ShardResearch } from "./Phase2ShardResearch";
import { useResearchState } from "./researchState";
import "./TeacherResearch.css";

export function ResearchWorkspacePage() {
  const requested = new URLSearchParams(window.location.search).get("research");
  const [savedPath, setPath] = useResearchState("path", requested === "phase2-shards" ? "phase2-shards" : requested === "bar-shards" ? "bar-shards" : requested === "v66-labels" ? "v66-labels" : "saved-labels");
  const path = savedPath === "phase2-shards" ? "phase2-shards" : savedPath === "bar-shards" ? "bar-shards" : savedPath === "v66-labels" ? "v66-labels" : savedPath === "market-preview" ? "market-preview" : "saved-labels";
  const [phase2Visited, setPhase2Visited] = useState(path === "phase2-shards");
  const [shardsVisited, setShardsVisited] = useState(path === "bar-shards");
  const [v66Visited, setV66Visited] = useState(path === "v66-labels");
  const [savedVisited, setSavedVisited] = useState(path === "saved-labels");
  const [marketVisited, setMarketVisited] = useState(path === "market-preview");
  useEffect(() => { if (savedPath !== path) setPath(path); }, [savedPath, path, setPath]);
  return <div className="research-path-shell"><nav className="research-path-nav" aria-label="Research paths">
    <button className={`button ${path === "phase2-shards" ? "primary" : "secondary"} compact`} aria-pressed={path === "phase2-shards"} onClick={() => { setPhase2Visited(true); setPath("phase2-shards"); }}>BarGPT phase 2</button>
    <button className={`button ${path === "bar-shards" ? "primary" : "secondary"} compact`} aria-pressed={path === "bar-shards"} onClick={() => { setShardsVisited(true); setPath("bar-shards"); }}>BarGPT shards</button>
    <button className={`button ${path === "saved-labels" ? "primary" : "secondary"} compact`} aria-pressed={path === "saved-labels"} onClick={() => { setSavedVisited(true); setPath("saved-labels"); }}>1a labels</button>
    <button className={`button ${path === "market-preview" ? "primary" : "secondary"} compact`} aria-pressed={path === "market-preview"} onClick={() => { setMarketVisited(true); setPath("market-preview"); }}>1b labels & grouping</button>
    <button className={`button ${path === "v66-labels" ? "primary" : "secondary"} compact`} aria-pressed={path === "v66-labels"} onClick={() => { setV66Visited(true); setPath("v66-labels"); }}>V66 labels · 1a / 1b</button>
  </nav><div className="research-path-content" hidden={path !== "v66-labels"}>{v66Visited && <V66LabelResearch />}</div><div className="research-path-content" hidden={path !== "saved-labels"}>{savedVisited && <SavedLabelResearch />}</div>
    <div className="research-path-content" hidden={path !== "market-preview"}>{marketVisited && <MarketTeacherPreview />}</div>
    <div className="research-path-content" hidden={path !== "bar-shards"}>{shardsVisited && <BarShardResearch />}</div>
    <div className="research-path-content" hidden={path !== "phase2-shards"}>{phase2Visited && <Phase2ShardResearch />}</div>
  </div>;
}
