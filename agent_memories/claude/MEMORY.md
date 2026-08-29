# Memory Index

- [aicomp 打榜 marathon](aicomp-leaderboard-marathon.md) — 接管中：每小时把关，把剩余预测 zip 一小时一发交到榜单
- [aicomp 提交器 runbook](aicomp-submitter-runbook.md) — 三层自愈自动化怎么跑、每小时巡检清单、怎么解阻塞
- [每小时自定闹钟+核对执行](hourly-self-alarm-verify-execution.md) — 用户准则：每小时用 agent 自己的 ScheduleWakeup 闹钟，醒来核对"真收进去(accepted+scored)"，每 cycle 重定闹钟
- [GitHub 代码管理](github-code-management.md) — 代码用 WRw5w/lihao 管；容器只能 HTTPS 推(SSH被封)，PAT已配在 /root/.git-credentials
- [远程服务器操作法](remote-server-ops-jupyter-api.md) — xgpu容器SSH不通,走 Jupyter kernel API 下shell + /files下载,常驻进程要 setsid -f
- [仙宫云账号 API](xiangongyun-account-api.md) — 用 key 自己查状态/开关机容器(仅限我们的 vrfgp6th8uu2nz1j),不再等用户手动 Start;GPU 4h 空闲会自动关机
- [冲82分 DivideMix 战役](reach-82-divmix-campaign.md) — 78.55冠军#2;PE-Core-G14诊断=89.36→瓶颈是B/32弱不是数据脏,#1大概率非开挂而是换了模型
