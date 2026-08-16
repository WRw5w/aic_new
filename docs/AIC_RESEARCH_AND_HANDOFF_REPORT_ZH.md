# 2026 AIC 噪声标签细粒度识别赛：比赛判断与团队交接报告

> 研究时间：2026-08-16 | 所属领域：鲁棒微调、细粒度图像分类、竞赛工程 | 研究对象类型：算法挑战赛与在研项目

## 一、一句话定义

这不是一场「把初赛 checkpoint 发给队友就算交接」的比赛，而是一条会在复赛、半决赛连续更换数据规模和分布、最终要求代码复现与线下答辩的鲁棒微调长链路；真正有价值的资产，是一套能随阶段迁移的方法、证据和安全打榜系统。

## 二、先给判断：应该怎么传

建议采用「私有 Git 仓库 + 私有 Release 大文件 + SHA-256 清单」的两层交接。

私有 Git 仓库保存代码、配置、实验结论、自动打榜控制面、复现说明、测试和 provenance；750 MB 左右的 checkpoint 不进入普通 Git 历史，单独作为私有 Release 资产上传。现有公开仓库 `WRw5w/lihao` 不再继续承担竞赛交接，因为公开性与赛事数据、提交产物和仍在演进的策略并不相容。

交付模型也不能只写「最高 78.9122」。本地证据里应同时保留三种口径：

| 口径 | 候选 | 分数 | 是否适合承诺复现 |
|---|---|---:|---|
| 历史最高合法单次 | `b512_aligned` | 78.9122 | 否：本地没有找到一一绑定 checkpoint |
| 当前最佳交付参考 | `b448_aligned formal v2` | 78.8561 | 是：模型、日志、预测、provenance 完整 |
| 可靠重训基准 | `champion_real` | 78.5477 | 是：用来估计 run variance |

这三个数字并不矛盾。78.9122 说明路线的上限；78.8561 是能交到队友手上的实物；78.5477 提醒团队，单次峰值不是稳定复现分。

自动打榜机也应该传，但不能把浏览器 profile、cookie 和旧队列一起打包。应该传的是受控状态机源码、调用契约、测试、helper 的固定哈希和机器配置清单。队友在自己的机器上重新登录 AICOMP，先跑 fake-only 和只读检查，再由队长授权第一发真实提交。

## 三、纵向分析：这场比赛如何把问题一步步推向「可迁移系统」

### 3.1 赛题的起点不是模型，而是约束

官方在 2026 年 4 月 30 日发布赛题。任务看起来像一个常规的 500 类自然动植物细粒度分类：训练集来自互联网、标签有噪声，测试集由人工精确标注，最终看 Top-1 Accuracy。但约束一加上去，问题的形状立刻变了。

骨干只能使用 OpenAI 官方 CLIP ViT-B/32；不能换更大视觉模型，不能调用闭源视觉 API，不能引入额外数据，测试集不能参与训练。允许的是 Prompt Tuning、Adapter、LoRA、鲁棒损失、噪声过滤、伪标签和表征约束。最终方案必须是单模型或单一推理流程，不能做多模型融合或投票。进入半决赛后还要提交完整训练、验证、推理脚本、环境说明、可执行模型或容器、技术方案 PDF；总决赛代码若无法复现，成绩可能被取消。完整约束见 [AIC 官方赛题页](https://www.aicomp.cn/tracks/tracks-1/3714.html)。

这组约束决定了一个关键事实：模型容量不是可以无限扩张的轴，项目必须在「冻结的有限骨干」「错误监督」「不同阶段的数据变化」之间找平衡。比赛不是在问谁能找到更大的 backbone，而是在问谁能把一个固定 backbone 驯服得更稳。

CLIP 本身来自 2021 年的自然语言监督预训练。原论文用 4 亿图文对训练可迁移视觉表示，并展示了在多个细粒度分类数据集上的零样本迁移能力；这解释了为什么赛事方选它作为共同起点，也解释了为什么灾难性遗忘会成为核心风险。[CLIP 论文](https://arxiv.org/abs/2103.00020)

LoRA 则提供了另一块拼图：冻结预训练权重，在 Transformer 层中注入低秩更新矩阵，用更少的可训练参数完成任务适配。[LoRA 论文](https://arxiv.org/abs/2106.09685) 在这道题里，LoRA 不只是省显存，它还天然形成了一道「别让脏标签把预训练表示改坏」的结构性护栏。

### 3.2 四阶段赛制改变了优化目标

官方规则把线上部分分为初赛、复赛、半决赛，再进入线下总决赛：

| 阶段 | 类别数 | 训练样本 | 测试样本 | 分布特点 | 最终权重 |
|---|---:|---:|---:|---|---:|
| 初赛 | 500 | 103,218 | 24,967 | 标签噪声 | 不计线上综合分 |
| 复赛 | 1,500 | 297,282 | 74,896 | 标签噪声 + 长尾 | 40% |
| 半决赛 | 1,000 | 180,274 | 49,857 | 标签噪声 + 长尾 | 60% |
| 总决赛 | 后续通知 | 后续通知 | 客观复核 | 线下答辩 + 复现 | 以细则为准 |

初赛只是熟悉任务和晋级，不进入最终线上综合分；复赛与半决赛才共同决定线上综合成绩。赛道通知给出的时间是：2026 年 9 月中旬前开始复赛，10 月 10 日前开始半决赛，11 月中下旬计划举行全国总决赛，具体日期仍以赛道公告为准。[算法挑战赛道通知](https://www.aicomp.cn/tracks/tracks-1/3629.html)

这个安排直接推翻了「把初赛最优模型交出去即可」的直觉。初赛的类别均衡校正、去噪比例、最佳分辨率和 SWA 窗口，最多是有价值的先验。到了复赛和半决赛，类别数、样本量、长尾程度和具体图片全部变化，旧阶段数据还明确禁止混入新阶段训练。队友真正需要的是重新构建缓存、重新估计噪声、重新做 seed 控制和重新绑定证据的能力。

### 3.3 项目第一阶段：从能跑到 76 分平台

项目早期路线围绕冻结 CLIP ViT-B/32 + LoRA 展开。attn-only LoRA 很快遇到容量上限，于是扩到 attention + MLP，配合 EMA、RandAugment 和更高分辨率。448 像素让 ViT-B/32 从 7×7 的 49 个 patch token 增长到 14×14 的 196 个 token，对细粒度辨别帮助明显。

随后，项目建立了 kNN 一致性去噪、教师头共识伪标签、连续样本权重和严格 90/10 分层验证协议。重要的不只是这些组件本身，而是顺序：先划分训练/验证，再在训练分区内做所有标签驱动统计，验证样本只查询训练图库。这避免了一个常见的竞赛错觉——本地指标因为标签统计穿透验证集而变得漂亮，实际上并不能复现。

这一阶段把线上成绩推到约 76.1，但也暴露了第一个大陷阱：`mid_03_06` 和 `noisy_all` 是在脏标签上测的，本地越高不一定线上越好。长训练会逐渐记住脏验证标签，本地数字继续上涨，干净榜分却下降。此后团队形成了「本地指标负责筛错，线上榜分负责定方向」的纪律。

### 3.4 第二阶段：cleanlab、mixup 和同轨迹 SWA

真正突破 76.1 平台的是 cleanlab 式的标签质量估计。Confident Learning 不是简单地按模型最大概率删样本，它试图估计 noisy label 与潜在真实标签的联合分布，用类别相关阈值识别标签错误。[Confident Learning 论文](https://arxiv.org/abs/1911.00068) 项目中的 cleanlab 方案把单模型推到约 76.61，叠加 mixup 0.2 后，`clmix` 达到 77.13。

再往上不是继续堆复杂结构，而是沿同一次训练轨迹做权重平均。`clmixsoup5` 把 epoch 3 到 12 的十个 checkpoint 做 SWA，达到 78.6318。窗口实验呈现出很清楚的弧线：7 点 78.26，8 点 78.42，9 点 78.59，10 点 78.63；再加入更早的 warmup epoch，11 点和 12 点反而回落。

这里最值得交给队友的不是「十点最好」，而是背后的边界：相邻 epoch 位于同一收敛盆地，权重平均能降方差；跨 seed、跨配方的模型不再 mode-connected，平均后可能灾难性下降。项目里 `cl2soup` 只有 73.96，`cl4soup` 甚至掉到 70.65。SWA 是单模型权重空间中的一条窄桥，不是多模型结果随便倒进一口锅。

WiSE-FT 的研究也提醒了同一个方向：在预训练模型微调中，权重空间插值可以兼顾目标域精度与分布外鲁棒性。[WiSE-FT 论文](https://arxiv.org/abs/2109.01903) 不过本赛题禁止多模型集成，实际使用时必须把最终提交保持为单一模型、单一推理流程，并保留足以说明其合规性的构建过程。

### 3.5 第三阶段：推理杠杆和打榜系统的成熟

项目线上最大「免费增益」来自两个推理杠杆。

第一个是多尺度 TTA：448、512、576 加水平翻转，约带来 1.1 分。它没有训练新模型，只让同一个模型从不同缩放视角观察测试图。

第二个是类别均衡 bias 校正。初赛官方测试集类别均衡，项目把预测类别分布拉回均匀，稳定带来约 2.9 分。`clmixsoup3` 的 balanced 版本是 78.59，无均衡 `_tta` 只有 75.46。历史上曾经把一批无均衡版本重复入队，连续浪费约 30 发；这个事故让「文件名后缀也是实验变量」变成了工程铁律。

另一个更严重的事故来自平台语义。AIC 公开榜展示的是团队最近一次已发布提交，不保存每个候选的完整历史最高。同一小时连发会让后面的低价值候选覆盖前面的展示项。旧系统又曾把平台 accepted 当作完整 done，导致高价值发没有抓到分就继续推进。

自动打榜机后来被重构为带 active lock 的状态机：queued、uploading、accepted、awaiting_score、scored、score_missed、failed、paused、skipped 各有明确含义；accepted 不再等于完成；一个小时只允许一个 effective submission；抓分证据必须绑定 queue index、accepted time、logical submission ID 和 candidate SHA-256。每个榜单发布窗口还使用一次性持久 claim，防止崩溃后重复抓同一窗口。

这一演进很有代表性。项目早期把自动化当节省点击的脚本，后来才发现它其实是一个金融交易式的幂等系统：每个外部动作都要有身份、锁、证据和恢复语义。自动化真正的价值不是更快，而是减少不可逆错误。

### 3.6 第四阶段：位置编码重采样把上限推到 78.91

在训练配方逐渐见顶后，项目回到一个很容易被忽略的细节：CLIP ViT-B/32 原生输入是 224，位置编码网格是 7×7。输入升到 448 或 512 时，位置编码也要被插值。

默认 timm 路线使用 bicubic 与 antialias。项目新增 `aligned` 路线，采用 bilinear + `align_corners=True`。最初的假设是 416 对应 13×13，原 7×7 锚点可以精确落在新网格的偶数节点上，因此奇数网格应更强。实验后来修正了这个故事：416 的纯增益只有 +0.036，448 是 +0.120，512 则是 +0.3765；真正稳定的现象不是奇偶网格，而是分辨率越高，插值方法越重要。

结果矩阵里，512 aligned 达到 78.9122，480 aligned 的 seed 123 达到 78.9082；但同一 480 配方 seed 42 只有 78.7439，相差 0.1643。这个 seed 对照杀死了「偶数网格天然更强」的漂亮叙事，也把最终判断改成更稳健的一句：448 到 576 是带噪的平台区，aligned 让高分辨率退化更慢，实际运营点应落在 448–512，而不是迷信某个像素值。

这是整个项目最有价值的研究习惯之一：先允许自己有机制假设，再用对照把故事推翻。结论经得住反例，比一次 78.91 更重要。

### 3.7 第五阶段：后续创新大多证明「复杂不等于更强」

位置编码之后，项目又尝试了保守 SSL 回收、双池化门控、动态划分、posterior soft divide、DivideMix、ELR、TrustCLIP 风格梯度投影、FET、DoRA、SCE、APL、OT、relabel、curriculum 和多种特征融合。

结果非常一致：保守 SSL 回收只有 78.5437，训练时间却大幅增加；双池化门控降到 78.2433；DivideMix 约 74.53；大多数头部结构和鲁棒损失没有超过简单 cleanlab + mixup + 同轨迹 SWA。

DivideMix 在论文里把样本损失分布建模成混合模型，把数据动态分为干净与噪声子集，再用两个网络互相提供划分并进行半监督学习。[DivideMix 论文](https://arxiv.org/abs/2002.07394) 它在标准 noisy-label benchmark 上很强，但赛事约束是单一模型流程，且本项目的细粒度 hard-but-correct 样本很多。激进划分会把真正困难的正确样本当噪声丢掉，论文机制与具体数据分布之间出现了错位。

JoAPR 则主张联合自适应阈值分区和标签修复，专门提升视觉语言模型 prompt learning 对标签噪声的鲁棒性。[JoAPR 论文](https://openaccess.thecvf.com/content/CVPR2024/papers/Guo_JoAPR_Cleaning_the_Lens_of_Prompt_Learning_for_Vision-Language_Models_CVPR_2024_paper.pdf) TrustCLIP 的题目进一步提出语义标签验证与 trust-aligned gradient projection，已被 ACM Multimedia 2025 接收。[ACM MM 2025 接收列表](https://acmmm2025.org/accepted-regular-papers/) 这些方法适合继续读，但项目现有结果已经给出约束：凡是需要额外模型、类语义文本、跨网络协同或激进分区的机制，都必须先检查赛事允许性和本数据的 hard-sample 结构，不能只因为论文新就烧算力。

## 四、横向分析：四种交接方式，谁适合这个项目

### 4.1 直接把整个目录压缩发过去

看起来最省事，实际上最差。当前工作目录混合了源码、数据缓存、数十 GB checkpoint、远程结果包、浏览器/提交日志和大量未提交改动。Git 对象库本身已有约 4.54 GiB 松散对象，直接压缩会把历史噪声、个人绝对路径和潜在凭据一起带走。队友收到后也不知道哪个文件是权威版本。

它唯一适合的场景是紧急灾备，而且必须在加密磁盘或可信局域网里传，不能当协作方式。

### 4.2 继续使用现有公开 GitHub 仓库

它的优点是已有 117 个提交，队友可以立即 clone；缺点是仓库公开，且 README 仍停留在早期 448 配方，和后来的 aligned、控制面、安全状态机已经分叉。更严重的是，比赛规则明确赛事数据仅限本次比赛使用，公开仓很容易让人无意提交数据、预测或状态证据。

GitHub 普通仓库会阻止超过 100 MiB 的文件，并建议仓库理想上小于 1 GB、强烈建议小于 5 GB。[GitHub 大文件说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github) 750 MB checkpoint 无法进入普通 Git。继续沿用这个公开远端，会同时遇到合规、体积和版本可信度三个问题。

### 4.3 新建私有仓库，使用 Git LFS

Git LFS 把大文件内容放到独立对象存储，Git 历史里只保留指针。GitHub Free/Pro 的 LFS 单文件上限为 2 GB，750 MB checkpoint 在技术上可行。[Git LFS 说明](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage)

优点是模型版本和代码 commit 可以一一对应，队友 clone 后能按指针下载；缺点是存储与带宽会计费或受配额影响，多个 750 MB checkpoint 很快膨胀。没有安装 Git LFS 的协作者只能得到指针文件，也容易误以为模型已经下载。

如果团队要持续比较多个阶段模型，LFS 适合只保留「每阶段一个基准 + 一个候选」。不要把每个 epoch 都放进去。

### 4.4 私有仓库 + GitHub Release 资产

Release 适合把一个确定的 checkpoint 当交付版本发布。GitHub 允许每个 Release 资产小于 2 GiB，单个 Release 最多 1000 个资产，并声明 Release 总大小和带宽没有总限制。[GitHub Releases 说明](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)

它比 LFS 更适合当前的一次性交接：代码仓保持轻量，`v0-initial-handoff` Release 里放 `full.pt`、预测 ZIP、资产清单和报告 PDF。队友看到 tag 就知道代码与模型的对应关系。缺点是模型更新不像 Git LFS 那样透明，需要手动下载或脚本拉取。

对当前团队，我更推荐 Release。到了复赛，如果每天都产生大模型版本，再评估是否迁移到 LFS 或对象存储。

### 4.5 最终选择

| 方案 | 版本可信度 | 大文件 | 合规风险 | 接手成本 | 结论 |
|---|---:|---:|---:|---:|---|
| 整目录压缩 | 低 | 能 | 高 | 高 | 只做灾备 |
| 现公开仓 | 中 | 不能 | 很高 | 低 | 停止扩展 |
| 私有仓 + LFS | 高 | 能 | 低 | 中 | 适合长期多版本 |
| 私有仓 + Release | 高 | 能 | 低 | 低 | 本次交接首选 |

私有个人仓的协作者默认拥有读写权限，若团队需要更细粒度的只读/写入角色，应使用 GitHub Organization。[GitHub 私有仓权限说明](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/permission-levels-for-a-personal-account-repository)

## 五、交给队友的「精华」到底是什么

### 5.1 第一层：决策压缩

队友最需要的是一页决策图，而不是 151 条提交记录。

有效主线：CLIP ViT-B/32 冻结骨干 → attn+MLP LoRA → cleanlab → mixup 0.2 → EMA/RandAugment → aligned 位置编码 → 448–512 → 同轨迹 SWA → 多尺度 TTA → 按官方测试先验做谨慎校正。

已经证伪：跨 seed soup、跨配方 soup、激进 DivideMix、ELR 大权重、FET 复杂头、DoRA、SCE/APL、relabel、OT、curriculum、双池化门控。它们不是永远无效，而是在初赛数据和现有实现下没有赢过基线；复赛重启时最多保留为二级候选，不能抢占基准复现时间。

### 5.2 第二层：一个能跑的参考模型

`b448_aligned formal v2` 是交付锚点，因为它有完整证据。它未必是历史单点最高，但比一个找不到 checkpoint 的 78.9122 更有工程价值。

队友拿到后先做三件事：校验 checkpoint SHA-256；用固定提交 ZIP 验证格式；在隔离 work-dir 做一次推理复核。任何一步不一致都停止，不要通过重新下载未知同名文件来猜。

### 5.3 第三层：实验账本，而不是日志墓地

完整 `submissions/aicomp_results.csv` 包含合法结果、重复提交、无均衡变体和违规探针。直接发过去会让新接手者把 92.0014 当冠军。交接包里的 `SCORECARD.csv` 只保留代表性节点，并为每条加上 `legal / invalid_probe / offline_unsubmitted` 状态。

原始账本仍应在私有归档中保留，因为它是证据；日常决策只看压缩后的 scorecard。

### 5.4 第四层：自动打榜的安全机制

真正要传的是这些机制：

- provenance 先于入队；
- exact intent 绑定候选与队列 revision；
- active lock 阻止同小时覆盖；
- submit-once 与 capture-only 分开；
- 抓分证据绑定 accepted time 与 candidate hash；
- 一次性窗口 claim 防止重复抓榜；
- task watcher 负责长时间等待，不在聊天里高频轮询；
- 未知状态 fail closed，不自动重提。

浏览器 cookies、个人 Chrome profile 和现有队列 JSON 反而不应该传。

### 5.5 第五层：负结果的边界

负结果不是废物。它们告诉队友在哪里别重复交学费，但必须写清条件。例如：「DivideMix 失败」的完整表述应是：初赛数据、当前实现、冻结 ViT-B/32、已有 cleanlab 先验下，动态划分把约 44% 训练分区降为 noisy/drop，线上约 74.53，显著低于 78.6 基线；不是说 DivideMix 论文在所有 noisy-label 任务上无效。

这种条件化表达也会直接改善总决赛答辩。评委问为什么不用更复杂方法时，团队可以拿出对照和机制解释，而不是说「试过，没用」。

## 六、接手后的前 72 小时

### 第 0–4 小时：建立信任链

队友创建私有仓库、clone 核心包、校验 `MANIFEST.sha256.csv`，再单独下载 `full.pt` 并校验 `8a349c...d0c73a`。运行 compileall、无 GPU 单测和提交 ZIP 校验。此阶段不训练、不提交。

### 第 4–12 小时：复现推理

用同阶段测试数据和固定 checkpoint 跑 448/512/576 + flip TTA，确认输出行数、文件名集合、预测分布和固定提交包一致。若完全一致，说明环境、模型加载、位置编码和后处理链路可信。

### 第 12–24 小时：复现训练烟测

在小样本或 `--smoke` 模式下验证 cleanlab、LoRA 注入、EMA、mixup、checkpoint 保存和 provenance。正式训练前先检查数据阶段标识，防止初赛缓存穿到新阶段。

### 第 24–48 小时：部署只读打榜

启动专用 Chrome，让队友自行登录。控制面只做 `queue_status`、候选 ZIP 校验和只读 leaderboard 页面检查，不执行真实提交。确认 contract v4、queue schema 3、helper hash、Node 路径和 CDP endpoint。

### 第 48–72 小时：第一发受控提交

由队长明确指定候选，生成 provenance，入队，读取 exact intent，再授权一次真实提交。随后只挂 watcher。抓到强绑定分数证据后复盘一次，确认队友真正理解 accepted 与 scored 的区别。

## 七、复赛与半决赛策略：哪些初赛结论可带走

### 可以直接带走

- 骨干与合规边界。
- 先划分再做标签统计的验证纪律。
- cleanlab + mixup + EMA 的基准价值。
- 同轨迹 SWA 的构建原则。
- aligned 位置编码作为高分辨率对照。
- provenance、hash、队列锁和一次性抓分 claim。

### 必须重新验证

- 448 与 512 的最优点。
- keep ratio 和 cleanlab 阈值。
- 训练轮数与 SWA 起点。
- balanced 校正。初赛测试集官方明确均衡，复赛/半决赛虽然评测规则仍是 Accuracy，但长尾描述主要指训练数据；不能自动假定测试分布和初赛完全相同。
- TTA 尺度。测试图原始分辨率可能变化。
- 本地验证代理。类别更多、长尾更强后，旧 `mid_03_06` 分带未必仍有筛选价值。

### 应新增的对照

1. 至少两个 seed 的 448/512 aligned 基线。
2. 训练期 class-aware sampler、logit adjustment 与普通采样对照。
3. 每类噪声率与样本数的二维分析，避免把尾部类当噪声整体删掉。
4. 不同 SWA 窗口的权重距离或线性插值损失，先证明 mode connectivity 再平均。
5. 按类别频次分桶的 holdout 指标，替代单一 noisy accuracy。

## 八、横纵交汇：历史如何塑造了今天的竞争位置

项目今天最强的地方不是某个单模块，而是「约束感」。早期 30 发无均衡误投和同小时覆盖事故，让团队建立了外部动作的身份与锁；本地指标多次误导，让团队学会把 proxy 只当 proxy；位置编码奇偶假设被 seed 对照推翻，让团队不再过度解释单次榜分；违规 89/92 探针则把表示瓶颈定位在 ViT-B/32，同时明确划出正式路线边界。

这些历史共同塑造了当前的生态位：算法层面不追求最复杂，而追求最少但互补的机制；工程层面不追求最快提交，而追求一次提交的完整闭环；交接层面不追求文件最多，而追求每个结论都能找到模型、配置、hash 和榜分证据。

竞争对手如果只有一个高分 ZIP，到了复赛换数据就要重新摸索；如果只有论文方法，没有打榜控制面，也会在平台窗口和提交证据上丢分。本项目真正可以形成优势的是把研究循环压缩成标准动作：构建可比较候选，自动生成来源，安全提交，准确归分，把负结果写回策略库。

但今天的劣势也来自历史。主仓和控制仓都处于混合 dirty worktree；公开 README 仍停留在旧配方；最强 78.9122 的 checkpoint 没有在本地闭环；控制面存在硬编码路径、队伍 ID 和 helper hash。过去为了快速迭代保留的本地假设，现在成了队友接手的摩擦。

所以交接不是附加工作，而是下一阶段竞争力本身。把系统整理成私有、轻量、可验证的两仓或单体包，等于提前完成半决赛要求的一半。

## 九、三个未来剧本

### 最可能剧本：基准迁移成功，分数由数据适配决定

队友用 1–2 天完成环境和控制面复现。复赛新数据上，cleanlab + mixup + aligned 448/512 仍是强基准，但最佳阈值和 SWA 窗口变化。团队通过小矩阵快速找到 2–4 个候选，线上综合分主要取决于长尾处理和 seed 稳定性。

这个剧本下，交接包最大的价值是节省「重新相信系统」的时间。

### 最危险剧本：把初赛 78.91 当固定配方，过拟合旧阶段经验

队友直接套 512 aligned、均衡 bias 和旧 cleanlab 阈值，没有重建分布诊断；同时把公开仓里的旧 README 当权威。复赛长尾类被错误过滤，测试分布又与初赛先验不同，线上掉分。为了追分，团队恢复旧 watchdog 或连续提交，覆盖高价值窗口。

这个剧本的预警信号很清楚：新阶段开始后没有 seed 对照、没有 per-frequency 指标、没有新 provenance scope，却很快出现大量提交。

### 最乐观剧本：交接系统反过来提升研究速度

私有仓把代码、配置、候选和证据全部结构化；checkpoint 通过 Release 与 tag 绑定；队友专注运行与分析，你专注 A 赛或新的研究方向。自动打榜只处理经过门禁的候选，每个结果自动落到 scorecard。到了半决赛，技术报告、实验消融和可复现环境已经随开发同步形成，决赛不需要临时补材料。

这个剧本不是靠再加一个模型实现的，而是靠消除协作中的信息损耗。

## 十、最终行动清单

1. 新建私有仓库，不在当前公开 `lihao` 继续推竞赛交接内容。
2. 运行 `build_handoff.ps1`，得到白名单核心包与 SHA-256 manifest。
3. 以 `v0-initial-handoff` 为 tag 建私有 Release。
4. 上传 `b448_aligned formal v2 full.pt`、固定预测 ZIP、报告 PDF 和 `ARTIFACTS.csv`。
5. 邀请队友，要求先跑 `verify_handoff.ps1`。
6. 队友完成推理复核后，再部署自动打榜只读状态。
7. 第一发真实提交必须由你明确授权，并完整走 provenance → queue → exact intent → watcher → scored。
8. 复赛开始时新建 competition scope 和队列，不复用初赛 active/claims。
9. 把历史最高 78.9122 标为「合法历史峰值」，把 78.8561 标为「交付参考模型」，不混写。
10. 违规 89/92 探针只留在受限诊断归档，报告、PPT 和正式模型中全部排除。

## 十一、信息来源

- [AIC 官方赛题：面向噪声标签数据的细粒度图像识别鲁棒微调](https://www.aicomp.cn/tracks/tracks-1/3714.html)，访问时间 2026-08-16。
- [第八届 AIC 算法挑战赛道通知](https://www.aicomp.cn/tracks/tracks-1/3629.html)，访问时间 2026-08-16。
- [CLIP: Learning Transferable Visual Models From Natural Language Supervision](https://arxiv.org/abs/2103.00020)。
- [LoRA: Low-Rank Adaptation of Large Language Models](https://arxiv.org/abs/2106.09685)。
- [Confident Learning: Estimating Uncertainty in Dataset Labels](https://arxiv.org/abs/1911.00068)。
- [DivideMix: Learning with Noisy Labels as Semi-supervised Learning](https://arxiv.org/abs/2002.07394)。
- [Robust Fine-tuning of Zero-shot Models](https://arxiv.org/abs/2109.01903)。
- [JoAPR: Cleaning the Lens of Prompt Learning for Vision-Language Models](https://openaccess.thecvf.com/content/CVPR2024/papers/Guo_JoAPR_Cleaning_the_Lens_of_Prompt_Learning_for_Vision-Language_Models_CVPR_2024_paper.pdf)。
- [ACM Multimedia 2025 accepted regular papers](https://acmmm2025.org/accepted-regular-papers/)。
- [GitHub：About large files on GitHub](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github)。
- [GitHub：About Git Large File Storage](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-git-large-file-storage)。
- [GitHub：About releases](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases)。
- [GitHub：Permission levels for a personal account repository](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/repository-access-and-collaboration/permission-levels-for-a-personal-account-repository)。
- 本地项目证据：`submissions/aicomp_results.csv`、`submissions/aicomp_state.md`、`docs/打榜实验日志.md`、`docs/CLAUDE_CODE_MEMORY_HANDOFF.md`、`remote_posembed_exp/RESULTS.md`、`runs/aic_b448_*`，核对时间 2026-08-16。

## 十二、方法论说明

本报告采用横纵分析法：纵轴追踪赛制、技术路线和工程系统从 2026 年 4 月至今的演进；横轴比较研究方法、交接渠道和大文件协作方案；最后在两条轴的交点上给出可执行的团队交接与后续比赛策略。

