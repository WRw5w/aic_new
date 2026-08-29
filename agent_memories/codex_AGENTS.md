请不要一直轮询任务!!!!使用task_watcher来监控,不要一直轮询吃光我的token了

创建 heartbeat/automation 用来盯榜、远程训练、长时间任务监控时,如果用户没有明确指定心跳间隔,默认使用 30 分钟一次,例如 rrule = "FREQ=MINUTELY;INTERVAL=30"。10 分钟一次太频繁,除非用户明确要求。

创建一次性闹钟或提醒时,先把“几分钟后”换算成 Asia/Shanghai 的目标本地时刻；由于接口只有分钟级墙钟精度,目标时刻必须向上取整到下一整分钟,确保提醒不早于用户要求的间隔。再使用一次性的绝对墙钟规则 `FREQ=DAILY;COUNT=1;BYHOUR=<小时>;BYMINUTE=<分钟>`。不要使用 `FREQ=SECONDLY;INTERVAL=<秒>;COUNT=1`,也不要用 `COUNT=2` 绕过“没有未来运行”的校验；后者会在每次触发后重新锚定并继续运行。一次性提醒完成后应删除对应 automation,避免残留活动心跳。
