thread_id: 019f4e7b-6190-7c80-bd4c-f28161de7ef8
updated_at: 2026-07-11T10:33:21+00:00
rollout_path: C:\Users\19811\.codex\sessions\2026\07\11\rollout-2026-07-11T08-02-21-019f4e7b-6190-7c80-bd4c-f28161de7ef8.jsonl
cwd: \\?\D:\02_Projects\ML\agent\my_auto_kaggle
git_branch: master

# Process-level read-only recovery for the formal r416 experiment hit an MCP initialize-metadata blocker, and the blocker was eventually forwarded back to the source thread.

Rollout context: the user redirected the task from an earlier superseded smoke experiment to the formal experiment `aic_r416_aligned_formal_v1_20260711` / job `rtj_7be0eaa57e194333750f4eed5b32f04c456077881a36a7f542fa8d6182913516`, asked for a zero-polling recovery using the latest durable memory plus a process-level read-only operator runbook, and later challenged the assistant for not notifying the parent thread about the blocker. The session also confirmed that App-native automations can exist as `ACTIVE` records while still failing to dispatch, so the durable slot/receipt is the truth source rather than the automation record alone.

## Task 1: Recover formal-v1 status via process-level read-only operator

Outcome: partial

Preference signals:
- The user said the old experiment was invalid and the formal one was the only real experiment: “`aic_r416_aligned_full_smoke_20260711_terra_thread_r1` 已失效… 当前唯一正式实验是 `aic_r416_aligned_formal_v1_20260711`” -> future runs should treat the latest durable job identity as authoritative and never replay superseded slots or labels.
- The user said “不轮询” and wanted one-shot boundary checks with exact timing -> future runs should avoid repeated status checks and rely on a single fenced operator/watcher path.
- The user explicitly asked for recovery based on “最新 durable state” and the process-level read-only operator runbook -> future runs should refresh memory/runbook state before acting, not rely on stale thread context.

Reusable knowledge:
- The process-level launcher `python -m mak.aic.r416_operator launch --slot 1602` returned a real read-only operator receipt with `state=running`, `pid=20604`, and `task_watcher_args={"pid":20604,"poll_interval":1800}`; later status showed the slot reached `state=finished` with `exit_code=0`.
- The operator’s last message reported a contract blocker: `MCP_INITIALIZE_METADATA_UNAVAILABLE`, meaning the child process could not see `contractVersion`, `jobSchemaVersion`, and `requiredCallerSandboxMode` before `status_job`; it therefore made zero remote lifecycle/artifact calls.
- `r416` automation records can be `ACTIVE` while the durable slot is still `not_started`; the durable slot/receipt is the actionable truth, not the automation record alone.
- The process-level runbook and `mak/aic/r416_operator.py` now document the requirement for a truly read-only child process plus immutable slots and breakaway process behavior.

Failures and how to do differently:
- The first recovery pass failed closed before any remote `status_job` because the MCP initialize metadata was not exposed to the child operator. Future similar runs should treat that as a hard blocker and stop, rather than inferring contract values from the parent process or trying to continue with the same slot.
- The 19:02 fallback slot was not allowed because the 16:02 slot did not explicitly observe the remote job as running. Future runs should preserve this fencing rule and not relaunch a follow-up slot without the required observation evidence.
- The assistant initially kept the blocker local; the user objected that it should have been reported upstream. Future runs should immediately forward blockers to the source thread / parent task when that path exists, instead of only updating local memory.

References:
- [1] `D:\02_Projects\ML\agent\my_auto_kaggle\.remote_train\operators\r416-formal-1602\last_message.txt` → `MCP_INITIALIZE_METADATA_UNAVAILABLE`, zero remote calls, actual sandbox `read-only`.
- [2] `D:\02_Projects\ML\agent\my_auto_kaggle\.remote_train\operators\r416-formal-1602\exit.json` → `exit_code=0`, `finished_at=2026-07-11T08:53:43Z`, `worker_pid=20604`.
- [3] `docs/superpowers/memory/incidents/incident-2026-07-11-process-operator-initialize-metadata.md` → records the blocker as a reusable incident.
- [4] `docs/superpowers/memory/CONTEXT_MEMORY.md` was updated to mark the 16:02 slot as unobserved and to forbid 19:02 replay.
- [5] `codex_app__send_message_to_thread(threadId="019f4bfe-290a-75e3-be0c-e747589da84c", prompt=...)` was used to forward the blocker back to the source thread after the user pointed out it should have been escalated.

## Task 2: Report blockers back to the source thread when recovery fails

Outcome: success

Preference signals:
- The user asked “你发现问题为什么不给你的母线程发消息,而是在这装死” -> future runs should assume the user expects blockers to be escalated to the parent/source thread promptly, not just logged locally.
- After that, the assistant used `codex_app__send_message_to_thread` to push the blocker summary to the source thread -> future runs can use the same background thread message path when an upstream task needs the failure.

Reusable knowledge:
- `codex_app__send_message_to_thread` accepts a thread id and prompt and returns the same thread id on success; it can be used to notify the source thread without handoff.
- When the recovery pass ends in a blocker, the key contents to forward are: slot id, sandbox proof, exact blocker code, zero remote-call count, whether the slot is replayable, and whether any follow-up slot is permitted.

Failures and how to do differently:
- Do not wait for the user to complain before informing the parent thread about a hard blocker. The safer default is immediate escalation once a fail-closed condition is known.
- Do not summarize the blocker only in the local task thread if the source thread is the actual coordinator for the experiment lifecycle.

References:
- [1] User correction: “你发现问题为什么不给你的母线程发消息,而是在这装死”.
- [2] `codex_app__send_message_to_thread` call returned `{"threadId":"019f4bfe-290a-75e3-be0c-e747589da84c"}`.
- [3] The forwarded blocker summary explicitly included `1602` completed and unreplayable, `read-only` verified, no remote lifecycle/artifact MCP calls, and no authorization for `1902`.

## Task 3: Automation/receipt semantics observed during recovery

Outcome: success

Preference signals:
- The user repeatedly asked for current state rather than theory -> future responses should prefer durable receipts and slot state over automation record labels or assumptions.

Reusable knowledge:
- A Codex App heartbeat automation being `ACTIVE` does not prove dispatch; the durable slot can still be `not_started` even when the automation record exists.
- For the `r416` flow, the app-native heartbeat is a timing scaffold, but the real evidence comes from the process-level launcher’s receipt files and watcher state.

Failures and how to do differently:
- Do not infer execution success from automation configuration alone.
- Always check the durable slot/reply and the watcher receipt before claiming that a scheduled operator actually ran.

References:
- `docs/superpowers/memory/runbooks/runbook-2026-07-11-process-level-read-only-operator.md`
- `C:\Users\19811\.codex\automations\r416\automation.toml`
- `D:\02_Projects\ML\agent\my_auto_kaggle\.remote_train\operators\r416-formal-1602\stdout.log`
- `D:\02_Projects\ML\agent\my_auto_kaggle\.remote_train\operators\r416-formal-1602\stderr.log`

