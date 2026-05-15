import { formatDuration, formatNumber } from "../format";
import { SemanticBadge, toneForStatus } from "./SemanticBadge";

export type Stage = {
  phase?: string;
  label: string;
  done: number;
  total: number;
  elapsed_sec: number;
  progress: number;
  unit_label?: string;
  active_count?: number;
  active_items?: Array<{
    bar_file_count?: number;
    chunk_index?: number;
    chunk_total?: number;
    detail?: string;
    label?: string;
    phase?: string;
    pending_writes?: number;
    session_date?: string;
    timeframe?: string;
    group?: string;
    output_start?: string;
    output_end?: string;
    warmup_start?: string;
    warmup_end?: string;
    started_at?: string;
  }>;
};

export type SessionCard = {
  session_date: string;
  status: string;
  phase: string;
  done: number;
  total: number;
  elapsed_sec: number;
  progress: number;
  day_stages: Stage[];
  timeframes: Array<{
    timeframe: string;
    done: number;
    total: number;
    elapsed_sec: number;
    progress: number;
    stages: Stage[];
  }>;
};

export function StageRow({ stage }: { stage: Stage }) {
  return (
    <div className="stage-row">
      <span className="stage-label">{stage.label}</span>
      <span className="stage-track">
        <span className="stage-fill" style={{ width: `${Math.max(0, Math.min(100, stage.progress))}%` }} />
      </span>
      <span className="stage-meta">{progressMeta(stage.done, stage.total, stage.elapsed_sec)}</span>
    </div>
  );
}

export function ProgressMeter({
  ariaLabel,
  done,
  elapsed_sec,
  label,
  progress,
  showLabel = true,
  status,
  total
}: {
  ariaLabel?: string;
  done: number;
  elapsed_sec: number;
  label: string;
  progress: number;
  showLabel?: boolean;
  status?: string;
  total: number;
}) {
  const boundedProgress = Math.max(0, Math.min(100, progress || 0));
  const visibleLabel = label.trim();
  return (
    <div className={showLabel ? "progress-meter" : "progress-meter compact"}>
      {showLabel ? (
        <div className={`progress-meter-row ${visibleLabel ? "" : "no-label"}`}>
          {visibleLabel ? <span>{visibleLabel}</span> : null}
          <span>{progressMeta(done, total, elapsed_sec)}</span>
        </div>
      ) : null}
      <span aria-label={ariaLabel ?? visibleLabel} className="progress-meter-track" role="meter" aria-valuemax={100} aria-valuemin={0} aria-valuenow={Math.round(boundedProgress)}>
        <span className={`progress-meter-fill ${progressTone(status, boundedProgress)}`} style={{ width: `${boundedProgress}%` }} />
      </span>
    </div>
  );
}

export function SessionProgressCard({ card }: { card: SessionCard }) {
  return (
    <article className="session-card">
      <header className="session-card-header">
        <div>
          <strong>{card.session_date}</strong>
          <div className="muted">Current: {String(card.phase).replaceAll("_", " ")} | {formatDuration(card.elapsed_sec)}</div>
        </div>
        <div className="session-card-status">
          <span className="session-percent">{Math.round(card.progress)}%</span>
          <SemanticBadge tone={toneForStatus(card.status)}>{String(card.status).replaceAll("_", " ")}</SemanticBadge>
        </div>
      </header>
      <ProgressMeter done={card.done} elapsed_sec={card.elapsed_sec} label="Total progress" progress={card.progress} status={card.status} total={card.total} />
      <div className="day-stage-grid">
        {card.day_stages.map((stage) => (
          <StageRow key={stage.label} stage={stage} />
        ))}
      </div>
      <div className="timeframe-progress-grid">
        {card.timeframes.map((timeframe) => (
          <section className="timeframe-card" key={timeframe.timeframe}>
            <div className="timeframe-card-title">
              <span>{timeframe.timeframe}</span>
              <span>{progressMeta(timeframe.done, timeframe.total, timeframe.elapsed_sec)}</span>
            </div>
            <ProgressMeter
              done={timeframe.done}
              elapsed_sec={timeframe.elapsed_sec}
              label={`${timeframe.timeframe} total progress`}
              progress={timeframe.progress}
              showLabel={false}
              status={card.status}
              total={timeframe.total}
            />
            {timeframe.stages.map((stage) => (
              <StageRow key={`${timeframe.timeframe}-${stage.label}`} stage={stage} />
            ))}
          </section>
        ))}
      </div>
    </article>
  );
}

export function SessionProgressColumn({ title, cards }: { title: string; cards: SessionCard[] }) {
  return (
    <section className="progress-column">
      <h2>{title}</h2>
      <div className="progress-column-body">
        {cards.length ? cards.map((card) => <SessionProgressCard card={card} key={card.session_date} />) : <div className="empty-state">No files yet.</div>}
      </div>
    </section>
  );
}

function progressMeta(done: number, total: number, elapsedSec: number) {
  return `${formatNumber(done, done % 1 ? 1 : 0)}/${formatNumber(total)}, ${formatDuration(elapsedSec)}`;
}

function progressTone(status: string | undefined, progress: number) {
  const normalized = String(status ?? "").toLowerCase();
  if (["failed", "stopped", "canceled", "cancelled", "canceling", "error"].some((item) => normalized.includes(item))) return "stopped";
  if (normalized.includes("complete") || normalized.includes("success") || progress >= 100) return "complete";
  if (normalized.includes("queued") || (!normalized && progress <= 0)) return "queued";
  return "running";
}
