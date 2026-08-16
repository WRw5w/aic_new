# jinyinsai_submit 调用 Agent 契约

契约版本：`aicomp-submit-agent-contract/v4`
canonical 文件：`docs/JINYINSAI_SUBMIT_AGENT_CONTRACT.md`
适用 MCP：`mak.mcp.jinyinsai_submit`
调用角色：`aic-submit-watch`
维护角色：主 agent；`deepseek-code-review` 只读审查

## 1. 文档放在哪里

采用三层结构，不能只把全部规则复制进子 agent prompt：

1. **MCP 代码是安全事实来源**：去重、状态机、intent、锁、lease、receipt、capture claim 和 CDP fence 必须由代码强制执行。文档不能代替门禁。
2. **本文件是唯一完整调用契约**：状态语义、标准流程、错误处置和变更治理只在这里维护一份，并通过 `contractVersion` 与运行中的 MCP 对齐。
3. **子 agent prompt 是薄适配层**：只写角色职责、必须读取本契约、标准入口、禁止修改控制面和结构化上报格式。不得复制一份会逐渐漂移的 MCP 实现说明。

`AGENTS.md` 保存项目级硬边界。如果三处发生冲突，当前用户/系统指令和 `AGENTS.md` 优先；MCP 实际拒绝结果优先于本文件的历史描述。

## 2. 版本与启动前检查

每个提交或抓分任务第一次调用必须只调用一次 `queue_status`，并检查：

```text
contractVersion == aicomp-submit-agent-contract/v4
queueSchemaVersion == 3
requiredCallerSandboxMode == read-only
```

调用工具前还必须从系统提供的运行时权限信息确认实际 sandbox 为 `read-only`。自定义 agent 文件虽然设置了 `sandbox_mode = "read-only"`，但 Codex 可能重新施加父任务的实时权限覆盖；若实际模式可写或无法确认，只返回 `OPERATOR_SANDBOX_NOT_READ_ONLY` 并停止，不得用创建/修改文件来“测试”权限。主 agent 应先把父任务权限调整为只读，再重新派发新的操作任务。

`read-only` 限制的是调用 agent 直接写工作区，不取消已经明确授权的 MCP 操作。queue、runner、receipt、claim 和结果文件只能由 `jinyinsai_submit` MCP 自己维护；如果部署方式导致 MCP 也无法写入，应修复 MCP 部署，不能把调用 agent 升权。Codex 自定义 agent 的 sandbox 配置与父任务实时覆盖语义见 [Codex Manual — Custom agents](https://developers.openai.com/codex/codex-manual.md#custom-agents)。

如果字段缺失或版本不一致，说明 MCP 进程可能仍加载旧代码：

- 停止所有突变和 runner start。
- 请求重启/重新加载 `jinyinsai_submit` MCP。
- 重启后只做一次 `queue_status` 复核。
- 不得通过修改 prompt、队列 JSON、Node runner 或禁用版本检查继续执行。

## 3. 权限和文件所有权

`aic-submit-watch` 是**调用者/操作员**，不是 MCP 维护者。它可以：

- 调用 `jinyinsai_submit` 暴露的工具。
- 读取返回状态、runner 日志和证据。
- 在 `queue_status` 明确返回 `submit_not_dispatched_reconcile_required` 和 exact `reconciliationIntent` 时，调用一次 `queue_reconcile_not_dispatched`；该动作只恢复同一 queue item，不启动提交。
- 挂 `queue_runner_watch` / `task_watcher`。
- 返回 `PROJECT_STATE.md` 和任务报告的结构化更新建议，由主 agent 核验后写入。
- 返回 `MCP_CHANGE_REQUEST` 和 `memory_candidates`。

它不得修改、覆盖、删除或“临时修复”：

- `mak/mcp/jinyinsai_submit.py`
- `D:/02_Projects/ML/jinyinsai/tools/aicomp_submit_queue.mjs`
- `D:/02_Projects/ML/jinyinsai/tools/aicomp_cdp.mjs`
- `D:/02_Projects/ML/jinyinsai/tools/aicomp_migrate_queue_v3.py`
- `.codex/config.toml` 或 MCP 注册配置
- `aicomp_submit_queue.json`、active sidecar、runner/control lock、events/results ledger、capture claim
- 安全测试、错误码、退出码 77 的 legacy quarantine

遇到缺能力或疑似 MCP bug 时，执行 agent 必须停在当前安全状态并上报；不得为了完成目标而修改控制面。只有主 agent 在一个单独的“维护任务”中，经过只读审查、无真实提交测试、完整回归和必要迁移后才能修改 MCP。

调用 agent 永远不得直接修改控制面；本契约授予的是工具调用权，不是代码、配置或 durable state 的维护权。

## 4. 状态机与唯一允许动作

| `queue_status.state` | 含义 | 调用 agent 唯一允许动作 |
|---|---|---|
| `idle` | 无 active、无 queued、无 live runner | 报告空闲；只有新候选和 provenance 齐备时才入队 |
| `queued` | 有候选且无 active | 核对候选身份；使用最新 `submit_candidate` intent，明确授权后启动一次 |
| `running` | runner/lease 正在执行 | 只挂 watcher；不得再启动、移动、删除或重入队 |
| `waiting_score` | 已接受提交等待发布/抓分 | 未到 `capture_start_at` 精确等待；到点后只使用 `capture_score` intent |
| `score_missed_blocked` | 当前接受项未取得可靠分数 | 永不重抓原窗口。可取一次逐提交证据；也可在 `queue_status` 返回 exact `publicLeaderboardRecoveryIntent` 时，对正确公开榜的一个更晚新发布窗口读取一次。两条路径都只有 exact `scoreFinalizeIntent` 才可 finalize |
| `submit_not_dispatched_reconcile_required` | 唯一 bounded runner 证据证明提交按钮未被点击；同一 identity 尚未对平台派发 | 原样回传 `reconciliationIntent`，只有明确授权后调用一次 `queue_reconcile_not_dispatched(confirm_retry_same_identity=true, intent=<exact>)`；再做一次 `queue_status` 取得新 submit intent |
| `blocked_identity_corruption` | queue、active、receipt、identity 或 claim 不一致 | 停止并提交 `MCP_CHANGE_REQUEST`；不得编辑 JSON 修复 |
| `needs_attention` | 有已知失败/未闭环历史 | 报告失败证据；不得把失败项自动重置为 queued |

任何状态下，只要 `identityIssues` 非空、存在未证明为 no-dispatch 的 `outcome_unknown`、stale lease、多个 blocking item 或 receipt 不确定，都没有自动提交权限。`reconciliationIntent` 只允许恢复原 queue item 为 `queued`，本身绝不授权真实提交。

## 5. 标准调用流程

### 5.1 新候选入队

1. 确认候选来自新实验/新推理结果；不得只改名或重新打包历史预测。
2. 生成 provenance schema v2 manifest，绑定 experiment ID、candidate artifact id/规范路径/ZIP SHA-256、backbone/model ID，以及每个 evidence/parent 的 artifact id、规范路径和实算 SHA-256；parent 还要绑定 model ID。旧 v1 manifest 必须从实际文件重新生成，不能原样复用。
3. 运行 `python -m mak.aic.provenance <manifest.json>`，必须 `ok=true`。
4. 调用 `validate_submission_file`，必须通过行数、文件名、类别和 ZIP 结构校验。
5. 调用一次 `queue_status`，验证契约版本和当前状态。
6. 调用一次 `queue_push_front/back`。出现 duplicate 时停止；不得改名、重压缩或寻找 override。

### 5.2 真实提交

1. 调用一次最新 `queue_status`。
2. 要求 `runnerStartIntent.action=submit_candidate`，并核对 queue index、candidate SHA-256、logical submission ID、queue revision 都属于目标候选。
3. 只有当前任务明确授权真实提交时，原样回传完整 intent，并使用 `confirm_real_submit=true`。
4. `queue_runner_start` 在持有控制锁后、创建 launch receipt、lease、attempt 或 child runner 前，会对固定本地 CDP endpoint 做一次有界 readiness probe。若返回 `CDP_UNAVAILABLE_BEFORE_RUNNER_START`，本次调用没有创建任何 runner/lease/attempt，也没有消费或改变原 intent；先用项目记录的专用 Chrome 启动入口恢复已登录的 CDP，再从一次新的 `queue_status` 重新核对状态。不得绕过探针或直接运行 CDP/Node 提交脚本。
5. 成功启动后立即挂 watcher；不得手动轮询。持久顶层任务可使用默认通知模式并在有 PID 时附加 `task_watcher.watch_pid`。一次性的 process-level operator 必须在启动 runner 的同一个 MCP 进程里调用 `queue_runner_watch(wait_for_completion=true, timeout_seconds<=240)`，让 MCP host 持有刚启动的 child runner 直到终态，禁止注册后台 watcher 后立即退出。阻塞 watch 超时属于结果未知，必须对账后再决定，不能重放 start。
6. `submit-once` runner 在平台接受回执和 durable active lock 写入后立即正常结束；它不得为了等待下一个整点而在进程内睡眠。后续榜单读取必须由新的、exact intent 绑定的 `capture-only` runner 在 `capture_start_at` 到达后执行。这样提交 watcher 的生命周期只负责证明提交终态，抓分 watcher 的生命周期只负责证明抓分终态。
7. blocking watch 若返回 `finalQueueItem`，只在 `finalQueueItemIssue` 为空且其 index、candidate SHA-256、logical submission ID 与 runner intent 全部一致时使用其中的 score/time 字段；缺失或不一致只能报告诊断，不得归分。

### 5.3 明确未派发的提交尝试

只有 `queue_status.state=submit_not_dispatched_reconcile_required` 且 `identityIssues=[]` 时才进入此流程。MCP 必须已验证：唯一 matching runner、候选 SHA/logical ID/queue index 一致、queue item exit code 为 5、queue item / persisted runner / lease execution intent 的非空 runner ID 与 attempt ID 完整一致、stdout 位于 `submissions`、恰好三条 submit-button `clicked=false`、没有 `SUBMIT_CLICKED_AT`/`SUBMIT_ACCEPTED_AT`，并把 stdout SHA-256、attempt ID、runner ID 和 queue revision 绑定进 exact `reconciliationIntent`。旧记录缺少 item runner ID 时 operator 与 maintainer 都必须 fail closed，不能凭时间或日志唯一性推断。只有 item 已有 durable runner ID、同 identity/time window 在检查日志 proof 前就只有一个 runner、queue note 精确匹配 stdout 尾部、唯一 runner.started → submit.start → outcome_unknown 事件链匹配时，主 agent 才可在独立维护任务中基于同一次 queue/runner/events 快照做 exact CAS、queue/runner 双备份并升级为 `legacy-attempt-binding-v2`；完成后再派发新的只读 operator。

1. 不得根据普通页面文字、上传成功、exit code 单独判断未提交；缺任一证据仍是 `outcome_unknown`。
2. 原样回传完整 `reconciliationIntent`，调用一次 `queue_reconcile_not_dispatched(confirm_retry_same_identity=true, intent=<exact>)`。
3. 成功结果必须保持原 queue index、candidate SHA-256 和 logical submission ID，只追加 attempt history/backup/event 并把原 item 设为 `queued`；不得创建新 item、改名、重压缩或生成新 identity。
4. reconciliation 不会启动 runner。若仍有真实提交授权，再调用一次新的 `queue_status`，人工核对新的 `submit_candidate` intent 后按 5.2 启动一次。
5. intent/evidence/queue 任一变化都必须返回 stale 并停止；不得自动刷新后重放。

### 5.4 纯抓分

1. active 必须是目标 queue index，且 acceptedAt、logical ID、candidate SHA-256 完整一致。
2. 未到 `capture_start_at` 时使用一次性精确唤醒，不查询 leaderboard。
3. 到点后做一次 `queue_status`，要求 `runnerStartIntent.action=capture_score`。
4. 原样回传 intent，使用 `confirm_real_submit=false`。
5. 同一 publication window 已有 claim 时不得再次抓榜。

### 5.5 完成归属

只有以下证据同时闭环才能声明候选已得分：

- candidate ZIP / SHA-256 / logical ID / queue index 一致。
- submittedAt / acceptedAt 来自明确平台成功 receipt。
- runner、snapshot、result row 属于该 queue index。
- public row 的 `teamSubmitTime` 与当前 accepted submission 匹配，或取得 `matched=true` 且 `scoreAttributionReady=true` 的唯一 per-submission record。后者要求同一条 identity-matched record 同时包含 numeric score 与 score time；不得跨记录拼接身份和分数字段。
- score / scoreTime / leaderboard publish time 可解析且不是旧行。

### 5.6 已消费窗口后的逐提交分数 finalize

1. 只适用于唯一 active/item 同为 `score_missed`，且原 capture claim 仍存在、内容未变的情况；claim 永久保留，不恢复 `awaiting_score`。本小节只描述逐提交来源；公开榜的新窗口恢复只能走 5.7 的独立 claim 路径。
2. 调用一次 `aicomp_submission_records_fetch`。普通 `matched=true` / `scoreAttributionReady=true` 仍不足以授权写分；还必须有 `scoreFinalizeReady=true` 和 exact `scoreFinalizeIntent`。
3. finalize 强身份只接受 exact queue/platform identity、exact attachment SHA-256，或兼容旧页面时同时具备 exact team ID、稳定 record/work ID、exact filename 且与 accepted/submitted time 相差不超过 60 秒。filename-only、15 分钟 time-only、缺稳定 record ID 均不得生成 intent。
4. 原样回传 intent，调用一次 `queue_finalize_submission_record_score(confirm_score_attribution=true, intent=<exact>)`。调用方不得手填 score、scoreTime、record ID 或 evidence path。
5. MCP 在 control lock 内重新读取并校验 candidate bytes、queue/active/results revisions、claim/evidence path 与 SHA-256、唯一 record 及时间因果；任何 canonical 写入前先完成 CSV schema/conflict、active identity 和 queue 前态预检。
6. finalize 使用 schema 3、带 SHA-256 sidecar 的阶段 WAL：`prepared -> result_applied -> queue_applied -> active_clear_prepared -> active_cleared -> event_applied -> committed`。receipt 在写入前计算完整 queue document、results CSV 与 active 的精确 before/after revisions；每个外部写入只允许对应阶段的预期 revision，同一 intent 的自身故障断点可幂等恢复，任何额外 queue/results 变化都 fail closed。所有未 committed 的公开榜恢复阶段还会重新检查 accepted history 中不存在更晚提交。
7. 裸 `leaderboard_snapshot` 不再属于公开 MCP 工具面；该路径不得创建 capture claim、删除旧 claim、启动 runner 或提交下一候选。public leaderboard 只能由 intent-bound capture/finalize 路径读取。

### 5.7 已消费原窗口后的公开榜新发布窗口恢复

1. 公开排行榜的唯一 canonical 页面是 `https://reg.aicomp.cn/special/phb/detail?id=4832828643476639839&rwId=4829238709759119407&stbh=4829238709759119431`。队伍页、提交页和“作品打分结果”页都不是公开排行榜，不能替代此 URL。
2. 原 capture claim 及其原 publication window 永久保持已消费，绝不删除、改写或重放。只有 `queue_status` 返回 exact `publicLeaderboardRecoveryIntent` 时，才说明当前唯一 `score_missed` item 尚无更晚 accepted submission，且原 claim、candidate、queue、active、results history 均已绑定。
3. 原样调用一次 `aicomp_public_leaderboard_fetch(confirm_public_leaderboard_read=true, intent=<exact>)`。MCP 在任何联网前先写一个带 SHA-256 sidecar、绑定原 claim 实际 SHA 的永久 consumption receipt，再创建独立单次 `public_leaderboard_recovery_claim`；它不启动 runner、不提交候选，也不复用原 claim。失败、超时或未知状态同样永久消费；claim、consumption receipt 或 sidecar 缺失/变化均 fail closed，绝不再次联网。
4. 只有页面 URL 精确匹配 canonical URL，team ID/name 精确匹配，`teamSubmitTime` 与 acceptedAt 相差不超过 60 秒，score/scoreTime 时间因果成立，且 `leaderboardPublishTime` 严格晚于原 claim 的 `expectedPublishAt`，才返回 exact `scoreFinalizeIntent`。存在任何更晚 accepted submission 时必须拒绝，防止把新提交的公开榜行归给旧候选。
5. 原样调用一次 `queue_finalize_public_leaderboard_score(confirm_score_attribution=true, intent=<exact>)`。MCP 在同一阶段 WAL 中绑定 candidate bytes、原 claim、recovery claim、永久 consumption receipt、公开榜 evidence、accepted history、完整 queue document/active/results revisions；原 claim、recovery claim 与 consumption receipt/sidecar 均永久保留。captured recovery claim 的幂等重放只返回原 evidence，不再次联网。
6. 该路径只恢复“平台在原失败窗口之后的新小时窗口才发布了同一 accepted submission”这一情况，不开放裸榜单抓取，不允许同一窗口重抓，也不把公开榜伪装成逐提交历史。

## 6. 常见问题与唯一解决方案

| 现象或错误 | 含义 | 正确处理 | 严禁 |
|---|---|---|---|
| `OPERATOR_SANDBOX_NOT_READ_ONLY` | 父任务覆盖或未知权限使调用 agent 可能写工作区 | 在任何 MCP side effect 前停止；由主 agent/用户把父任务权限切为只读后重新派发 | 在可写环境中“靠自觉继续”、试写文件检测 |
| MCP 返回 filesystem permission denied | MCP 服务进程本身没有维护 durable state 的权限 | 停止并提交部署类 `MCP_CHANGE_REQUEST`；由主 agent 单独修复 MCP 服务权限 | 给调用 agent 工作区写权限、直接代写 queue/lock |
| `ZIP_NOT_FOUND` / `SUBMISSION_NOT_FILE` / `UNSUPPORTED_FILE_TYPE` | 候选路径不存在、不是文件或不是 ZIP | 回到产物生成步骤，找到原始候选并重新做 provenance；不得对未确认文件继续 | 猜路径、复制旧 ZIP 冒充新产物 |
| `ZIP_MUST_CONTAIN_ONLY_*` / `BAD_ROW_WIDTH` / `DUPLICATE_FILENAME` / `TEST_FILE_SET_MISMATCH` / `INVALID_CLASS_ID` | 提交包结构、样本集合或类别非法 | 在候选生成侧修复预测与打包，生成新的 SHA 和 manifest 后重新校验 | 在 MCP 中放宽校验、直接编辑已入队 ZIP |
| `PROVENANCE_MANIFEST_REQUIRED` / `PROVENANCE_CANDIDATE_PATH_MISMATCH` | 缺少 manifest 或 manifest 未绑定当前路径 | 从可信训练/推理证据重新生成 manifest，并先运行 provenance CLI | 手填路径蒙混过关、复用其他候选 manifest |
| `PROVENANCE_QUEUE_SHA256_MISMATCH` / `PROVENANCE_QUEUE_MODEL_ID_MISMATCH` | 已绑定身份与当前文件/模型证据漂移 | 停止；保留文件、manifest、queue item 证据并交主 agent 判断是否需要独立维护 | 覆盖队列 SHA、改 manifest 迎合队列 |
| `DUPLICATE_SUBMISSION_IDENTITY` | path、artifact SHA 或 logical ID 已在历史中 | 停止；选择真正的新预测候选和新 provenance | 改名、重压缩、删除 tombstone、恢复 override |
| `DUPLICATE_OVERRIDE_FORBIDDEN` | 调用方尝试旧 override | 删除 override 诉求并停止该候选 | 修改 schema 或 MCP 重新开放参数 |
| `PROVENANCE_*` / ZIP 校验失败 | 输入或证据不合规 | 返回远程训练/产物生成角色修复候选或 manifest，再从校验开始 | 弱化验证器、手改 SHA、伪造证据 |
| `ITEM_NOT_FOUND` / `ITEM_NOT_QUEUED` | 目标 index/name 已不存在或状态已变化 | 做一次 `queue_status`，报告最新状态；只有仍明确 queued 时才重新选择 | 伪造 index、把 terminal 项改回 queued |
| `RUNNER_INTENT_REQUIRED` | 未先取得精确 intent | 做一次 `queue_status`，重新判断状态 | 自己拼 intent |
| `STALE_RUNNER_CONFIRMATION` | queue/active/provenance 已变化 | 只刷新一次 `queue_status`；重新人工判断，不能自动循环 | 重放旧 intent、删除 revision 字段 |
| `CAPTURE_ONLY_CONFIRMATION_MUST_BE_FALSE` / `RUNNER_ACTION_UNSUPPORTED` | confirm 与 intent action 不匹配，或出现契约外 action | 修正为契约规定的参数；未知 action 直接上报版本/能力缺口 | 将 capture 以真实提交授权启动、猜测 action |
| `CDP_UNAVAILABLE_BEFORE_RUNNER_START` | 固定专用 CDP endpoint 在 runner 创建前不可达或响应无效；MCP 已保证没有创建 lease、attempt 或 runner | 使用项目记录的专用 Chrome 启动入口恢复已登录 CDP；随后只做一次新的 `queue_status` 并重新核对 exact intent | 绕过 readiness probe、直接运行 CDP/Node、把失败记成已提交或创建第二 identity |
| `RUNNER_ALREADY_RUNNING` / live lease | 已有唯一 runner | 绑定现有 runner watcher | 启动第二个 runner、杀进程抢锁 |
| `SUBMISSION_CONTROL_BUSY` | 另一个合规控制操作持锁 | 短暂退出并由 watcher/后续单次任务处理 | 高频重试、删除 control lock |
| `LOCK_DIRECTORY_CONTAINS_UNEXPECTED_FILE` | lock 路径形态异常，可能有人为篡改或旧实现残留 | 停止并记录目录元数据，提交维护请求 | 删除未知文件后继续、将目录强制改成文件 |
| stale lease / `RUNNER_LEASE_STALE_RECONCILIATION_REQUIRED` | 上次进程结束但结果可能未知 | 停止，收集 runner 日志、queue/active/attempt 证据，交主 agent reconcile | 删除 lease、把 uploading 改回 queued |
| `RUNNER_START_RECONCILIATION_REQUIRED` | runner 启动事务留下未闭环 receipt/lease | 停止并上报 launch receipt、PID、日志和 queue revision | 再启动一次“试试”、删除 launch receipt |
| `RUNNER_LEASE_*_MISMATCH` / `RUNNER_FENCE_REQUIRED` | 进程身份、action、index、revision、SHA 或 logical ID 不属于当前 lease | 让当前进程停止写入，保留 owner/lease 证据并交主 agent | 修改环境变量或 lease 让旧进程通过 |
| `blocked_identity_corruption` / `RUNNER_IDENTITY_BLOCKED` | durable 身份互相矛盾 | 提交结构化变更请求和证据；保持 fail-closed | 手改 queue/active/results 让它“看起来一致” |
| `MULTIPLE_BLOCKING_QUEUE_ITEMS` / `BLOCKING_QUEUE_ITEM_WITHOUT_ACTIVE_LOCK` | 同时存在多个可能有外部副作用的项，或 blocking item 无 active | 停止；导出 queue、active、events、receipt 证据供独立维护 | 任选一个继续、把其他项批量重置 |
| `ACTIVE_*_MISMATCH` / `ACTIVE_*_REQUIRED` / `ACTIVE_NOT_CAPTURE_READY` | active 与 queue 的 index、时间、logical ID、SHA 或状态不一致 | 原样报告 `identityIssues`，不启动 runner | 补字段、改状态、重建 active sidecar |
| `CAPTURE_*_MISMATCH` / `CAPTURE_CONFIRMATION_STALE` / `CAPTURE_WINDOW_IDENTITY_REQUIRED` | 抓分 intent 已过期或不再绑定当前 active/window | 重新做一次 `queue_status`；若仍不一致则变更请求 | 复用旧 acceptedAt、换窗口 ID 绕过 |
| `outcome_unknown` / `SUBMIT_OUTCOME_UNKNOWN_BLOCKING` | 点击后无法证明平台是否接受 | 查 per-submission 证据；证据不足则人工阻塞 | 自动重提、提交下一项 |
| `SUBMIT_NOT_DISPATCHED_RECONCILIATION_REQUIRED` / `submit_not_dispatched_reconcile_required` | bounded evidence 证明 submit-button click 从未派发，但同一 item 仍需显式恢复 | 仅使用最新 exact `reconciliationIntent` 调一次 `queue_reconcile_not_dispatched`；成功后重新 `queue_status` | 直接启动 runner、创建新 queue item、改名/重打包、把普通 failure 当 no-dispatch |
| `CONFIRM_NOT_DISPATCHED_RETRY_REQUIRED` / `RECONCILIATION_INTENT_REQUIRED` | 缺少明确同 identity 重试确认或 exact intent | 停止并取得用户授权/最新 status | 猜 intent、默认确认、把 reconciliation 当真实提交授权 |
| `STALE_RECONCILIATION_CONFIRMATION` / `NO_DISPATCH_EVIDENCE_CHANGED` | queue、runner、日志哈希或证据在确认后变化 | 停止并保留现场；至多重新做一次 `queue_status` 后人工判断 | 自动循环刷新、忽略日志变化继续 |
| `SUBMIT_ATTEMPT_BINDING_REQUIRED` / `RUNNER_LEASE_ATTEMPT_ID_MISMATCH` / `SUBMIT_LEASE_RUNNER_ID_MISMATCH` | submit runner、lease、CDP 与 queue item 的 attempt/runner 身份未形成完整闭包 | fail closed；保留 lease、runner record、queue attempt 与日志，交主 agent 独立维护 | 随机生成第二个 attempt、忽略字段、仅凭时间/文件名继续 |
| `SUBMIT_CANDIDATE_FILE_MISSING` / `SUBMIT_CANDIDATE_BYTES_CHANGED` | 入队后候选消失或字节已变化 | 永久阻止该绑定提交；保存路径/hash 证据，重新产出真正的新候选走全流程 | 恢复同一 identity、覆盖 queue SHA 后继续 |
| `LOGIN_REQUIRED` | Chrome 登录失效 | 保留状态，通知人工恢复登录；恢复后从一次 `queue_status` 开始 | 写浏览器绕过脚本、清 active |
| CDP timeout / renderer error | 浏览器健康未知 | 保留现场和日志，修复 Chrome/CDP 后重新评估；不推断未提交 | 自动重启提交、把 timeout 标为 failed 后前进 |
| `CDP_COMMAND_FAILED` / `CDP_OUTPUT_NOT_JSON` / `LEADERBOARD_COMMAND_FAILED` | 浏览器命令失败或证据无法解析 | 保存 stdout/stderr/raw evidence；若窗口已 claim 则不得重抓该窗口 | 丢弃 claim、把解析失败当成“无分” |
| `FENCE_LOST` | runner 已失去写权限 | 停止并保留新 owner；由主 agent检查 lease/日志 | 让旧 runner继续写、删除新 lease |
| `DIRECT_*_DISABLED` 或退出码 77 | 命中了故意隔离的旧入口 | 回到 MCP 工具流程 | 重新启用旧脚本或复制其代码 |
| `SCORE_CAPTURE_WINDOW_ALREADY_CLAIMED/USED` | 当前发布窗口已查询或已开始查询 | 不再查询该窗口；报告阻塞并等待明确后续决策 | 删除 claim、同小时换 runner 再抓 |
| `score_missed_blocked` | 单次窗口抓分没有可靠闭环 | 获取一次 per-submission 证据；或仅在 exact `publicLeaderboardRecoveryIntent` 存在时读取一次更晚公开榜窗口。只有 exact `scoreFinalizeIntent` 才可 finalize | 重抓原窗口、裸抓榜、自动 skip、自动提交下一项 |
| `PUBLIC_LEADERBOARD_URL_MISMATCH` | 证据来自队伍/作品/错误榜单页面或 URL 带额外参数 | 保留 evidence 并停止；恢复 canonical `/special/phb/detail` 页面后只能用新的、尚未消费的合法 intent | 把其他页面当公开榜、手改 evidence URL |
| `PUBLIC_LEADERBOARD_NOT_A_NEW_PUBLICATION_WINDOW` | 榜单发布时间等于或早于原 capture claim 的发布窗口 | 保留两个 claim，不归分 | 删除 claim、把同一小时当新窗口再抓 |
| `PUBLIC_LEADERBOARD_LATER_ACCEPTED_SUBMISSION_EXISTS` | r416 之后已有另一个 accepted submission，公开榜最新行不再能唯一归给 r416 | 停止公开榜恢复，改用强绑定逐提交证据 | 仅凭 teamSubmitTime/文件名猜归属 |
| `PUBLIC_LEADERBOARD_RECOVERY_ALREADY_CONSUMED` | 独立 late-publication recovery claim 已失败或不可重试 | 保留现场并停止；captured 状态只能幂等返回原 evidence | 再联网、换 claim key、删 claim 重试 |
| `TEAM_ROW_INCOMPLETE` / 时间不可解析 / scoreTime 早于 teamSubmitTime | 公开榜证据字段不完整或时间因果不成立 | 拒绝归属，保存 snapshot，转 per-submission 证据或人工阻塞 | 猜时间、沿用旧分 |
| public row 时间早于 acceptedAt | 当前看到的是上一发 | 标记 stale，等待合法未来窗口或 per-submission 证据 | 把旧分归给新候选 |
| public row 时间超出 active 合法窗口 | 榜单行无法安全绑定当前提交 | 拒绝归属并报告 `PUBLIC_LEADERBOARD_SUBMISSION_TIME_OUTSIDE_ACTIVE_WINDOW` | 仅凭队名或分数相近就归属 |
| `PER_SUBMISSION_SOURCE_UNAVAILABLE` | 工具没有取得逐提交来源 | 记录工具缺口并停止在证据边界 | 用 public row 假装逐提交记录 |
| `PER_SUBMISSION_FIELDS_INSUFFICIENT` | 页面存在但缺少可匹配字段，或只能按文件名/basename 对上 | 原样报告字段缺口并提交 capability request；要求 queue/platform identity、精确 artifact hash 或兼容 accepted/submitted time 中至少一个强字段 | 误报 `NO_MATCHING_SUBMISSION_RECORD`、用 filename-only 归分、skip 或重提 |
| `scoreAttributionReady=false` | 可能已匹配提交 identity，但同一条唯一记录尚无完整 score/scoreTime，或匹配记录不唯一 | 保留 `scoreAttributionReason`，不得声明得分；等待准确发布窗口或报告歧义 | 从另一条记录借 score、模糊文件名匹配、把 `matched=true` 当作已打分 |
| `scoreFinalizeReady=false` / `PER_SUBMISSION_IDENTITY_NOT_STRONG_ENOUGH` | 记录可诊断匹配但尚不足以写入 canonical ledger | 保留 evidence；要求 queue/hash/platform identity，或 exact team + stable record ID + filename + 60 秒内时间闭环 | 把 15 分钟 time-only 或 filename-only 当成可 finalize |
| `STALE_SCORE_FINALIZE_CONFIRMATION` / evidence、claim、active、queue、results revision 变化 | fetch 后 durable 事实发生变化 | 零写入停止；重新人工判断，不能自动循环 | 忽略 SHA/CAS、直接手改 queue/results |
| `NO_MATCHING_SUBMISSION_RECORD` | 来源字段充分但确实无匹配 | 保留查询证据，人工判断平台延迟/异常 | 当作提交失败并自动重提 |
| `RESULT_IDENTITY_CONFLICT` / `RESULT_IDENTITY_MISMATCH` / results schema 异常 | 分数 ledger 与 durable identity 冲突或格式损坏 | 停止写分，保存原文件 hash 和冲突行，交独立维护/迁移 | 删除冲突行、手填分数、降级 schema |
| `NO_ACTIVE_SUBMISSION_LOCK` / `ACTIVE_NOT_SKIP_READY` / `SKIP_SELECTOR_IDENTITY_MISMATCH` | 人工 skip 请求不属于唯一 awaiting/score_missed active | 拒绝 skip，返回最新状态和 selector 证据 | 扩大 selector、直接改状态推进队列 |
| watcher `RUNNER_NOT_FOUND` | 当前 MCP 内存没有该 runner | 查 persisted runner、PID 和日志一次；若已退出则汇总证据 | 因 watcher 丢失而启动新 runner |
| MCP 断连、进程重启或 JSON-RPC 超时 | 工具调用结果未知，尤其 runner start 可能已经生效 | 重启/恢复后只做一次 `queue_status`，核对 lease、runner、active 和 receipt；不自动重放调用 | 把网络错误当作未执行、重复调用 side-effect 工具 |
| 意外的 automation/旧任务在调用隔离入口 | 仍有历史控制器或过期任务存活 | 记录来源、命令、退出码 77，交主 agent 单独停用该 automation | 临时放开入口让旧任务完成 |
| contractVersion 缺失/不匹配 | MCP 进程或调用文档版本过旧 | 重启 MCP，复核一次版本 | 继续提交或修改版本常量绕过 |
| 未列出的错误码、未知 state 或必需字段缺失 | 当前契约不能证明继续安全 | fail closed；保留完整工具返回并提交 `MCP_CHANGE_REQUEST` | 根据名称猜测可重试、现场修改代码 |

## 7. MCP 变更请求格式

调用 agent 只有在当前契约无法安全完成任务时才返回：

```yaml
MCP_CHANGE_REQUEST:
  contract_version: aicomp-submit-agent-contract/v4
  tool: <tool-name>
  operation: <submit | capture | evidence | queue-mutation>
  observed_state: <queue_status compact summary>
  error_code: <exact code/reason>
  expected_behavior: <what capability was needed>
  actual_behavior: <what happened>
  safety_boundary: <why continuing would be unsafe>
  evidence:
    - <queue index, log, snapshot, manifest, test or report path>
  reproduction: <bounded temp-root reproduction, or "not reproduced">
  requested_capability: <behavioral requirement; do not include an unreviewed patch>
  external_actions_taken: []
```

返回变更请求后结束执行。不得在同一提交/抓分任务里切换身份成为维护者。

## 8. 维护者修复流程

仅主 agent 在用户授权的独立维护任务中执行：

1. 确认无 live runner、active side effect 或旧 automation。
2. 备份并只读审计 queue、active、lease、events/results。
3. 先写 temp-root/fake-CDP 回归测试，禁止真实提交。
4. 实现最小修复，不扩大执行 agent 权限。
5. 由 `deepseek-code-review` 做只读安全审查。
6. 运行 MCP 定向测试和两个仓库完整测试。
7. 需要数据迁移时使用 CAS、控制锁和完整备份。
8. 重启 MCP 进程，检查 `contractVersion`，最后只做一次 `queue_status`。

执行 agent 不得参与第 3–7 步的代码修改；它只能提供证据、复现条件和文件更新建议。维护工作必须使用另一个明确的主 agent 任务，不能临时提高 `aic-submit-watch` 的 sandbox 权限。
