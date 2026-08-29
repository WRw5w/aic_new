---
name: github-code-management
description: 代码用 GitHub(WRw5w/lihao) 管理；xgpu 容器只能 HTTPS 推送(SSH被封)，PAT已配好
metadata: 
  node_type: memory
  type: project
  originSessionId: 40d1946f-2d42-4c1e-afb9-9737c1408a17
---

从 2026-06-25 起，代码用 GitHub 仓库 **WRw5w/lihao**（公开仓库）管理；本地 origin 已指向它。

**关键约束：xgpu 训练容器只能通过 HTTPS 访问 GitHub**——SSH(22) 与 ssh-over-443 都被网络封死（github.com:22 超时；ssh.github.com:443 被透明代理拦到内网 IP 192.168.11.10）。所以容器侧只能用 **HTTPS + 细粒度 PAT**，不能用 SSH key。HTTPS 到 github.com/api.github.com 实测 ~0.5–0.7s，很快。

容器已配置好：git 身份(Zhang Chengxi)、`credential.helper=store`、PAT 存于 `/root/.git-credentials`(chmod 600)。代码克隆在快盘 **/root/jinyinsai-code**（另有更早的克隆 /root/lihao）。push 权限已验证 `push:true`。PAT 是细粒度、限 lihao、Contents:RW、可过期可吊销。

数据/权重不进 git（.gitignore 已排除 `*.pt`、`train/`/`test/`、`outputs_*`）；数据集与 checkpoint 走云盘 `/root/cloud/jinyinsai`。

**代码分叉的处理（2026-06-25 定）**：按用户"分开建、保留旧的、增量开发"的意思，把远程 posembed 实验那套（含 `--pos-resample aligned` 新功能，与根目录 finetune_lora.py 差~1403行）**作为独立目录 `remote_posembed_exp/` 整体提交**，不动根目录旧版（纯增量、零冲突）。已推到分支 **`posembed/416-experiment`**（代码29文件 + 4个arm的TTA-balanced提交文件8个）。md5 校验过本地代码=远程实际跑的代码。

**2026-06-28 已推送对齐**：`main`(→69fa104) 与 `posembed/416-experiment`(→55497cf，含 DivideMix `--dynamic-divide` 两个提交) 都已 push 到 origin，本地=GitHub 无 ahead。⚠️ 但**远程容器实跑代码不在 git 内**：实跑目录是 `/root/remote_posembed_exp/code`(独立目录，非 git 仓库)，靠文件拷贝部署、`git pull` 不更新它；容器里的 `/root/jinyinsai-code`、`/root/lihao` 是该 repo 克隆但**不是实跑代码**(陈旧 main)。见 [[reach-82-divmix-campaign]]。`posembed/416-experiment` 是否合并进 main 仍由用户定。

操作容器(SSH不通走Jupyter API)见 [[remote-server-ops-jupyter-api]]；存储布局见 repo `server_ops/REMOTE_STORAGE_NOTES.md`；打榜见 [[aicomp-leaderboard-marathon]]。posembed 4候选正在打榜(aligned已交,等抓分)。
