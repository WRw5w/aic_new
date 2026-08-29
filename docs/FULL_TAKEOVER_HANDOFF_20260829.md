# AIC 完整换人接手说明

更新时间：2026-08-29。本文是新负责人和新 Agent 的首要入口。

## 一句话状态

初赛工程已经形成完整的训练、推理、打榜和证据链；当前应交付的合法可复现模型是 `b448_aligned formal v2`（78.8561），而 89.3620 / 92.0014 是使用 PE-Core-G14 的违规诊断实验。

## 远程交接分层

| 层 | 内容 | 用途 |
|---|---|---|
| GitHub 仓库 `WRw5w/aic_new` | 当前核心代码、测试、接手文档、研究报告 | 日常开发入口 |
| [`full-history-20260829` Release 记录包](https://github.com/WRw5w/aic_new/releases/tag/full-history-20260829) | 当前工作区的代码/配置/日志/榜单/提交证据、净化后的 Git 历史 bundle、Codex 与 Claude Code 记忆 | 追溯决策和恢复历史 |
| [`handoff-v1-20260816/full.pt`](https://github.com/WRw5w/aic_new/releases/download/handoff-v1-20260816/full.pt) | 合法参考模型 `b448_aligned formal v2` | 直接复现 78.8561 路线 |
| [`full-history-20260829/pe_core_g14_lora_ep04.pt`](https://github.com/WRw5w/aic_new/releases/download/full-history-20260829/pe_core_g14_lora_ep04.pt) | 92.0014 诊断模型的 LoRA＋分类头 checkpoint | 仅用于理解历史诊断，不得正式提交 |

## 接手后的第一小时

1. 克隆 `WRw5w/aic_new`，阅读仓库根 `README.md`。
2. 下载 `full-history-20260829` Release 的记录包，运行包内 `verify_record_handoff.ps1`。
3. 阅读记录包中的 `agent_memories/claude_project/memory/MEMORY.md`、`agent_memories/codex/MEMORY.md`。
4. 阅读 `record_snapshot/docs/打榜实验日志.md` 和 `record_snapshot/docs/CLAUDE_CODE_MEMORY_HANDOFF.md`。
5. 需要追溯某次代码决策时，用包内的 `git_history/jinyinsai_sanitized_all_refs.bundle` 恢复 132 个历史提交。
6. 需要复现正式模型时，按 `REPRODUCE_CHAMPION.md`；不要从 92 分诊断路线开始。

## Git 历史恢复

```powershell
git clone .\git_history\jinyinsai_sanitized_all_refs.bundle jinyinsai-history
cd jinyinsai-history
git branch -a
git log --all --oneline --decorate --graph
```

历史 bundle 保留所有分支、标签和提交脉络，但删除了曾误提交的两个 token 文件和一把 SSH 私钥及其公钥。旧仓库中出现过的凭据应视为已经泄露并轮换；不要尝试恢复或继续使用。

## 记录包包含什么

- 当前工作区中的源代码、脚本、配置、测试。
- `docs/`、研究报告、个人实验笔记、设计与复盘资料。
- 所有非数据型实验日志、指标 CSV/JSON、provenance、提交 ZIP、榜单快照、队列迁移与事故证据。
- `remote_results/`、`runs/`、`outputs*/`、`exp_pipelines/` 中除模型权重和特征缓存外的记录文件。
- Codex 自动记忆库、Claude Code 的本项目 memory 和项目会话记录。
- SHA-256 清单、排除清单、关键模型指针。

## 明确不包含什么

- `data/train`、`data/test`、历史数据集压缩包以及任何阶段的原始赛事图片。
- 批量 `.pt/.pth/.ckpt/.safetensors/.npy/.npz` 权重与特征缓存；只单独保留真正用于复现的关键模型。
- 活跃 token、cookie、浏览器 profile、账户密码和 SSH 私钥。
- Python/Node 依赖缓存、编译产物、临时目录、重复的第三方二进制。

这些排除不会删除原文件，只影响公开交接包。缺少数据时应从赛事官方渠道重新取得；缺少依赖时按 requirements 重建环境。

## 方法主线

合法方法由以下部分组成：冻结 OpenAI CLIP ViT-B/32；attn+MLP LoRA rank 32 / alpha 64；cleanlab 与 kNN 去噪；mixup 0.2；EMA 与 RandAugment；aligned 位置编码重采样；448–512 分辨率；同轨迹 SWA；多尺度翻转 TTA；测试集均衡先验校正。

失败路线也必须保留：跨 seed/跨配方 soup、DivideMix、激进 SSL 回收、双池化门控、ELR、FET、DoRA、SCE/APL、OT、TGP 等。新负责人应先查 `SCORECARD.csv` 和历史日志，避免重复烧 GPU。

## 92 分诊断路线的准确解释

`92.0014` 来自 `vit_pe_core_gigantic_patch14_448.fb`：PE-Core-G14-448 基座，最后 12/50 个视觉 Block 与 attention pool 加 LoRA（rank 16 / alpha 32），训练 5 epoch，使用 `ep04.pt`，水平翻转 TTA 与类别均衡后处理。分类头是 `Linear(1536, 500, bias=False)`，第 i 行对应按目录名排序后的第 i 个类别。

这证明规定的 B/32 骨干是主要瓶颈，但该模型违反赛题骨干限制。

## 自动打榜现状

旧 Claude 记忆中的 runner/watchdog/hourly alarm/直接 Node `skip-score` 流程已退役，只能作为事故史阅读。现行控制面在 `my_auto_kaggle`：候选必须有 provenance，队列操作和 runner 启动走 `jinyinsai_submit` MCP，监控走 `task_watcher`，默认 30 分钟 heartbeat。

## 关键核验值

| 资产 | SHA-256 |
|---|---|
| 合法 `b448_aligned formal v2/full.pt` | `8a349c46647166dcb4c0758f26cc8bde1926dfc7ed3b5b2a57c814b9d0d0c73a` |
| 92 分诊断 `pe_core_g14_lora ep04.pt` | `70c3d838dfa82bada7e734d5da5f1dd1cfbb496e57fa2747e729f98778571b73` |
| PE-Core-G14 官方 `model.safetensors` | `a0a499d4a7c6d35bb5f11aa2b076eaacde06e66a08176eb3b3d362d80eb91bd8` |

## 当前未决事项

- 后续赛段数据会更换，初赛 checkpoint 不能直接作为复赛/半决赛模型交付；应迁移方法与工程闭环。
- 自动打榜在新机器上仍需重新配置 Chrome 登录态、队伍/赛段 ID 和本机路径。
- 78.9122 的历史合法峰值缺少一一绑定的本地 checkpoint，只可作为历史记录。
- 曾暴露在旧公开 Git 历史中的凭据应由账号持有人确认已经轮换。
