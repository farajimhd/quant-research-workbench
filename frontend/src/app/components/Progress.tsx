import { formatDuration, formatNumber } from "../format";
import { SemanticBadge, toneForStatus } from "./SemanticBadge";

export type Stage = {
  label: string;
  done: number;
  total: number;
  elapsed_sec: number;
  progress: number;
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
      <span className="stage-meta">
        {formatNumber(stage.done, stage.done % 1 ? 1 : 0)}/{formatNumber(stage.total)}, {formatDuration(stage.elapsed_sec)}
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
      <div className="file-progress">
        <span style={{ width: `${Math.max(0, Math.min(100, card.progress))}%` }} />
      </div>
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
              <span>
                {formatNumber(timeframe.done, timeframe.done % 1 ? 1 : 0)}/{formatNumber(timeframe.total)}, {formatDuration(timeframe.elapsed_sec)}
              </span>
            </div>
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

