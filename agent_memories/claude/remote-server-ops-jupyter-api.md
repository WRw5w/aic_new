---
name: remote-server-ops-jupyter-api
description: "怎么操作 xgpu 训练容器——SSH被封,走 Jupyter kernel API 下 shell + /files 下载"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 40d1946f-2d42-4c1e-afb9-9737c1408a17
---

xgpu GPU 训练容器（`vrfgp6th8uu2nz1j.container.x-gpu.com`，root，RTX 4090D）的操作方式。

**SSH 用不了**：22 端口 kex 直接被对端关（容器没暴露 sshd）；ssh-over-443 也被透明代理拦。所以**操作容器只能走 Jupyter HTTP+WebSocket kernel API**（就是浏览器那条 `https://<host>-8888.container.x-gpu.com/...?token=<REDACTED>` 的地址）。GitHub 同样只能 HTTPS，见 [[github-code-management]]。

**跑 shell 命令**：`POST /api/kernels?token=` 建 kernel → 连 `wss://<host>/api/kernels/<id>/channels?token=` 发 `execute_request`（python `subprocess.run(cmd, shell=True, capture_output=True)`）→ 收 iopub `stream` 取 stdout/stderr → 完事 `DELETE /api/kernels/<id>`。Node v24 自带全局 `WebSocket`（不用装 ws）。会话里写过 `jupyter_exec.mjs`（在 scratchpad，临时；方法记住即可重建）。
- 一次性命令随便跑；**常驻进程（心跳/训练）必须 `setsid -f`** 脱离 kernel——kernel 一关，平台会把它的子进程全清掉（用户踩过这个坑）。

**下载文件到本地**：`GET {base}/files/<相对/的路径>?token=`（Jupyter `--notebook-dir=/`，所以 `/root/x` 写成 `files/root/x`）。二进制 OK，已用它把提交 zip 拉到本地。

**token**：在用户给的 JupyterLab 链接的 `?token=` 里（容器重建会变，别硬记值）。环境里 `NODE_TLS_REJECT_UNAUTHORIZED=0` 是平台设的（全局关了证书校验）。

**存储三层**（详见 repo `server_ops/REMOTE_STORAGE_NOTES.md`）：`/`=110G zfs 快盘(你的可写区) / `/root/cloud`=1T rclone-webdav 共享云盘(持久,跨项目共用) / `/.xgcos/.links/*`=平台只读 bind-mount 镜像层(**不占你配额、EBUSY 删不动**，别去清)。
