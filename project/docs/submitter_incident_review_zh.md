# 打榜提交器问题复盘与重构记忆

记录时间：2026-06-20  
目的：给后续重构 `aicomp` 打榜提交器留一份可靠记忆，避免再次把高价值提交覆盖、漏抓分、误判状态。

## 结论先写

这轮提交器的核心问题不是单点 bug，而是状态机设计不清晰：

- `done` 被混用为“平台 accepted”和“分数已抓到”，导致很多条看似完成，实际没有分。
- 同一小时连续提交多发，平台榜单只显示该小时该队伍最后/较新的提交，早先高价值提交被覆盖，无法从公开榜抓分。
- 队列优先级和实际提交顺序脱钩，脚本拿 `pending` 时按数组顺序，不按 `priority`。
- 心跳默认 2 分钟，长期等待时会产生大量无意义调用和日志。
- 页面/榜单抓取依赖单页文本，队伍掉到第 2 页或页面卡死时会误判抓不到。
- 缺少“单发独占窗口”的硬约束，导致高价值 `balanced soup` 被低价值旧 `_tta` 覆盖。

## 关键事故线

### 1. 09 点快模式覆盖事故

当时为了清队列，使用过类似“accepted 后直接标 done 继续下一发”的快路径。结果同一小时内连续提交：

- `09:07:54` accepted：`pred_results_ortho_cb05_tta_balanced.zip`
- `09:10:49` accepted：`pred_results_ortho_clmixsoup_tta_balanced.zip`
- `09:14:11` accepted：`pred_results_ortho_clmixsoup2_tta_balanced.zip`
- `09:19:14` accepted：`pred_results_5sig_ep8_tta.zip`
- `09:23:34` accepted：`pred_results_5sig_tta.zip`

10:00 榜只抓到了最后窗口里的 `5sig_tta`：

- `pred_results_5sig_tta.zip`
- 分数：`71.3382`
- 榜单发布时间：`06月20日 10时00分`

这说明平台公开榜不是每次提交都有历史分，而是每个队伍在榜单窗口中只保留一个展示项。前面的 `cb05 / clmixsoup / clmixsoup2` 虽然 accepted，但分数被后续提交覆盖，公开榜无处可抓。

### 2. 高价值 soup 重新单发验证

后面改成“一小时只提交一发，等榜单刷新抓分后再继续”，重新验证了三发：

- `pred_results_ortho_clmixsoup2_tta_balanced.zip`
  - accepted：`2026-06-20 13:22:57`
  - 榜单：`06月20日 14时00分`
  - 分数：`78.2633`
  - 这是当前已确认最高分。

- `pred_results_ortho_clmixsoup_tta_balanced.zip`
  - accepted：`2026-06-20 14:06:46`
  - 榜单：`06月20日 15时00分`
  - 分数：`77.2740`

- `pred_results_ortho_cleanlabsoup_tta_balanced.zip`
  - accepted：`2026-06-20 15:06:49`
  - 榜单：`06月20日 16时00分`
  - 分数：`77.1699`

### 3. cb05 当前状态

`cb05_balanced` 旧提交已确认没有可回填分数，因为被 09 点后续提交覆盖。已单独重投：

- `pred_results_ortho_cb05_tta_balanced.zip`
- 点击提交：`2026-06-20 17:06:51`
- accepted：`2026-06-20 17:06:53`
- 当前状态：`awaiting_refresh`
- 预期抓分窗口：`18:00/18:05` 左右

## 具体问题清单

### 状态字段语义混乱

当前队列里 `done` 不可靠。它可能表示：

- 真的已提交并抓到分。
- 平台 accepted 了，但还没抓到分。
- accepted 后为了继续清队列被强行标 done。
- 曾经抓分失败后继续队列。

重构要求：

- `accepted` 和 `scored` 必须分成两个状态。
- 推荐状态机：
  - `queued`
  - `uploading`
  - `accepted`
  - `awaiting_score`
  - `scored`
  - `score_missed`
  - `failed`
  - `paused`
  - `skipped`
- `done` 这个词最好废掉，或者只允许表示最终终态 `scored/skipped/failed` 的聚合展示，不能写回主状态。

### 缺少一小时窗口锁

最大事故来自同一小时提交多发。提交器必须有窗口锁：

- 如果存在任何 `accepted/awaiting_score`，禁止提交下一发。
- 下一发只能在当前发完成 `scored` 或明确人工 `skip_score` 后开始。
- 即使用户要求“清空队列”，也不能破坏窗口锁。

推荐做法：

- 新增 `active_submission.json` 或队列内唯一 active 锁。
- 锁内容至少包含：
  - artifact id / file name
  - accepted time
  - expected leaderboard publish time
  - score capture attempts
  - source queue index

### 优先级排序没有贯彻到执行

队列里有 `priority` 字段，但 runner 曾经用数组顺序找第一个 `pending`：

```js
queue.find((candidate) => candidate.status === "pending")
```

这会导致手工重排之前，低价值旧 `_tta` 抢跑。

重构要求：

- 执行前必须按 `(priority desc, createdAt asc)` 选下一发。
- 队列数组顺序只能是展示顺序，不能是执行语义。
- 每次启动 runner 都打印“下一发选择理由”。

### balanced 与普通 tta 没有策略隔离

本轮事故里，多次出现高价值 `_tta_balanced` 被旧 `_tta` 干扰。尤其是 soup 系列：

- `clmixsoup2_tta_balanced` 是核心高价值。
- 对应未均衡 `clmixsoup2_tta` 不是同等优先级，不能自动补交覆盖窗口。

重构要求：

- 引入策略分组和互斥关系：
  - `family = clmixsoup2`
  - `variant = balanced | raw`
  - `tier = critical | normal | waste`
- 同一 family 的低 tier 不能排在高 tier 前。
- 已提交高 tier 后，低 tier 默认 `paused_duplicate_family`，除非人工显式解锁。

### 抓榜逻辑不可靠

曾遇到的问题：

- 队伍不在第 1 页，旧抓取只看第 1 页，误判没有分。
- 排行榜页 CDP 会卡死，需要关闭旧 tab 重新打开。
- 16:00 榜单曾短暂出现“暂无数据”，后续刷新才有数据。

已经做过的修复：

- `tools/aicomp_cdp.mjs` 的 `leaderboardSnapshot()` 增加了尝试第 2 页逻辑。

重构要求：

- 抓榜要有明确的 retry/backoff。
- 遇到“暂无数据”不能立刻判失败。
- 每次 snapshot 必须保存原始文本、解析结果、发布时间。
- 如果本队不在第 1 页，必须自动翻页或扩大 page size。
- 如果 CDP 连接失败，关闭 leaderboard tab 并重建。

### 心跳频率不合理

默认 `AICOMP_HEARTBEAT_INTERVAL_MS=120000`，即 2 分钟。打榜每小时最多一次提交，这个频率太高。

重构要求：

- 默认心跳改为 30 分钟。
- 等榜期间只需要：
  - 启动时确认一次页面健康。
  - 中途 30 分钟一次心跳。
  - 到刷新点主动抓榜。
- 心跳失败只应报警/重连，不应自动提交下一发。

### 日志太散，缺少一眼可读状态

目前证据散在：

- `submissions/aicomp_submit_queue.json`
- `submissions/aicomp_results.csv`
- `submissions/aicomp_submission_log.csv`
- `submissions/aicomp_leaderboard_snapshots.log`
- 多个 runner/watchdog stdout/stderr

重构要求：

- 增加一个单文件状态面板，例如 `submissions/aicomp_state.md`。
- 每次状态变化更新：
  - 当前 active 发
  - 下一个待交
  - 最近一次 accepted
  - 最近一次 scored
  - 当前最高分
  - 下次动作时间
  - 是否有窗口锁

## 事故中用到的关键事实

已确认有效分：

| 文件 | 提交时间 | 榜单 | 分数 |
|---|---:|---:|---:|
| `pred_results_ortho_clmixsoup2_tta_balanced.zip` | 2026-06-20 13:22:57 | 06月20日 14时00分 | 78.2633 |
| `pred_results_ortho_clmixsoup_tta_balanced.zip` | 2026-06-20 14:06:46 | 06月20日 15时00分 | 77.2740 |
| `pred_results_ortho_cleanlabsoup_tta_balanced.zip` | 2026-06-20 15:06:49 | 06月20日 16时00分 | 77.1699 |
| `pred_results_soup_v3_tta.zip` | 2026-06-20 12:06:46 | 06月20日 13时00分 | 73.8014 |
| `pred_results_5sig_tta.zip` | 2026-06-20 09:23:34 | 06月20日 10时00分 | 71.3382 |

当前待确认：

| 文件 | accepted | 状态 |
|---|---:|---|
| `pred_results_ortho_cb05_tta_balanced.zip` | 2026-06-20 17:06:53 | 等 18 点榜 |

## 下一版提交器建议设计

### 主流程

1. 载入队列。
2. 如果存在 active accepted item：
   - 只做抓分。
   - 禁止提交下一发。
3. 如果没有 active：
   - 从队列中按优先级选择一个 `queued`。
   - 上传并提交。
   - accepted 后写入 active 锁。
   - 状态设为 `awaiting_score`。
4. 到榜单发布时间：
   - 抓榜。
   - 若 `team_submit_time` 与 active accepted 时间匹配，写入 score，状态设为 `scored`。
   - 若榜单未刷新或暂无数据，延迟重试。
   - 若榜单显示另一个提交时间，视为窗口被污染，立刻报警。

### 必须禁止的行为

- 禁止 accepted 后直接继续下一发。
- 禁止 `done` 无分。
- 禁止用队列数组顺序代替 priority。
- 禁止在有 `awaiting_score` 时自动 refill。
- 禁止默认提交旧 `_tta` 覆盖 critical `_balanced`。
- 禁止在抓榜失败时静默继续队列。

### 推荐文件结构

```text
submissions/
  aicomp_queue.json
  aicomp_active_submission.json
  aicomp_scores.csv
  aicomp_state.md
  aicomp_events.jsonl
  leaderboard_snapshots/
    20260620_1405_clmixsoup2.json
```

### 推荐事件格式

```json
{
  "time": "2026-06-20T09:06:53.017Z",
  "event": "submit.accepted",
  "queue_index": 64,
  "file": "pred_results_ortho_cb05_tta_balanced.zip",
  "accepted_at": "2026-06-20T09:06:53.017Z",
  "expected_publish": "2026-06-20T10:00:00Z"
}
```

## 操作原则

- 高价值模型宁可慢一小时，也不要被同小时覆盖。
- `accepted` 不是成功闭环，`scored` 才是。
- 所有自动化都要能回答三个问题：
  - 当前正在等哪一发？
  - 下一次动作是什么时间？
  - 如果现在继续，会不会污染榜单窗口？

这次真正救回来的关键信息是：`clmixsoup2_balanced` 确认 78.2633。重构提交器时，第一目标不是跑得快，而是保证这种高价值发不会再被自己覆盖。
