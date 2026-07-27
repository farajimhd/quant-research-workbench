# Durable Chat Summary Prompt

Use this prompt when consolidating long Codex chats into durable,
chat-independent repository context. Its purpose is to preserve decisions and
unfinished work without turning `TASK_HISTORY.csv` into a transcript archive or
adding generated runtime artifacts to the repository.

## Prompt

```text
Create a durable, chat-independent narrative history for this repository.

Purpose:
Preserve the reasoning, evolving requirements, decisions, discoveries,
implementation work, validation evidence, and unfinished work from long chats
so a new chat can recover the project context without loading the original
conversation.

Storage design:
1. Keep TASK_HISTORY.csv as the concise canonical task/outcome ledger.
2. Keep TASK_HISTORY.md generated from TASK_HISTORY.csv.
3. Create or update CHAT_SUMMARIES.md as the canonical narrative chat history.
4. Add a "Long Chat Summaries" section at the end of TASK_HISTORY.md that
   contains a concise index linking each entry to CHAT_SUMMARIES.md.
5. Update scripts/render_task_history.py so regenerating TASK_HISTORY.md
   preserves or regenerates this index. Do not manually append content that the
   renderer will later erase.
6. When a task-history row is materially connected to a chat summary, include
   the applicable chat-summary identifier in its progress or result field
   without making the task row verbose.

Chat discovery and access:
1. Inventory all accessible chats/tasks associated with this repository or
   project.
2. Do not claim to have reviewed a chat unless its content was actually
   accessible.
3. If some known chats cannot be accessed, list them under "Unavailable or
   incomplete source chats" with the available identifier, title, and date. Do
   not reconstruct missing content from assumptions.
4. Detect continuations of the same durable task across multiple chats.
   Preserve each chat as a separate entry, but cross-reference the related task
   and earlier/later chat summaries.
5. Do not expose secrets, credentials, raw sensitive data, or unnecessary
   private content.

For every reviewed chat, create one section in CHAT_SUMMARIES.md using this
structure:

## CHAT-YYYYMMDD-NNN — <descriptive title>

- Chat started: <exact date and time with timezone, when available>
- Chat ended or last activity: <exact date and time with timezone, when available>
- Summary written: <current date and time with timezone>
- Chat/task identifier: <identifier when available>
- Repository or scope: <repository, module, service, or research version>
- Related task-history entries: <TASK-NNNN identifiers or "None">
- Source completeness: Complete | Partial | Inaccessible portions

### Narrative

Write a chronological, story-like account from the beginning of the chat to its
conclusion.

The narrative must explain:

- What the user initially wanted and why.
- The relevant starting state.
- How the user's requirements, understanding, or priorities evolved.
- Important questions, alternatives, disagreements, and design checkpoints.
- Evidence that changed the direction of the work.
- Decisions that were accepted, rejected, revised, or superseded.
- What was diagnosed, designed, implemented, moved, removed, or documented.
- Important failures, regressions, blockers, and how they were handled.
- What was validated, including exact tests, builds, runtime checks, data
  checks, or workstation checks when available.
- Commits, branches, pushes, task-history entries, and important artifact
  locations when relevant.
- The final state at the end of the chat.

Weight detail by recency:

- Summarize the early exploratory portion compactly.
- Give progressively more detail as the narrative approaches the end.
- Describe the final decisions, implementation state, validation, and remaining
  dependencies most precisely.
- Do not reproduce the transcript, raw command output, or repetitive status
  messages.

### Durable decisions

List the decisions from this chat that future agents must preserve. Distinguish:

- Confirmed requirements
- Architectural decisions
- Rejected approaches
- Assumptions
- Unresolved uncertainty

### Delivered outcomes

List the concrete outcomes that were genuinely completed. Reference relevant
files, task identifiers, commits, runtime locations, tests, or validation
evidence.

### Unfinished or hanging work

Identify everything discussed but not completed, including:

- Explicit next steps
- Operational actions the user still needs to perform
- Missing validation
- Inaccessible workstation or external-system checks
- Deferred implementation
- Known defects or risks
- Questions that were never resolved

For each unfinished item, state:

- Current state
- Why it remains unfinished
- Exact next action
- Dependency or owner
- Related task-history identifier

### Handoff to the next chat

End with a short operational briefing that tells a new agent:

- What it must understand first
- Which files and task-history entries to read
- Which decisions must not be reversed accidentally
- The most important next action
- Any actions that require user approval or external access

Quality and consistency requirements:
1. Ground every statement in accessible chat content, current repository
   evidence, task history, commits, or runtime evidence.
2. Clearly distinguish confirmed facts, user intent, engineering judgment,
   inference, and unresolved uncertainty.
3. Do not invent timestamps, validation, completion, commit identifiers, or
   outcomes.
4. Reconcile contradictions using the user's latest clarification as
   authoritative while recording that an earlier direction was superseded.
5. Do not mark work completed merely because code was written or committed.
6. Avoid duplicating identical background in every entry; cross-reference
   earlier chat summaries where appropriate.
7. Preserve important reasoning and decisions, but keep the document usable as
   retrieval context rather than a transcript archive.
8. Order summaries chronologically from oldest to newest.
9. Use America/Vancouver for normalized timestamps while preserving a source
   timestamp's original timezone when materially relevant.
10. Review the resulting documents for consistency and verify that
    scripts/render_task_history.py can regenerate TASK_HISTORY.md without
    losing the Long Chat Summaries index.

Before changing files, report:

- Which chats are accessible
- Which chats are unavailable
- The proposed summary count
- Any ambiguity in matching chats to TASK_HISTORY entries

Then implement the documentation, validate the renderer, update the relevant
task-history entry, commit, and push according to AGENTS.md.
```
