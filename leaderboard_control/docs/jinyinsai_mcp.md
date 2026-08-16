# jinyinsai MCP Deployment

This project ships four JSON-RPC MCP servers under `mak.mcp` and registers them in `.codex/config.toml`.

## Servers

- `task_watcher`: watches a local PID or log file and emits a completion notification. Use this instead of repeatedly polling long tasks in chat.
- `remote_train`: provides the versioned XGY remote-training control plane for preflight, exact-intent launch, reconciliation, durable watchers, safe stop, and verified artifact collection.
- `jinyinsai_submit`: validates jinyinsai submission ZIP files, edits the AICOMP queue, starts the queue runner, reads leaderboard snapshots, and fetches per-submission AICOMP scoring records.
- `codex_automation`: read-only two-phase App automation guard. It prepares a heartbeat intent/hash and official App request, then verifies the App-created record by id/hash; it never writes automation TOML. If no interval is supplied, the prepared heartbeat defaults to `FREQ=MINUTELY;INTERVAL=30`.

## Start Commands

The project config uses:

```powershell
python -m mak.mcp.task_watcher
python -m mak.mcp.remote_train
python -m mak.mcp.jinyinsai_submit
python -m mak.mcp.automation
```

`jinyinsai_submit` locates `D:\02_Projects\ML\jinyinsai` automatically when run from this repository. Override with `JINYINSAI_ROOT` if needed.

## Remote Training

### Caller Contract and Ownership

The canonical guide is [`REMOTE_TRAIN_AGENT_CONTRACT.md`](REMOTE_TRAIN_AGENT_CONTRACT.md), currently `remote-train-agent-contract/v2` with job schema `1` and boot-intent schema `3`. `aic-remote-train-experiment` is a `read-only` caller, not a maintainer. Its first read-only call verifies `contractVersion`, `jobSchemaVersion`, `bootIntentSchemaVersion`, and `requiredCallerSandboxMode`; missing or mismatched metadata stops all side effects, and a capability gap returns `MCP_CHANGE_REQUEST`.

The supported flow is `instance_status -> preflight_job -> start_job(confirm_remote_execution=true, intent=<exact launchIntent>) -> watch_job(poll_interval=1800) -> one status_job/reconcile_job after watcher completion -> collect_job only after succeeded`. When `expected_finish_at` is known, use one exact wake-up; otherwise the watcher fallback remains 1800 seconds. Do not poll in chat.

Unknown states require reconcile-before-retry and never authorize automatic restart. No tool boots implicitly: `boot_instance` requires separate current user authorization, an exact current boot intent, and `confirm_billing=true`. Legacy `start_job(label, command)` is rejected with `PREFLIGHT_REQUIRED`. Direct `xgy_remote_exec` lifecycle calls, manual PID kills, ledger/lock/watcher edits, wrapper recreation, and experiment renaming are forbidden workarounds.

Only a separately authorized main-agent maintenance task may change the control plane, using fake-backend/no-real-remote tests and read-only review. The operator normally changes no files and must not edit MCP/backend code, safety tests, project evidence, or canonical memory.

Required environment:

```powershell
$env:XGY_TOKEN = "..."
$env:XGY_CONSOLE_TOKEN = "..."  # 登录控制台 token；仅用于单实例自动关机 arm/readback
$env:XGY_INSTANCE_ID = "..."
```

`XGY_TOKEN` 与 `XGY_CONSOLE_TOKEN` 是两个独立凭据域，不能互相替代。不要把任一 token 放进 JobSpec、日志、receipt、聊天或已跟踪配置文件；设置或更新环境后需重启 `remote_train` MCP 进程，使其从本机秘密环境重新加载。

## Submission

### Caller Contract and Ownership

The canonical calling guide is [`JINYINSAI_SUBMIT_AGENT_CONTRACT.md`](JINYINSAI_SUBMIT_AGENT_CONTRACT.md), currently `aicomp-submit-agent-contract/v4`. The MCP enforces safety invariants, the contract owns the complete state/error/runbook documentation, and the `aic-submit-watch` prompt is only a thin operator adapter. This avoids three diverging copies of the same protocol.

The first `queue_status` in a task must expose `contractVersion: aicomp-submit-agent-contract/v4`, `queueSchemaVersion: 3`, and `requiredCallerSandboxMode: read-only`. Missing or mismatched values mean the MCP process may be stale: stop mutations and runner starts, restart/reload the server, and verify once. `aic-submit-watch` also declares `sandbox_mode = "read-only"`; if a live parent-session override makes the spawned agent writable or the actual mode cannot be confirmed, it stops with `OPERATOR_SANDBOX_NOT_READ_ONLY` before any side effect. Calling agents must never repair the MCP, runner, queue ledger, locks, claims, configuration, migrations, safety tests, or project evidence files during a submission/score-capture task. They return structured update suggestions and, for control-plane gaps, an `MCP_CHANGE_REQUEST`; only the main agent may perform a separately authorized, no-real-submission maintenance task. The contract contains the full state table, known failure modes, exact responses, forbidden workarounds, and maintainer workflow.

The safe flow is:

1. `python -m mak.aic.provenance <manifest.json>` for the candidate ZIP.
2. `validate_submission_file` on the candidate ZIP.
3. `queue_push_front` or `queue_push_back` with `files` and one-to-one aligned `provenance_manifests`.
4. `queue_status` once and inspect its exact `runnerStartIntent` (action, queue index, candidate SHA-256, logical submission ID, and semantic queue revision).
5. Ensure the dedicated logged-in Chrome profile is available, then echo that intent unchanged into `queue_runner_start`. Use `confirm_real_submit: true` only for `submit_candidate`; use `false` for `capture_score`.
6. `queue_runner_watch` or `task_watcher` for completion.

`queue_runner_start` may submit to AICOMP only in `submit_candidate` mode. It revalidates the bound candidate's provenance, compares the echoed intent under a cross-process control lock, and probes the fixed local CDP endpoint before it creates any launch receipt, lease, attempt, or child runner. `CDP_UNAVAILABLE_BEFORE_RUNNER_START` is therefore a pre-side-effect readiness failure: the exact intent remains unchanged and no runner exists; restore the documented dedicated Chrome profile and obtain one fresh `queue_status` before trying again. `capture_score` starts a mutually exclusive `capture-only` runner, binds the active sidecar by queue index + accepted time + logical submission ID + candidate SHA-256, takes exactly one leaderboard snapshot in its bound publication window, and can never advance to another queue item. Before the snapshot it atomically creates a durable `submissions/score_capture_claims/` marker, so a crash cannot cause another runner to query the same hourly window again. Ordinary queue tools reject duplicate path/content/logical identities; there is no agent-controlled duplicate override. Direct `submit-one`, Node queue mutation commands (`sync`, `skip-score`, and reset planning), the historical backlog/import/reconcile/watchdog/alarm scripts, and the legacy mutating MCP are disabled as controller bypasses. Manual score skipping is performed only by `queue_skip_score_wait` under the MCP control lock, with an exact queue index or logical submission ID and a non-empty reason.

Chrome/CDP must already be logged in for AICOMP:

```powershell
powershell -ExecutionPolicy Bypass -File D:\02_Projects\ML\jinyinsai\tools\aicomp_start_chrome.ps1 -Restart
```

### Score Semantics

AICOMP public leaderboard rows show the team's latest published submission score, even when it is lower than the previous score. They do not preserve the team's historical best and are not a complete per-submission score history. Accept a leaderboard row for the active candidate only when its `teamSubmitTime` matches the accepted submission. If the timestamp predates the active submission, the row is the previous submission's still-visible result and the leaderboard has not refreshed yet. Use work scoring results / per-submission records when an exact submission record is required.

The only canonical public leaderboard page is `https://reg.aicomp.cn/special/phb/detail?id=4832828643476639839&rwId=4829238709759119407&stbh=4829238709759119431`. Team, submission, and `作品打分结果` pages are different sources and are rejected as public-leaderboard evidence. If an original hourly capture claim was consumed before the platform published the accepted submission, v4 may recover exactly once from a strictly later publication window: `queue_status.publicLeaderboardRecoveryIntent` → `aicomp_public_leaderboard_fetch` → `queue_finalize_public_leaderboard_score`. Before networking, this path persists a sidecar-bound consumption receipt plus a separate durable recovery claim; deletion or corruption never reopens the network. It rejects corrupt/later accepted history, binds exact queue/results before/after revisions in the finalize WAL, preserves the original claim, recovery claim, and consumption receipt, and never exposes bare `leaderboard_snapshot` or retries the original window.

AICOMP publishes at most one effective leaderboard score per hour. When `expected_publish_at` or `capture_start_at` is known, wait for that exact window once and attach `queue_runner_watch` / `task_watcher`; do not query repeatedly inside the hour.

If the current candidate needs per-submission score evidence, the correct source is the work scoring result / submission detail record. If the MCP server cannot retrieve that evidence, record it as an MCP feature gap and return the blocking reason. Do not bypass the queue runner by writing ad hoc controller-side Chrome/CDP scripts.

`jinyinsai_submit.aicomp_submission_records_fetch` is the read-only MCP tool for that evidence. It uses the logged-in Chrome/CDP session to open AICOMP `作品打分结果`, captures bounded table/API/detail evidence, writes a raw evidence JSON under `submissions/submission_record_evidence/`, and returns normalized records with record/work id, submit/create time, attachment file/hash/url, status, failure reason, numeric score, score time, competition/team identifiers, and target-match metadata when present. A per-submission score is attributable only when `matched=true` and `scoreAttributionReady=true`; the latter requires one unique identity-matched record to contain both score and score time, with no fuzzy filename match or cross-record field join.

The tool accepts `queue_index`, `file`, `sha256`, `submitted_at`, and `accepted_at`; when omitted it uses the active queue lock. For queue item 164, `aic-submit-watch` should call `aicomp_submission_records_fetch` with `queue_index: 164` and accept a score only when `matched=true` and the matched record source is per-submission. A public leaderboard row, including the previous PE/LoRA `92.0014` row whose submit time predates item 164, is rejected as `PER_SUBMISSION_SOURCE_UNAVAILABLE` because this tool requires a per-submission source.

If the records page is reachable but lacks target-comparable fields such as queue index, file/hash, submit time, accepted time, or score/score time, the MCP returns `PER_SUBMISSION_FIELDS_INSUFFICIENT`. Treat that as a tooling/page capability gap, not as `NO_MATCHING_SUBMISSION_RECORD`.

## DeepSeek Review Only

DeepSeek is no longer part of the remote-training or submission execution path. Keep it as a dedicated code/architecture review agent through `.codex/agents/deepseek-code-review.toml`.

Execution ownership is:

- `aic-remote-train-experiment`: remote training, logs, status, and artifacts.
- `aic-submit-watch`: queue runner, score capture, leaderboard evidence.
- `deepseek-code-review`: patch review, compatibility-layer leftovers, test coverage, and workflow risk review only.

The Python gateway still supports direct DeepSeek API calls for review use via `DEEPSEEK_API_KEY`, but it intentionally does not route `deepseek-*` model names through the Anthropic-compatible Claude Code environment. Do not use DeepSeek to start remote jobs, submit candidates, run queue watchers, or claim scores.
