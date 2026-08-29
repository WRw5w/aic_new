---
name: aicomp-submitter-runbook
description: How the aicomp submit automation works and how to supervise/unblock it each hour
metadata: 
  node_type: memory
  type: reference
  originSessionId: 79057f48-ac89-42b9-89e1-83b7460a0fe1
---

打榜提交自动化（`tools/aicomp_*.mjs`），支撑 [[aicomp-leaderboard-marathon]]。三层：

- **runner** `node tools/aicomp_submit_queue.mjs run` — 长驻循环。有 active 锁就等到 `capture_start_at`(整点+5min) 抓分；抓到→选下一发(priority desc) 提交；抓不到→`score_missed` 阻塞退出。
- **watchdog** `node tools/aicomp_queue_watchdog.mjs` — 每 60s，queue 还有活但 runner 不在就拉起。
- **hourly-alarm** `node tools/aicomp_hourly_acceptance_alarm.mjs run` — 每小时 backlog 导入新 zip + 审计 + 确保 runner/watchdog 在 + score_missed 打 ACTION_REQUIRED。

关键文件：`submissions/aicomp_state.md`(一眼状态)、`aicomp_active_submission.json`(窗口锁)、`aicomp_events.jsonl`(事件)、`aicomp_submit_queue.json`(队列)、`aicomp_hourly_acceptance_alarm.log`、`aicomp_results.csv`(已抓分)。时间戳是 UTC，本地=UTC+8。

每小时巡检清单：
1. `cat submissions/aicomp_state.md` 看 counts/active/next。
2. 进程：`Get-CimInstance Win32_Process -Filter "Name='node.exe'"` 确认 runner+watchdog 在；不在等 alarm 拉，或手动起。
3. 心跳健康：`tail aicomp_events.jsonl` 找最近 `heartbeat.ok`/`leaderboard.snapshot_fresh`；若 `heartbeat.failed` 反复→Chrome/CDP 登录态可能掉，需人工看浏览器（`tools/aicomp_start_chrome.ps1`）。
4. `score_missed` 阻塞：低价值发可 `node tools/aicomp_submit_queue.mjs skip-score <idx>` 放行；高价值发先查为何抓不到（可能掉到第2页/出榜延迟），别盲目 skip。
5. 进度对比上一小时 queued 是否在降；若 awaiting_score 卡了 >1 整点没抓到分→查抓榜。

绝不能做：accepted 后同小时再交、用数组顺序代替 priority、抓榜失败静默继续。

已知反常（2026-06-21 00:43 首次遇到）：
- **公开排行榜显示每队"最近一发"的分，不是历史最高**。我们峰值 clmixsoup3=78.5917 被后续低价值发顶下榜，但历史分仍在平台「作品打分结果」里。所以"交完所有"会拉低公开榜分——这是用户已知并接受的策略（赌最终按历史最高算）。
- **平台整点榜可能后半夜停发**：23:00 之后 00:00 窗迟迟不发(>40min)，致 runner 4 次抓分全 stale → score_missed 阻塞、runner 退出、watchdog 不重启。**手动 `node tools/aicomp_cdp.mjs leaderboard` 看 发布时间 字段确认是平台没发(全队都停在旧窗)还是我们 CDP 卡缓存**。
- 平台停发时的处置：**别 skip、别硬交**(会一发发全 score_missed 纯浪费)，按兵不动每小时盯。score_missed 时 runner 退出、watchdog 也按设计退出(queueNeedsRunner=false)，队列全 halt 等人工。
- **恢复手法(2026-06-21 01:22 实测成功，优于 skip 不丢分)**：等 发布时间 推进到新窗、且手动抓榜确认我队那行显示的是被卡那一发的 submit_time(平台榜显示每队最近一发)→ 把 `aicomp_active_submission.json` 的 `"status":"score_missed"` 手改回 `"awaiting_score"`、`score_capture_attempts` 归 0 → 跑 `node tools/aicomp_hourly_acceptance_alarm.mjs once`(它会 requeue 该项并把 runner+watchdog 都拉起；ensureActiveLock 会从锁同步 awaiting_score 到队列项，无需手改大队列文件) → runner 启动即从新窗抓到真分(capture_start 已过立即抓)、记 scored、自动续交下一发。
- 仅当新窗榜单始终不显示该发的分(真丢)时才 `node tools/aicomp_submit_queue.mjs skip-score <idx>` 放行(waste 档可)。CDP 抓榜有内部 retry，偶尔中途读到旧页属正常，会自行重试到 fresh。

- **【CDP 卡死烧队列】(2026-06-22 01:16 首次遇到，恢复停发后紧接着触发，最凶险)**：症状=runner 提交连续报 `CDP timeout: Runtime.enable` → `submit.failed` → runner 退出 → watchdog 60s 拉起 → 提交下一发又同样失败，**每 ~90s 烧掉一个队列项**(status 变 `failed`，是 TERMINAL 不会自动重试)，外加 1 个卡在 `uploading`。根因=**手动 `aicomp_cdp.mjs leaderboard` 抓榜留下的残留 Chrome 标签页(排行榜 tab、有时还有 cas/login 重定向 tab)堆积，害 runner attach 时 Runtime.enable 超时**。Chrome 进程其实活着、`http://127.0.0.1:9222/json/version` 也通，但提交页 renderer 被残留 tab 拖垮。
  - 恢复(实测成功不丢分)：① 立刻 `Stop-Process` 杀掉 runner+watchdog 止血。② `node tools/aicomp_cdp.mjs pages` 看标签页——**正常稳态是 2 个：submit 页(`/app/JSGLPT/639980…`) + leaderboard 页(`/special/phb/detail`)，这两个都是 runner 自己提交/抓分要用的别关**；真正的残留是**多出的 cas/login 重定向 tab、或重复的排行榜 tab**(手动抓榜留下的)。③ 逐个 `fetch http://127.0.0.1:9222/json/close/<id>` 关掉残留 tab(保留 1 submit + 1 leaderboard)。④ `node tools/aicomp_cdp.mjs heartbeat` 必须返回 `ok:true reason:ready`(exit 0)；若 `reason:login`(exit 2)=登录态真掉了，需用户重登。⑤ 改 `aicomp_submit_queue.json`：把 `failed`/`uploading` 的项 status 改回 `queued`、清空 submittedAt/exitCode/submitStartedAt/acceptedAt(无内置 requeue 命令；runner 停了可直接写文件，格式 `JSON.stringify(doc,null,2)+"\n"`，顶层是 `{updatedAt,refreshDelayMinutes,queue:[],schemaVersion}`)。⑥ `node tools/aicomp_hourly_acceptance_alarm.mjs once` 拉起 runner+watchdog。⑦ **务必盯到下一发 `submit.accepted`(不是 failed)再走人**(accept 约提交后 2~2.5min 落事件)。
  - 预防：手动抓榜会残留 tab，巡检完顺手 `pages` 看一眼、`json/close` 清掉多余 tab；别让排行榜 tab 累积。
  - **同症状的另一触发(2026-06-22 21:3x 实遇)**：`pages` 显示标签页是干净的(就正常 1 submit+1 leaderboard、无残留)但 `Runtime.enable` 照样超时、连 `heartbeat` 都失败——这是 **renderer 本身卡死**,不是残留 tab。诱因=Chrome 跑了很久(那次 5.5h)+ 同机有 **chrome-devtools-mcp(`npx chrome-devtools-mcp@latest --autoConnect`)/codex** 抢内存/CDP 资源(呼应历史 RAM 压力)。**修法=整个重启 Chrome**:`tools/aicomp_start_chrome.ps1 -Restart`,等 9222 通后 `heartbeat` 应 `ok:ready`(可能 attempts=2,首次列表空它会自动 reset/query 重试),再按上面 ⑤⑥⑦ 重置 failed→queued、起 workers、盯 accepted。那次 wedge 30s 一个连烧 3 发(8/9/10),重启 Chrome 后第一发即 accepted、0 丢分。**所以恢复决策树:先 `pages`——有残留 tab 就 close;没残留但仍超时就 `-Restart` 整个 Chrome。**

- **【永久丢弃队列项要用 status=`dropped`,不能用 `skipped`】(2026-06-22)**：要把低价值发从队列里**永久踢掉**时,别设 `skipped`——`tools/aicomp_enqueue_backlog.mjs` 的 `shouldRequeue()` 把 `["paused","paused_low_value","paused_duplicate_family","skipped","skipped_duplicate","score_missed","capture_missed","failed"]` 全当"待重投",**每小时 alarm 跑 backlog 时会把它们 status 改回 `queued` 复活**(亲历:skip 了 11 个非均衡废档,alarm once 一跑就 requeued=11)。正确做法:设一个**两个消费者白名单都没有**的自定义状态 `dropped`——runner 的 RUNNABLE=`{queued,uploading,accepted,awaiting_score}` 不含它(不会发)、backlog 的 requeue 列表不含它(不会复活)。**且条目要留在队列里别删**:backlog 的"加新 zip"按文件名查 `existing`(含所有状态的队列项),只要 `dropped` 条目还在、名字就在 existing 里,submissions/ 里的 zip 就不会被当新文件重新加回。改完务必**单跑一次 `node tools/aicomp_enqueue_backlog.mjs` 验幂等**(应 added=0 requeued=0)再重启 workers。
- **next_queue 投递流程**：`submissions/next_queue/` 是新候选投放区(放 `*.zip`+同名 `*.csv`)。**runner 跑着时别改大队列**——先停 runner+watchdog → `node tools/aicomp_import_next_queue.mjs`(把 zip 复制进 submissions/、按 `priorityForName` 入队 status=queued、再把文件移出 next_queue;已在队列里的按名去重跳过) → 需要的话手改队列调 priority/补发 → `alarm once` 重启。注:已 scored 的同名(如冠军 clmixsoup3_balanced)会被去重跳过,要重投得手动 push 一个新的 queued 条目(复用其 path)。priority 越大越先发,想让某发"榜上最近显示"就给它**最小**的非废档优先级(最后发)。
