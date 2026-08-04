# Frontend instructions

- Treat backend contracts and shared configuration/runtime authorities as canonical; do not duplicate business logic in components.
- Use `frontend/src/app/theme.ts` for registered themes and semantic visual tokens and `frontend/src/app/components/Layout.tsx` for supported UI scales.
- Preserve readable behavior across normal and compact viewports, themes, and scales. Keep status, freshness, scope, units, and accessibility explicit.
- For UI changes, run the relevant repository launcher and inspect representative browser captures; a build alone is not visual validation.
- Store screenshots, traces, reports, and other generated review output under the machine-specific runtime root.
