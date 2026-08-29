# Agent 记忆交接

本目录提供能在 GitHub 直接阅读的 Codex 与 Claude Code 项目记忆，用于让新负责人或新 Agent 快速恢复上下文。

建议阅读顺序：

1. `../docs/FULL_TAKEOVER_HANDOFF_20260829.md`
2. `codex/MEMORY.md`
3. `claude/MEMORY.md`
4. 需要追溯某条路线时，再查看对应 rollout summary 或专题记忆。

这里的记忆是历史记录，不是所有内容都仍然有效。特别是旧 Claude 记忆中的 runner、watchdog、hourly alarm 和直接调用 Node `skip-score` 流程已经退役；现行自动打榜边界以仓库根 `CLAUDE.md` / `AGENTS.md` 和完整接手说明为准。

完整 Claude Code 项目会话 JSONL、完整 Codex 自动记忆目录、记录快照和净化后的 Git bundle 位于 [`full-history-20260829` Release](https://github.com/WRw5w/aic_new/releases/tag/full-history-20260829) 的记录压缩包中。为避免公开仓库继续传播可用凭据，打包时仅对 token、私钥等认证材料做了删除或脱敏；普通项目上下文和历史决策均予以保留。
