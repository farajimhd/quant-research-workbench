# Frontend instructions

- Run, install, and build the frontend only through `python scripts/run_frontend.py <command>`; use `scripts/start_workspace_services.ps1` and `scripts/stop_workspace_services.ps1` for the managed workspace lifecycle, or Ctrl+C to stop a standalone launcher. Never run npm from `frontend/`; dependencies, caches, and build output must remain in the external runtime workspace.
- For browser UI design, implementation, review, or modification, use the `design-frontend-ui` skill. If unavailable, follow `docs/codex/skills/design-frontend-ui/SKILL.md`.
- Trace the authoritative data source and contract before designing. Account for field meaning, update rate, latency, freshness, lifecycle, failure modes, and the UI's responsibility in the larger workflow.
- Treat the existing interface as evidence, not an immutable authority. Preserve strong patterns and improve weak ones when evidence supports it. When no interface exists, establish routine visual choices from the users, workflow, risk, density, platform, and product objective.
- Treat backend contracts and shared configuration/runtime authorities as canonical; do not duplicate business logic in components.
- Use `frontend/src/app/theme.ts` for registered themes and semantic visual tokens and `frontend/src/app/components/Layout.tsx` for supported UI scales.
- Define colors, surfaces, borders, shadows, chart treatments, semantic states, and decorative treatments through the theme system, not component-local literals.
- Treat scales `0.8`, `0.9`, `1`, `1.1`, and `1.25` as a design dimension independent of viewport size. Layout, overlays, fixed/sticky regions, charts, truncation, and interaction targets must remain usable at every scale without local zoom workarounds.
- Preserve readable behavior across normal and compact viewports, themes, and scales. Keep status, freshness, scope, units, and accessibility explicit. Format data according to the comparisons and decisions it supports.
- A `full review` is diagnosis-first unless implementation is explicitly requested: inventory routes, responsibilities, data sources, shared authorities, states, themes, scales, dimensions, and interactions; return prioritized systemic findings. For `full review and fix`, complete the audit before changing code and rerun coverage.
- Validate with `python scripts/run_frontend.py ui:review` for targeted captures or `ui:review:full` for bounded full-product coverage. Use `-- --matrix exhaustive` for shared theme, scale, layout, or component changes. Inspect captures at representative normal/compact viewports, themes, and minimum/default/maximum scales. A build alone is not visual validation.
- Store screenshots, traces, reports, and other generated review output under the machine-specific runtime root.
