# AIC 记录型完整交接包

本目录用于构建 2026-08-29 的完整换人交接资产。目标是保留可复现所需的代码、配置、日志、榜单证据、Git 历史与 Agent 记忆，同时排除赛事原始数据、批量重复权重和可直接控制账号的凭据。

运行：

```powershell
.\build_record_handoff.ps1
```

构建结果位于 `build/`，包括：

- `record_snapshot/`：当前工作区记录快照。
- `agent_memories/`：Codex 与 Claude Code 记忆。
- `git_history/`：删除凭据路径后的完整 Git bundle。
- `ARTIFACT_POINTERS.csv`：关键模型位置和哈希。
- `EXCLUSIONS.md`：排除边界。
- `MANIFEST.sha256.csv`：文件完整性清单。
- 同名 ZIP：用于上传 GitHub Release。

验证：

```powershell
.\verify_record_handoff.ps1 -PackageRoot <解压后的目录>
```
