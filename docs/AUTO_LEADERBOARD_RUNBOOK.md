# AIC 自动打榜机交接手册

## 它现在是什么

自动打榜不是一个「每小时上传文件」的脚本，而是两仓协作的受控状态机：

- 竞赛仓 `jinyinsai`：保存候选 ZIP、队列 ledger、浏览器 CDP helper、提交 runner、榜单证据与历史结果。
- 控制仓 `my_auto_kaggle`：提供 `jinyinsai_submit` MCP，把真实提交限制在 provenance、exact intent、窗口锁和一次性抓分 claim 之内。

当前本机状态：队列共 171 项，`scored=151 / dropped=18 / skipped=1 / paused=1`，无 active lock、无下一项，状态为 idle。公开榜显示的是团队最近一次已发布提交，不是历史最高，因此公开榜当前分数低于 78.9122 并不代表历史成绩丢失。

## 为什么不能把旧脚本直接发给队友

历史脚本曾发生过同一小时多发覆盖、accepted 被误当 scored、错误提交无均衡 `_tta`、抓榜只看第一页等事故。现在以下入口已隔离或只读：

- `aicomp_queue_watchdog.mjs`
- `aicomp_hourly_acceptance_alarm.mjs`
- `aicomp_goal_watch_until_noon.mjs`
- `aicomp_enqueue_backlog.mjs`
- `aicomp_import_next_queue.mjs`
- `aicomp_apply_guide_queue.mjs`
- `aicomp_reconcile_leaderboard.mjs`
- `aicomp_wait_all_accepted.mjs`
- 旧 `mcp_aicomp_leaderboard` 的所有突变工具

队友只使用 `my_auto_kaggle/mak/mcp/jinyinsai_submit.py` 提供的控制面。

## 机器依赖

1. Windows 10/11。
2. Python 3.11+。
3. Node.js，当前实现默认路径是 `C:\Program Files\nodejs\node.exe`。
4. Chrome/Chromium，使用专用 profile，并开放本机 `127.0.0.1:9222` CDP。
5. 两个代码目录。推荐：

```text
D:\02_Projects\ML\jinyinsai
D:\02_Projects\ML\agent\my_auto_kaggle
```

当前控制面还有硬编码路径、队伍 ID、排行榜参数和 helper SHA-256。换目录、换队伍、换阶段或改 helper 后，必须先维护配置与测试，不能靠复制旧 JSON 状态绕过。

## 首次安装

```powershell
cd D:\02_Projects\ML\agent\my_auto_kaggle
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
python -m pytest -q tests/test_mcp_jinyinsai_submit.py tests/test_mcp_jinyinsai_submit_safety.py
```

在 Codex MCP 配置中注册：

```toml
[mcp_servers.jinyinsai_submit]
command = "D:\\02_Projects\\ML\\agent\\my_auto_kaggle\\.venv\\Scripts\\python.exe"
args = ["-m", "mak.mcp.jinyinsai_submit"]

[mcp_servers.task_watcher]
command = "D:\\02_Projects\\ML\\agent\\my_auto_kaggle\\.venv\\Scripts\\python.exe"
args = ["-m", "mak.mcp.task_watcher"]
```

不要把比赛账号、cookie 或 token 写进 TOML。

## 登录浏览器

```powershell
powershell -ExecutionPolicy Bypass -File D:\02_Projects\ML\jinyinsai\tools\aicomp_start_chrome.ps1 -Restart
```

在打开的专用 Chrome 中由队友本人登录 AICOMP。登录状态只保存在本机 profile；不压缩、不上传、不发聊天。

先检查 `http://127.0.0.1:9222/json/version` 返回合法 browser websocket，再做任何提交动作。

## 候选进入队列前

候选必须满足：

- ZIP 内只有一个 `pred_results.csv`。
- CSV 行数等于当前阶段测试图数。
- 文件名、四位类别编号、编码和换行格式通过校验。
- 只使用当前阶段官方数据。
- 正式路线只使用 CLIP ViT-B/32，单模型单推理。
- 有 provenance manifest，绑定候选 SHA-256、checkpoint SHA-256、模型 ID、实验 ID、证据文件。

生成和校验：

```powershell
python check_submission.py --zip <candidate.zip>
python -m mak.aic.provenance <candidate.provenance.json>
```

## 唯一允许的提交流程

1. 调一次 `validate_submission_file`。
2. 调 `queue_push_front` 或 `queue_push_back`，候选与 provenance 一一对应。
3. 调一次 `queue_status`，确认：
   - `contractVersion == aicomp-submit-agent-contract/v4`
   - `queueSchemaVersion == 3`
   - `requiredCallerSandboxMode == read-only`
4. 读取 `runnerStartIntent`，不得修改 action、queue index、candidate SHA-256、logical submission ID 或 revision。
5. 只有用户明确授权真实提交时，原样回传 intent，并设置 `confirm_real_submit=true`。
6. 启动后挂 `queue_runner_watch` 或 `task_watcher`。不要在聊天中不断轮询。
7. 等平台准确发布时间。已知时间就精确等到一次；未知时 watcher 默认 30 分钟间隔。
8. `accepted` 不是完成。只有强绑定分数证据写入 `scored`，active lock 才闭环。

## 状态与动作

| 状态 | 允许动作 |
|---|---|
| `idle` | 报告空闲；有新候选时才入队 |
| `queued` | 核对候选身份；明确授权后启动一次 |
| `running` | 只挂 watcher，不再启动或改队列 |
| `waiting_score` | 等精确窗口；到点只运行 capture-only |
| `score_missed_blocked` | 取一次逐提交证据或合法 late-publication recovery；不得重抓原窗口 |
| `submit_not_dispatched_reconcile_required` | 只按 exact reconciliation intent 恢复同一 identity |
| `blocked_identity_corruption` | 停止，提交维护请求；禁止手改 JSON |
| `needs_attention` | 保存证据并人工判断；禁止自动复活失败项 |

## 三条硬铁律

1. 一小时一个 effective submission。存在 active/awaiting_score 时绝不发下一项。
2. 同一候选的 path、内容 hash、logical identity 都不可重复；不存在 agent override。
3. 公开榜只在 teamSubmitTime 与 accepted 时间闭环时才能归分。队名或分数相近不能当证据。

## 换阶段时必须改什么

- 比赛 stage / leaderboard ID。
- 当前阶段测试集行数和受信数据根。
- 队伍 ID 与队名（若变化）。
- ZIP 校验上限。
- 旧队列必须归档为只读历史，不能把初赛 queue ledger 继续当新阶段队列。
- 新阶段建立新的 `competitionScope`，避免同 hash/文件名跨阶段碰撞。
- helper 修改后重新计算 SHA-256，并跑 fake-only 与安全测试。

## 当前可移植性缺口

- `SUBMISSION_RECORDS_HELPER` 是本机绝对路径。
- Node 可执行文件是 Windows 固定路径。
- team ID、team name、公开榜 page/rw/stage ID 写在源码常量中。
- helper hash 与源码严格绑定；复制后若换行符或内容变化，控制面会 fail closed。
- Chrome profile 和登录步骤仍是机器本地状态。

所以本次交接包提供的是「精确源码 + 契约 + 测试 + 配置清单」，不是无脑双击版。队友机器首次部署应作为一次独立维护任务：只跑 fake tests 和只读页面检查，确认后再由你授权第一发真实提交。

## 禁止传输

- Chrome user-data-dir。
- cookies、localStorage、session token。
- XGY token、GitHub token、HF token。
- `submissions/` 全量状态目录和个人绝对路径日志。
- 任何旧 automation/heartbeat 定义。
- 含数据集内容的缓存、压缩包或临时目录。

