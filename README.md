# AIC 2026 团队交接仓库

本仓库是“面向噪声标签数据的细粒度图像识别鲁棒微调”赛题的团队交接快照，包含核心训练/推理代码、实验记录、复现说明和自动打榜控制代码。

请从 [`docs/START_HERE.md`](docs/START_HERE.md) 开始。

当前正式交接方案：`b448_aligned formal v2`，线上分数 `78.8561`。对应模型 `full.pt` 不进入 Git 历史，请从仓库 Release 下载，并按 `docs/ARTIFACTS.csv` 中的 SHA256 校验。

重要说明：`89.3620`、`92.0014` 是违规探测结果，不得用于正式提交或成绩汇报。

## 目录

- `project/`：比赛项目代码、配置与选定实验产物
- `leaderboard_control/`：自动打榜控制端的安全子集
- `docs/`：复现、成绩、资产和交接文档
- `MANIFEST.sha256.csv`：交接快照文件哈希清单

## 校验与复现

交接快照中的原始文件哈希记录在 `MANIFEST.sha256.csv`。模型下载完成后，请按 `docs/ARTIFACTS.csv` 中记录的 SHA256 单独校验。

完整步骤以 `docs/START_HERE.md` 和 `docs/REPRODUCE_CHAMPION.md` 为准。
