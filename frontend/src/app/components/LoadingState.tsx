type LoadingStateProps = {
  className?: string;
  fill?: boolean;
  label?: string;
};

export function LoadingState({ className = "", fill = false, label = "Loading" }: LoadingStateProps) {
  const classes = ["app-loading-state", fill ? "is-fill" : "", className].filter(Boolean).join(" ");
  return <div aria-live="polite" className={classes} role="status">
    <span className="loading-spinner" aria-hidden="true" />
    <span>{label}</span>
  </div>;
}
