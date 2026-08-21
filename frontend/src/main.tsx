import React, { Component, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/inter/wght.css";
import "@fontsource-variable/public-sans/wght.css";
import "@fontsource-variable/public-sans/wght-italic.css";

import { App } from "./App";
import "./app/styles.css";
import "./app/configurationVisuals.css";

class ApplicationErrorBoundary extends Component<{ children: ReactNode }, { error: string; retryKey: number }> {
  state = { error: "", retryKey: 0 };

  static getDerivedStateFromError(error: unknown) {
    return { error: error instanceof Error ? error.message : String(error) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Application render failure", { componentStack: info.componentStack, error, route: window.location.hash });
  }

  render() {
    if (!this.state.error) return <React.Fragment key={this.state.retryKey}>{this.props.children}</React.Fragment>;
    return <main className="application-error-boundary" role="alert"><div><strong>This page stopped rendering</strong><span>{this.state.error}</span><button className="button primary" onClick={() => this.setState((current) => ({ error: "", retryKey: current.retryKey + 1 }))} type="button">Retry this page</button></div></main>;
  }
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ApplicationErrorBoundary><App /></ApplicationErrorBoundary>
  </React.StrictMode>
);
