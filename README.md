# AIC 2026 团队交接仓库

本仓库是“面向噪声标签数据的细粒度图像识别鲁棒微调”赛题的团队交接快照，包含核心训练/推理代码、实验记录、复现说明和自动打榜控制代码。

请从 [`docs/START_HERE.md`](docs/START_HERE.md) 开始。

如果是完全换人接手，请改从 [`docs/FULL_TAKEOVER_HANDOFF_20260829.md`](docs/FULL_TAKEOVER_HANDOFF_20260829.md) 开始。它解释了完整历史包、Codex/Claude Code 记忆、Git 历史恢复方式，以及哪些 92 分实验只能作为诊断记录。

当前正式交接方案：`b448_aligned formal v2`，线上分数 `78.8561`。对应模型 `full.pt` 不进入 Git 历史，请从仓库 Release 下载，并按 `docs/ARTIFACTS.csv` 中的 SHA256 校验。

重要说明：`89.3620`、`92.0014` 是违规探测结果，不得用于正式提交或成绩汇报。

## 目录

- `project/`：比赛项目代码、配置与选定实验产物
- `leaderboard_control/`：自动打榜控制端的安全子集
- `docs/`：复现、成绩、资产和交接文档
- `agent_memories/`：可直接浏览的 Codex 与 Claude Code 项目记忆；完整项目会话在 Release 记录包中
- `handoff_tools/`：记录包的构建、排除与校验脚本
- `MANIFEST.sha256.csv`：交接快照文件哈希清单

## 完整历史和关键模型

- [完整记录交接 Release](https://github.com/WRw5w/aic_new/releases/tag/full-history-20260829)：不含赛事数据，包含记录快照、Agent 记忆与净化后的全部 Git 历史。
- [合法参考模型 Release](https://github.com/WRw5w/aic_new/releases/tag/handoff-v1-20260816)：`b448_aligned formal v2/full.pt`，用于复现 78.8561 路线。
- `pe_core_g14_lora_ep04.pt` 随完整记录 Release 单独提供，只能复盘 92.0014 违规诊断实验，不能正式提交。

## 校验与复现

交接快照中的原始文件哈希记录在 `MANIFEST.sha256.csv`。模型下载完成后，请按 `docs/ARTIFACTS.csv` 中记录的 SHA256 单独校验。

完整步骤以 `docs/START_HERE.md` 和 `docs/REPRODUCE_CHAMPION.md` 为准。
