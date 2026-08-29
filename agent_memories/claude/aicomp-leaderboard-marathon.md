---
name: aicomp-leaderboard-marathon
description: Ongoing aicomp 打榜 submission marathon — Claude is the hourly supervisor of the self-healing submit automation
metadata: 
  node_type: memory
  type: project
  originSessionId: 79057f48-ac89-42b9-89e1-83b7460a0fe1
---

接管自 2026-06-20：把 `submissions/` 里剩余的预测 zip 全部交到 aicomp 榜单（team swpu_1 / AIC-2026-58579595），并每小时巡检一遍。用户原话：「将剩下的所有的未提交的项目都提交上去，每隔一个小时你检查一遍」。

铁律：**一小时只能交一发**。平台公开榜每个队伍在每个整点窗口只保留一个展示项，同小时多发会覆盖掉早先的高价值提交且无处抓分（见 [[aicomp-submitter-runbook]] 的事故复盘 docs/打榜提交器问题复盘与重构记忆.md）。`accepted` 不是闭环，`scored` 才是。

自动化已在运行（三层自愈，见 [[aicomp-submitter-runbook]]）：runner 长驻「提交→等整点出榜→抓分→交下一发」；watchdog 60s 复活 runner；hourly-alarm 每小时审计+backlog导入+确保进程在。进程死亡能自愈，我巡检只为处理它**无法自愈**的：`score_missed` 阻塞、Chrome/CDP 登录态失效、卡进度、窗口污染。

进度基线（2026-06-20 19:54 本地）：scored=70 queued=47 awaiting_score=1。最高分 clmixsoup2_tta_balanced=78.2633。

**2026-06-25/26 posembed 分辨率实验打榜（5候选，控制变量=分辨率/插值，同配方含cleanlab+mixup+SWA ep4-12）**：结论——**分辨率是倒U，峰值=448，不是单调**。LB：b448_default=**78.7359 🏆全场最高** > r608_aligned=78.6438 > r416_aligned=78.1071 > r416_default=78.0711 >> b224_native=73.2847。曲线 224→416→448(顶)→608回落(−0.09)。**关键洞见**：608 同时占"更高分辨率+奇数19×19无损aligned插值"两个理论优势却仍输 448 → 真正瓶颈是**位置编码外推距离**(CLIP原生7×7;448=2.0×,608=2.7×),不是插值是否无损;拉到2.7×把空间先验拉坏,盖过高分辨率增益。aligned 在416只+0.036,救不回离峰值的损失。注:b448_default 超旧冠军 clmixsoup5(78.6318)仅+0.10,同配方族属噪声带非免费午餐。**工作点定 448**。详见 repo `remote_posembed_exp/RESULTS.md` + 分支 posembed/416-experiment（见 [[github-code-management]]）。

**2026-06-26 aligned 全分辨率扫描(12点)→ 新冠军 b512_aligned=78.9122**(超本项目原best clmixsoup5 78.6318 **+0.28**,纯靠 512+aligned,配方没动)。**最终结论(经3轮自我修正)**:①**头条且稳健**:aligned(align_corners)优于 timm default,且**增益随分辨率暴涨**——416 +0.036 / 448 +0.120 / **512 +0.377**。机理=位置编码要从原生7×7外推,推得越远(分辨率越高)插值方法越关键,timm退化快、aligned退化慢。②**aligned 把最优分辨率右移**:default(timm)峰在448(78.74)、到512已回落(78.54);aligned 到512还在涨(78.91>448的78.86)。③分辨率=陡升到~448后是**噪声高原(448–608≈78.6–78.9)**,高原内差异被噪声淹没,"峰恰在512"不可靠。④**单跑种子噪声~±0.15**:b480 种子42=78.7439 vs 种子123=78.9082,同分辨率仅换种子就摆 0.16。⑤**"偶高奇低"奇偶规律是噪声不是定律**:曾出现 even{448,512,576}全高/odd{480,544,608}全低的干净3:3分层(还反着打脸最初"奇数锚点对齐更好"的假设),但 b480 reseed 一换种子就从低位跳高位,两个假说全是过度解读噪声。⑥工作点:**aligned@448–512**(512最高78.9122,448最省bs32near-equal),别超512。坑:`--pos-resample`合法值只有 timm/aligned(没有default,会报错);TTA须HF offline;打榜tab冻结/平台午夜发布延迟致score_missed→读榜手动补抓+`skip-score <idx>`清锁。详见 repo `remote_posembed_exp/RESULTS.md`。git/RESULTS/memory 已全部回填。运维坑:TTA步骤要强制 HF offline(HF_HUB_OFFLINE=1),否则重载backbone撞HF网络抖动崩;打榜Chrome tab闲置~10h会冻结致CDP Runtime.enable超时,关掉重建tab即解(见 [[aicomp-submitter-runbook]])。

**2026-06-30 打分时间铁律(用户明确强调,要牢记):打分是【整点】出结果,不是提交后几分钟。** 19:16 提交 → 必须等 **20:00 整点**那一档才打分上榜。排行榜页头"(发布时间:06月30日 19时00分)"= 当前是 19:00 那一档的快照,只反映 ≤19:00 已打分的提交。**提交后不要几分钟就去查分(会误判"没收进去"),要等过了下一个整点再拉榜核对。** 我这次就踩了坑:19:16 交完 11:24(UTC)去查还是旧分,差点误判。

**2026-06-30 重要观察(待 20:00 确认):公开榜每队只显示【最近一次已打分的提交】,不是历史最佳。** 证据:19:00 档 swpu_1 显示 = 74.5264 @ 2026-06-28(=我们 DivideMix 那次失败),而我们真正的冠军 78.9122(b512_aligned,~06-26)**没显示**——说明后来更差的 74.53 把 78.91 从展示位顶掉了。**含义(若确认):绝不能提交 < 当前最佳的候选,否则展示分被拉低、排名掉。** 复现 zip(~78.91)已于 19:16 交,预计 20:00 把 swpu_1 恢复到 ~78.91、排名第4→第2。20:00 后核对(后台 bymlx4xox)→ 若 swpu_1=~78.91 则同时坐实"复现成功"+"latest-wins 榜"。与第12行"每个整点窗口只保留一个展示项"一致。

**2026-06-22 收官**：原始候选库全交完（scored=111）。用户中途改策略——把队列里剩的低价值**非均衡**废档全 `dropped`（非 `skipped`，否则每小时 backlog 复活，见 [[aicomp-submitter-runbook]]），改投 `next_queue` 里的新均衡候选 clmixsoup5/6/7。冲分线结果钉死 **SWA 长轨迹的甜点位 = 10 点**：clmixsoup5(ep03–12,10点)=**78.6318 🏆新冠军**(超原 78.5917 +0.04) > clmixsoup6(ep02–12,11点)=78.4916 > clmixsoup7(ep01–12,12点)=78.3034——再往前加 ep01/02 极早 warmup 单调掉分。另:clmixsoup3 重投精确复现 78.5917 = **测试集自始至终没变的铁证**（曾误判"换测试集",实为均衡 vs 非均衡差 ~3 分,见 docs/打榜实验日志.md 自我纠错）。队列抽干后 runner 自然 `runner.finished` 停。**冲分线已收官,自巡检 loop 已停**;若有新候选投 `next_queue` 再按其流程续打。约需 47 小时跑完。
