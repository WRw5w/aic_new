# AIC 队友交接：从这里开始

交接日期：2026-08-16

> 版本提示：本文保留 2026-08-16 的首版交接设计。完全换人接手时，应以 2026-08-29 的 [`FULL_TAKEOVER_HANDOFF_20260829.md`](FULL_TAKEOVER_HANDOFF_20260829.md) 为当前入口；其中记录了已经落地的公开仓库、完整记录包、Agent 记忆和净化历史。本文与新入口冲突的私有化、资产排除和自动打榜描述均视为历史决策。

这份目录把「比赛判断、可复现代码、已验证结果、模型资产、自动打榜控制面」拆成五层。目标不是把原工作目录完整复制给队友，而是让队友在 30 分钟内知道当前最强结论、在半天内跑通环境与推理、在拿到新阶段数据后能安全继续训练。

## 先看结论

- 历史最高合法榜分是 `78.9122`（`b512_aligned`），但当前本地扫描没有找到与它一一绑定的 checkpoint，不能把它写成「已可复现交付冠军」。
- 当前最适合交付的参考资产是 `b448_aligned formal v2`：榜分 `78.8561`，本地有 `full.pt`、训练日志、提交 ZIP 和 provenance，四者能闭环。
- 两个 `89.3620 / 92.0014` 条目使用了违规超大骨干或训练集标注探针，只能当诊断证据，严禁作为正式路线、宣传成绩或决赛材料。
- 初赛不计最终线上综合分；复赛占 40%，半决赛占 60%。后两阶段换数据、增大类别规模并引入长尾，因此真正该传的是方法和工程闭环，不是初赛单一 checkpoint。
- 现有 GitHub `WRw5w/lihao` 是公开仓库，不应继续作为竞赛交接仓库。建议新建私有仓库，并只邀请队友。

## 阅读顺序

1. `AIC_比赛与交接研究报告.md`：比赛、项目演进、横向方案比较和接手策略。
2. `REPRODUCE_CHAMPION.md`：如何验证并复现当前可交付参考模型。
3. `AUTO_LEADERBOARD_RUNBOOK.md`：自动打榜机的状态机、安装依赖、边界和故障处理。
4. `SCORECARD.csv`：经过合规标记的代表性榜分，不再混淆合法成绩和诊断探针。
5. `ARTIFACTS.csv`：大文件和关键产物的路径、大小、SHA-256、交付等级。

## 推荐传输结构

```text
私有 Git 仓库
├─ core/                    训练、推理、校验代码
├─ tests/                   无 GPU 单测与安全回归
├─ docs/                    研究报告、复现说明、实验结论
├─ leaderboard_control/     自动打榜控制面源码与调用契约
└─ manifests/               分数表、资产哈希、provenance

私有 Release 资产（或 Git LFS）
├─ b448_aligned_formal_v2_full.pt
└─ pred_b448_aligned_tta_balanced.zip

不上传
├─ data/train、data/test
├─ Chrome profile、cookies、token、API key
├─ submissions 的整份历史状态/个人绝对路径
└─ 违规探针 checkpoint 与预测结果
```

## 立即可执行的交接动作

在 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\handoff\aic_team_handoff_20260816\build_handoff.ps1
```

脚本会在 `handoff/aic_team_handoff_20260816/build/` 下生成一个白名单导出的核心目录、文件哈希清单和 ZIP。它默认不会复制 783 MB checkpoint，避免无意制造第二份大文件。模型应直接从 `ARTIFACTS.csv` 指定的原始路径上传到私有 Release，再由队友按 SHA-256 校验。

队友下载后运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\verify_handoff.ps1 -PackageRoot .
```

## 权限边界

- 本包只适合发给同一参赛团队成员或明确授权的指导教师。
- 比赛数据不进入 Git、Release 或聊天附件。
- 自动打榜依赖本地已登录的专用 Chrome profile。每个队友在自己的机器上登录，不传 cookies。
- 实际提交是外部副作用。只有得到明确「提交这个候选」授权后，才能启动 `submit_candidate` runner；其余时候只读状态。
