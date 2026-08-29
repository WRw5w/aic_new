# AIC 项目协作规则

## 赛题硬边界

- 正式路线必须使用 OpenAI CLIP ViT-B/32；PE-Core-G14 的 89.3620 / 92.0014 仅是违规诊断探针，不能用于正式成绩、答辩或交付模型。
- 单模型、单推理流程；不得引入其他阶段数据或让测试集参与训练。
- 数据集、浏览器登录态、API token、SSH 私钥不进入 Git 或公开交接包。

## 当前交付基线

- 可复现正式参考：`b448_aligned formal v2`，榜分 78.8561。
- 模型：`runs/aic_b448_aligned_formal_v2_20260713/collected/full.pt`。
- 历史合法峰值 78.9122 没有找到一一绑定的本地 checkpoint，不能宣称为可复现冠军。
- 代表性成绩和合法性只看 `handoff/aic_team_handoff_20260816/SCORECARD.csv`，不要直接按原始账本最高值决策。

## 操作红线

- 自动打榜只允许走 `my_auto_kaggle` 的 `jinyinsai_submit` MCP 控制面；旧 watchdog、hourly alarm、直接 CDP/Node 提交流程只作历史资料。
- 长时间任务使用 `task_watcher`；没有明确指定时，heartbeat 默认每 30 分钟一次。
- 调试训练必须使用隔离的 `--work-dir`，不要覆盖正式 checkpoint、特征缓存或队列状态。
- 修改提交队列前先确认没有 active lock，并保留 provenance、artifact SHA-256 和 logical submission ID 的绑定。

## 新接手者阅读顺序

1. `docs/FULL_TAKEOVER_HANDOFF_20260829.md`
2. `handoff/aic_team_handoff_20260816/START_HERE.md`
3. `handoff/aic_team_handoff_20260816/REPRODUCE_CHAMPION.md`
4. `docs/打榜实验日志.md`
5. `docs/CLAUDE_CODE_MEMORY_HANDOFF.md`
6. `handoff/aic_team_handoff_20260816/AUTO_LEADERBOARD_RUNBOOK.md`

## 常用验证

```powershell
D:\04_Tools\Python\python.exe -m pytest -q `
  tests/test_lora_contract.py `
  tests/test_three_way_split.py `
  tests/test_robust_utils.py `
  tests/test_aicomp_queue_runner_resilience.py `
  tests/test_aicomp_queue_identity_safety.py `
  tests/test_aicomp_cdp_cli.py `
  --basetemp .pytest_handoff_project
```

项目结构和基础训练命令见 `README.md`；完整历史资产的恢复方式见接手文档。
