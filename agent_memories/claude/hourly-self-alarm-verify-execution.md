---
name: hourly-self-alarm-verify-execution
description: "打榜巡检必须每小时用 agent 自己的闹钟(ScheduleWakeup)并核对\"真正被执行\"，而非进程体检"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 79057f48-ac89-42b9-89e1-83b7460a0fe1
---

做 [[aicomp-leaderboard-marathon]] 巡检时：**每过一小时，给自己定一个 agent 自己的闹钟（ScheduleWakeup / 动态 loop），每次醒来核对上一发「有没有真正被执行」——即平台 accepted 且抓到分（scored 计数+1、aicomp_results.csv 有新行），并确认下一发已 submit.accepted。每个 cycle 醒来后立刻重新定下一次闹钟。**

**Why:** 用户两次强调「我要的是你这个 agent 的闹钟」「每小时定一个自己的闹钟，检查有没有真正被执行」。他不接受用外部 cron 作业或只靠常驻 node 进程代劳，也不接受只查进程存活就报平安。原则上 `accepted` 不算闭环，`scored` 才算（见 [[aicomp-submitter-runbook]]）。

**How to apply:** 用 ScheduleWakeup（delaySeconds≈3420~3600，卡在整点+10min，即 runner :05 抓分之后），prompt 用无 interval 的 `/loop …` 以保持动态自唤醒模式（不要用带 `1h` 前缀的 prompt，否则会走 CronCreate）。每次醒来：tail aicomp_events.jsonl 看 score.captured/submit.accepted，cat aicomp_state.md 看 counts，确认登录态(heartbeat.ok)，汇报「上一发X抓到分Y、下一发Z已交、scored/queued」，然后再 ScheduleWakeup 定下一次。绝不省掉重新定闹钟这一步，否则 loop 断。
