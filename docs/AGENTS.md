# Documentation and continuity instructions

- Keep durable design decisions, commands, schemas, operational assumptions, and remaining dependencies near the affected area.
- `TASK_HISTORY.csv` is the editable task ledger; `TASK_HISTORY.md` is generated and must be refreshed with `python scripts/render_task_history.py`.
- `CHAT_SUMMARIES.md` is an index. Detailed summaries belong under `docs/codex/chat-summaries/<YYYY>/` and should remain bounded and outcome-oriented.
- Use `docs/codex/CHAT_SUMMARY_PROMPT.md` for narrative summaries; summaries preserve decisions and handoffs but do not replace the canonical task ledger.
- Keep `docs/codex/REPO_MAP.md` and task profiles concise. Put long rationale in focused reference documents, not global instructions.
