thread_id: 019f3687-f0c6-73f0-9cee-7bcc98151553
updated_at: 2026-07-06T08:26:32+00:00
rollout_path: C:\Users\19811\.codex\sessions\2026\07\06\rollout-2026-07-06T16-25-06-019f3687-f0c6-73f0-9cee-7bcc98151553.jsonl
cwd: \\?\D:\02_Projects\ML\agent\agent_learning

# The rollout answered when Claude Code writes `.claude` and confirmed the current repo had no project `.claude/` directory.

Rollout context: The user asked in Chinese, roughly “When does Claude Code write `.claude`?” The assistant checked official docs and the local workspace at `D:\02_Projects\ML\agent\agent_learning`.

## Task 1: Explain when Claude Code creates `.claude`

Outcome: success

Preference signals:

- The user asked a direct, concise “when does it write `.claude`” question, which suggests future answers should focus on the specific trigger and file locations rather than broad Claude Code background.

Key steps:

- The assistant first read the local skill guidance file and then searched Anthropic docs for Claude Code settings.
- It checked the current repo for a `.claude` directory and found none: `NO .claude in current directory`.
- The final answer distinguished multiple `.claude`-related outputs by feature/scope instead of treating `.claude` as a single artifact.

Reusable knowledge:

- Claude Code does not necessarily create a project `.claude/` folder on startup; it creates `.claude` artifacts only when certain project-level or local features are used.
- Project-local settings are stored in `.claude/settings.local.json` when using local/project configuration changes such as `/config` options.
- Project subagents are stored in `.claude/agents/*.md`.
- Project skills and legacy custom commands live under `.claude/skills/.../SKILL.md` and `.claude/commands/*.md`.
- Project output styles are stored under `.claude/output-styles/`.
- MCP is scope-sensitive: `claude mcp add` with local scope writes `~/.claude.json`, while project scope writes `.mcp.json` at the repo root, not inside `.claude/`.

Failures and how to do differently:

- The rollout did not show a failure; the only useful caution is to answer this kind of question by separating file locations by Claude Code feature and scope, because “`.claude`” is not one single creation event.

References:

- `D:\02_Projects\ML\agent\agent_learning` had no `.claude` directory at the time of the check.
- Exact local check output: `NO .claude in current directory`
- Official docs referenced in the final answer: `https://code.claude.com/docs/en/settings`, `https://code.claude.com/docs/en/mcp`, `https://code.claude.com/docs/en/sub-agents`, `https://code.claude.com/docs/en/output-styles`
