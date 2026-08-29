v1

## User Profile
The user works mainly on Windows/PowerShell across `D:\02_Projects\ML\agent`, `D:\02_Projects\ML\agent\my_auto_kaggle`, `D:\02_Projects\ML\jinyinsai`, `D:\02_Projects\ML\stanford`, and nearby tooling repos. They use Codex for practical repo work, runtime/config wiring, MCP/tooling integration, source-backed product/runtime comparisons, and machine-level cleanup when needed.

They prefer live verification over theory: if something should submit, push, call an API, access a page, send to a thread, or prove a storage claim, they usually want the real path exercised or the exact local files checked. They care about execution boundaries, exact thread IDs, durable receipts, and whether a "duplicate" is really a junction/hardlink.

Communication-wise, concise and concrete beats broad background. They correct scope quickly when the assistant drifts from a named local repo/tutorial to generic docs, and they often want plain-language explanations for runtime terms once confusion appears. For summaries or resume-style copy, start shorter than usual and make it paste-ready.

## User preferences
- Prefer a real end-to-end check when the user asks whether something `能不能` work: real API call, real submit path, real reachability test, real push/auth check, or real thread-state readback.
- If the user points to a named local repo or tutorial path, start there before generic docs; local file/chapter evidence is usually what they want.
- Keep parallel mechanisms separate when comparing systems. If the user says `heart和task_wather是三线,别串上了`, do not collapse watcher, heartbeat, and scheduler into one causal pipeline.
- For long-running flows, default to watcher/heartbeat based monitoring and one-shot status checks; avoid chat-loop polling (`不要一直轮询`).
- For project wiring requests, change the project's own runtime/config instead of drifting into external CLI or app configuration.
- When the user asks to `开一下` a practical feature or config, do the change directly when feasible rather than only explaining how.
- For storage cleanup, verify junctions, hardlinks, and physical-file identity before calling something a duplicate or promising reclaimed space.
- If deletion intent is narrow, preserve the meaningful artifact the user names as evidence, such as `lora留着证明我真的训了`.
- For destructive cleanup like recycle-bin emptying, narrow the scope to the intended volume and require explicit final confirmation.
- For article/source-checking or similar review tasks, leave behind organized files in a dedicated folder and give a direct verdict on whether it is靠谱.
- For product/runtime questions, answer in execution-boundary terms: what runs where, which config is separate, and which auth path was actually used.
- If the user explicitly says `停下`, treat the current long-running setup as intentionally aborted and do not continue without a fresh ask.
- When a thread-delivery result is shaky, verify with `thread/read` and give a paste-ready fallback quickly.
- For resume-style copy, start shorter than you think and keep it paste-ready.

## General Tips
- Environment default: Windows PowerShell.
- On this machine, Codex memories live under `C:\Users\19811\.codex\memories`; `AGENTS.md` is a separate instruction surface.
- In `my_auto_kaggle`, check `mak/cli.py`, `mak/env.py`, `.env`, and `.codex/config.toml` first for model/provider/MCP behavior.
- `PYTHONIOENCODING=utf-8` is a proven Windows fix for long transcript `UnicodeEncodeError`.
- For Windows storage analysis here, check `dir /al`, `fsutil reparsepoint query`, file IDs, and hashes before interpreting huge duplicate-looking folders.
- For WeChat pages, raw HTTP fetch plus HTML parsing can work even when browser/open-page tooling fails.
- For Codex thread transport on this machine, `thread/read` with `includeTurns:true` is the truth source; do not trust deep links or optimistic send responses alone.
- For current product-behavior questions about Codex/Claude Code, verify against the current manual/docs rather than memory.
- If a schedule or automation seems wrong, inspect the durable slot/receipt before assuming the automation record is the truth.

## What's in Memory

### D:\02_Projects\ML\stanford

#### 2026-07-17

- CS224R HW1 starter-code walkthrough: cs224r, hw1, flappy bird, imitation learning, behavior cloning, flow matching, dagger, action chunking
  - desc: Search first for the Stanford HW1 starter folder, the TODO files, and the recommended sequence in `cwd=D:\02_Projects\ML\stanford\hw1_starter_code\hw1_starter_code`.
  - learnings: the real work is in `networks.py`, `losses.py`, and `dagger.py`; action chunking predicts 20 actions but only executes the first 10 before replanning.

### D:\02_Projects\ML\agent

#### 2026-07-12

- Claude Code tutorial loop architecture vs AIDE execution model: Claude Code, AIDE, fs.watch, idle_notification, mailbox polling, tryClaimNextTask
  - desc: Search first for comparisons of Claude Code/Codex/AIDE control flow, especially when the user asks whether the loop is serial, blocking, watcher-driven, or event-driven.
  - learnings: keep heartbeat, watcher, and scheduler separate; the local Claude tutorial points to a mixed watcher/polling/claim model, while AIDE is a top-level serial search loop with per-run process isolation.
- Codex existing-thread delivery verification: codex app-server, ws://127.0.0.1:4500, thread/resume, turn/start, thread/read, includeTurns:true
  - desc: Search first when the user gives an exact Codex thread ID and wants content sent into that existing task rather than a new one.
  - learnings: use `thread/resume` + `turn/start`, keep the listener alive, and re-read the thread; if confidence is still low, give a paste-ready fallback immediately.

### D:\02_Projects\ML\agent\my_auto_kaggle

#### 2026-07-12

- Codex app heartbeat automations: heartbeat, automation_update, Restart-CodexDesktop.ps1, OpenAI.Codex, ChatGPT.exe, WindowsApps
  - desc: Search first for thread-bound wake alarms and scheduled desktop restart automation using Codex app automation_update, not MCP alarms.
  - learnings: a thread-bound wake test works with `destination: "thread"` and cleanup is a delete call after the heartbeat fires; the desktop restart helper validates the exact WindowsApps target and runs as a strict one-shot.

#### 2026-07-11

- Process-level read-only recovery and source-thread escalation: r416, MCP_INITIALIZE_METADATA_UNAVAILABLE, durable slot, codex_app__send_message_to_thread
  - desc: Search first for formal r416 recovery, durable-slot truth, process-level read-only operator blockers, and escalation back to the source thread.
  - learnings: the durable slot/receipt beats the automation record, the child can fail closed on missing initialize metadata, and hard blockers should be forwarded upstream immediately.

### D:\02_Projects\codex_do

#### 2026-07-11

- Cross-chat local article handoff limitation: cross-chat, attached chat, local article, read-only reference, paste fallback
  - desc: Search first when the user asks whether local workspace content can be sent into an attached ChatGPT chat.
  - learnings: the attached chat was readable context only in this rollout; no direct send path was found, so the safe fallback was upload/paste into ChatGPT.

### D:\02_Projects\ML\agent\mle-new

#### 2026-07-12

- Windows storage cleanup with junction/hardlink checks: storage-analyzer, MuMu, junction, hardlink, recycle bin, sd_xl_base_1.0.safetensors, LoRA
  - desc: Search first for machine-local storage triage, duplicate-vs-double-count checks, selective deletion planning, and recycle-bin confirmation on this Windows host.
  - learnings: MuMu was a junction to D:, SDXL paths were hardlinks, LoRA was intentionally preserved, and paper simulation `.npy` outputs were safe deletion targets after exact-file verification.
- Claude/Codex skills path and Khazix skill install: `C:\Users\19811\.claude\skills`, `.claude\skills`, `C:\Users\19811\.codex\skills`, `KKKKhazix/khazix-skills`
  - desc: Search first for local skill-directory questions, "can Claude Code skills connect over" questions, or third-party Codex skill installation on this machine.
  - learnings: no local Claude skills were installed at check time; Codex now has `aihot`, `hv-analysis`, `khazix-writer`, `neat-freak`, and `storage-analyzer`.

### D:\02_Projects\ML\jinyinsai

#### 2026-07-05

- AICOMP leaderboard queue tooling and heartbeat supervision: aicomp_leaderboard, queue_runner_start, queue_runner_watch, heartbeat, local queue fallback, Transport closed
  - desc: Search first for deque-style leaderboard tooling, worker/supervisor heartbeat design, retrain requeue sweeps, and local-file fallback when MCP transport is unstable.
  - learnings: the no-polling preference is strong here; queue/event/result files can prove final completion when the MCP transport is flaky.

### Older Memory Topics

#### D:\02_Projects\ML\agent

- Codex and Claude Code product/runtime boundaries: codex-plugin-cc, separate runtime, MCP, skills, config.toml, prompt caching, API pricing
  - desc: Use for current product-behavior questions about Codex inside Claude Code, runtime/tool inheritance, and billing/cache boundaries; applies to `cwd=D:\02_Projects\ML\agent` and needs current-doc verification.
- Direct syntax clarification and external AIDE mechanism lookup: t["status"], pending icon, AutoKaggle, AIDE, num_drafts, search_policy
  - desc: Use when the user points to a tiny code fragment and wants a direct Chinese explanation, or remembers an external AutoKaggle/AIDE mechanism only fuzzily; applies to `cwd=D:\02_Projects\ML\agent`.

#### D:\02_Projects\ML\agent\my_auto_kaggle

- Project-local DeepSeek wiring and real gateway call: .env, ANTHROPIC_AUTH_TOKEN, ANTHROPIC_BASE_URL, resolve_model, build_gateway, deepseek-v4-pro
  - desc: Search first when the user wants the project itself wired to a DeepSeek/Anthropic-compatible API and expects proof through a real call in `cwd=D:\02_Projects\ML\agent\my_auto_kaggle`.
- jinyinsai MCP integration and real DeepSeek submit: mak.mcp.remote_train, mak.mcp.jinyinsai_submit, validate_submission_file, confirm_real_submit, deepseek-v4-flash
  - desc: Search first for remote-train/submit MCP deployment into `my_auto_kaggle`, tool-use verification, and the real AICOMP submit gate.
- WeChat article access, extraction, and reliability packaging: mp.weixin.qq.com, Invoke-WebRequest, js_content, wechat_glm_nvidia_check, NVIDIA Build, 无限白嫖
  - desc: Search first when a specific WeChat article needs reachability checking, body extraction, folder packaging, or a direct trustworthiness verdict.
- Codex memory inspection and AGENTS.md boundary: AGENTS.md, memory_summary.md, MEMORY.md, raw_memories.md, C:\Users\19811\.codex\memories, idle window
  - desc: Search first for questions about whether Codex created memory yet, whether `AGENTS.md` is auto-created, or where local memory actually lives.
- Start UU远程 on this machine: UU远程, GameViewer, Netease, Start Menu shortcut, GameViewer.exe, GameViewerService
  - desc: Search first for direct app-launch help around UU远程 / NetEase GameViewer on this Windows host.
- AutoKaggle repo migration, Codex feature flags, and Git auth path: auto_reaserch, memories = true, undo = true, Git Credential Manager, HTTPS push, robocopy
  - desc: Search first for Windows-side repo migration, global Codex feature flags, and why a private GitHub repo accepted pushes in this checkout.

#### D:\02_Projects\ML\jinyinsai

- Resume-ready `jinyinsai` project summary: 简短介绍和自我评价, 再简短一些, CLIP, LoRA, FET, noisy labels, TTA
  - desc: Use for very short, paste-ready resume/project-experience wording for this competition project; applies to `cwd=D:\02_Projects\ML\jinyinsai`.
- AICOMP leaderboard queue tooling and heartbeat supervision: aicomp_leaderboard, queue_runner_start, queue_runner_watch, heartbeat, local queue fallback, Transport closed
  - desc: Use when the task is about the earlier leaderboard MCP / queue supervision work rather than the newer deep memory around storage or resume wording.
