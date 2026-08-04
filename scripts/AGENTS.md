# Scripts and terminal instructions

- For scripts that produce CLI, console, Rich, progress, monitoring, or TUI output, use the `design-terminal-ui` skill. If unavailable, follow `docs/codex/skills/design-terminal-ui/SKILL.md`.
- Prefer runnable Python launchers with complete safe defaults over PowerShell-only workflows. Generated commands must include all required arguments and avoid laptop-only paths.
- Trace the script's authoritative inputs, outputs, lifecycle, interruption behavior, and operator responsibility before changing it.
- Show active, queued, completed, skipped, retried, and failed work with accurate units and actionable reasons. Support graceful interruption and restart.
- Write all generated logs, reports, caches, screenshots, and temporary files under the machine-specific runtime root.
