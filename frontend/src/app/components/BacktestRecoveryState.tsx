import { TriangleAlert } from "lucide-react";
import { LoadingState } from "./LoadingState";
import "./BacktestRecoveryState.css";

export function BacktestRecoveryState({ error, onRetry, onSetup }: {
  error?: string;
  onRetry: () => void;
  onSetup: () => void;
}) {
  return <main className="backtest-recovery-state">
    <div className="backtest-recovery-content">
      {error ? <div className="backtest-recovery-error" role="alert">
        <TriangleAlert aria-hidden="true" size={20} />
        <h1>Could not open backtest</h1>
        <p>{error}</p>
      </div> : <LoadingState label="Opening backtest" />}
      <div className="backtest-recovery-actions">
        {error ? <button className="button primary compact" onClick={onRetry} type="button">Retry connection</button> : null}
        <button className="button secondary compact" onClick={onSetup} type="button">Return to setup</button>
      </div>
    </div>
  </main>;
}
