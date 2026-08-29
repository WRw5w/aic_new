---
name: xiangongyun-account-api
description: "仙宫云(x-gpu)账号 API:可自己查状态/开关机容器,不用再等用户按 Start"
metadata: 
  node_type: memory
  type: reference
  originSessionId: 17971dba-69cb-4186-95b8-2563d664b8f9
---

仙宫云(xiangongyun / x-gpu)**账号级 REST API** —— 用户 2026-07-01 给的,让我能**自己给 GPU 容器开机/关机/查状态**,根治"4h 空闲自动关机后只能等用户手动 Start"的痛点。配合 [[remote-server-ops-jupyter-api]] 用。

**凭据(secret,用户自有 key)**:`<REDACTED>`
控制台/取 key 页:https://www.xiangongyun.com/a/API ;交互式 playground:https://api-playground.xiangongyun.com/instance/0 ;文档:https://docs.xiangongyun.com/

**Base**:`https://api.xiangongyun.com` **认证**:HTTP header `Authorization: Bearer <key>`(www.xiangongyun.com/api 那条是网页会话,不是这个;用 api. 子域)。

**已验证可用**:
- `GET /open/instances` → 200 `{"code":200,"data":{"list":[...]}}`。每个实例含 `id,name,status,gpu_used,jupyter_url,jupyter_token,ssh_domain/port/user,password,storage_mount_path,auto_shutdown` 等。`status` 值见过 `running` / `shutdown`。**这就是自监控开机与否的手段。**

**我们自己的实例**:`id=vrfgp6th8uu2nz1j`,`name="zhangchengxin"`(和 git 用户 Zhang Chengxi 对应),RTX 4090 D 48G。它的 jupyter host/token 是**基于 instance-id、跨重启稳定**的(`https://vrfgp6th8uu2nz1j-8888.container.x-gpu.com`,token `h4n8oe5yon4sfndz2djbfq3rlgtfw48i`),所以 jupyter_exec 里硬编码的地址重启后仍然有效。
**⚠ 账号下共 12 个实例**(fuyu/krea2/常用Comfyui/musubi/ideogram… 是别人/别的项目)。**只能对 `vrfgp6th8uu2nz1j` 做开关机操作,绝不碰其它任何实例。**

**开关机路由(还没完全打通)**:`GET /open/instance/power_on` 和 `GET /open/instance/power_off` 路由存在(POST/PUT 返回 405,只收 GET)。但目前带 `?id=` / `?instance_id=` / `?uuid=` 都回 `{"code":1000,"success":false}`,**正确的查询参数名还没试出来**。TODO:去 playground(会显示确切请求)或文档确认参数名,或直接问用户要一条能开机的 curl。查状态那条已经够用来判断掉没掉线。

**auto_shutdown**:实例设了 `auto_shutdown=1`,规则=**GPU 连续 ~4 小时没算力使用就自动关机**(用户强调过,踩过多次)。缓解:容器一上线就 `setsid -f python gpu_keepalive.py`(scratchpad 里有,4096×4096 周期 matmul,~700MB)持续占用;真实训练本身也算占用。
