---
name: codex-session-inspection
description: Inspect what Codex/ChatGPT Desktop sessions are doing now.
---

# Codex / ChatGPT Desktop session inspection

## When to Use

- "What is Codex doing right now?" / "which session is running?" / "is it stuck?"
- "Are these 503 / 401 / browser timeout errors still happening?"
- Any request to observe ChatGPT Desktop activity or report session progress.

For current thread state, first load `codex-conversation-status` and use `codex-status/cli.cjs` inside the deployed Web MCP repository (see its `codex-status/README.md`). This queries the desktop app that owns the sessions. Inspection stays read-only: do not restart the app or kill `codex.exe`. Explicitly requested send/resume/stop operations use that skill's documented action commands instead of database edits. Subagents use the same read/status interface, with related IDs from the parent's read result. Use the databases below only for historical activity or error investigation, always read-only with `mode=ro`.

## Data locations (Windows)

- `%USERPROFILE%\.codex\state_5.sqlite` — `threads` (id, title, cwd, model, archived, tokens_used, rollout_path), `projects`, `thread_spawn_edges`.
- `%USERPROFILE%\.codex\thread_history_1.sqlite` — `thread_items` (created_at_ms, item_type, item_json), `thread_turns` (status, started_at, completed_at, duration_ms, error_json). Multi-GB file: always filter by time, never `SELECT *`.
- `%APPDATA%\Codex Web GPT\logs\launcher.jsonl` — JSON lines, UTC `at` field, `event` + `detail.line`. Contains `[http-stream] tracked response timing`, `model_catalog_failed`, `browser.*` errors.

Use the live entrypoint for current state; database timestamps describe historical activity.

## Interpreting database snapshots

1. `thread_turns.status='inProgress'` is NOT liveness. Rows can retain `inProgress` after a turn ended.
2. `threads.updated_at_ms` is NOT liveness. Background polling may refresh this timestamp independently of turn execution.
3. `MAX(created_at_ms)` per thread indicates recorded activity only. It cannot prove running, idle, stalled, or waiting. Use the desktop live status and activeFlags for those distinctions; if unavailable, say live state is unconfirmed.

Some active threads have no row in `threads` (orphan / deleted / subagent). Fall back to the first `userMessage` in `thread_items` and pull `content[].text` for a title.

## Procedure

1. For historical investigation only, recorded activity set: `SELECT thread_id, MAX(created_at_ms) FROM thread_items WHERE created_at_ms >= now-40min GROUP BY thread_id ORDER BY 2 DESC`. Do not scan the multi-GB history to answer a live-status question.
2. Metadata per id from `threads`, with the `userMessage` fallback.
3. Latest turn per id from `thread_turns` for status / duration / `error_json`.
4. Failure scan: `status IN ('failed','interrupted') AND started_at >= now-25min`. Note `started_at` is seconds, `created_at_ms` is milliseconds.
5. Optional: parse `launcher.jsonl` for `/v1/responses` status+outcome counts over the last hour.

## Recurring watch / "report every N minutes"

Only create or change recurring jobs when explicitly requested. For a short, explicitly requested watch, use bounded live status queries every 30–60 seconds. Do not recreate the removed observer. Historical database snapshots must be labelled historical, not live state or screen observations.

## Interpreting results

- Long `inProgress` with a steady item flow = long-running work, not stuck. Bucket item counts per 5 minutes before calling anything hung.
- `status: 200` + `outcome: "aborted"` = client-side stream cancellation, not an outage. Cancellation alone does not prove a connection failure.
- 401/502 on `/v1/models` may be a `model_catalog_failed` event; check the configured passthrough endpoint and authentication without printing Bearer values. Do not infer turn failure from model-catalog errors alone.
- Before claiming a 503, require the literal `503 Service Unavailable` inside the window. `grep 503` also matches timing values like `maxChunkGapMs":5025` and text the user pasted days earlier — both fabricate outages.
- Report the observation window, and label inferences as inferences.
