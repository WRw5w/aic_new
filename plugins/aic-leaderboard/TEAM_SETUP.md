# 队友安装与适配说明

来源：<https://github.com/WRw5w/new_mcp/commit/40fae38e1d90bb038ddb0d054a0af04cb100ad24>。
包含真正的 stdio MCP 服务、7 个工具、Codex 插件声明、配套 skill、CLI、浏览器脚本和测试。
本次同步保留上游业务实现；本地差异是中文交接说明和 MCP 启动显式启用 UTF-8。

## 1. 安装（Windows PowerShell）

从 aic_new 仓库根目录执行，需已有 Python 3.10+、Node.js/npm 和 Chrome：

```powershell
cd plugins/aic-leaderboard
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
npm ci
```

macOS/Linux 对应使用 `python3 -m venv .venv` 和 `.venv/bin/python`；其余安装步骤相同。

## 2. 在 MCP 客户端配置

将下列对象合并到客户端的 MCP 配置，不要覆盖已有服务。所有示例路径和网址均须替换。
`command` 指向刚安装依赖的虚拟环境 Python；`PYTHONUTF8` 也让子进程统一使用 UTF-8。
根目录需指向本插件目录，因为自动流水线从这里寻找 tools 脚本。

```json
{
  "mcpServers": {
    "aic-leaderboard": {
      "command": "D:/aic_new/plugins/aic-leaderboard/.venv/Scripts/python.exe",
      "args": ["-X", "utf8", "-m", "aic_leaderboard.mcp"],
      "env": {
        "PYTHONUTF8": "1",
        "AIC_LEADERBOARD_ROOT": "D:/aic_new/plugins/aic-leaderboard",
        "AIC_LEADERBOARD_TEAM_ID": "替换为本人参赛编号",
        "AIC_LEADERBOARD_SUBMIT_URL": "https://reg.aicomp.cn/替换为本赛道完整提交路由",
        "AIC_LEADERBOARD_LEADERBOARD_URL": "https://reg.aicomp.cn/替换为带赛道参数的完整榜单路由",
        "AIC_LEADERBOARD_RECORDS_URL": "https://reg.aicomp.cn/替换为本赛道结果查询路由",
        "AIC_LEADERBOARD_PIPE_PROFILE": "D:/aic-browser-profile-teammate",
        "AIC_LEADERBOARD_DEADLINE": "替换为本赛道带时区的ISO8601截止时间",
        "AIC_DAILY_SUBMIT_LIMIT": "5"
      }
    }
  }
}
```

每日额度应按本人赛道核对；截止时间格式例如 `2026-10-05T20:00:00+08:00`，此例不代表其他比赛的期限。
不要只填 `/special/phb/detail`，缺赛道参数可能让榜单接口失败。
重启客户端后检查 `initialize`、`tools/list`，应能发现 7 个工具。
Codex 插件文件为 `.codex-plugin/plugin.json`，配套技能位于 `skills/aic-leaderboard/`。
直接使用 MCP 配置时无需另外安装 skill；只复制 skill 则不会启动 MCP 服务。

每个账号/赛道使用独立的插件工作副本与浏览器 profile，避免共享队列、账本或登录态。
首次浏览器连接需本人登录；服务使用 Playwright 调试管道，默认不依赖 9222 端口。

## 3. 跨比赛适配边界

此版本源自棒材赛道，不是已验证的图像识别赛道提交器：

- `core.safe_zip` 仅接受 ZIP 内单个根目录 JSON；RAR 目前只检查文件大小，不能据此认定内容合规。
- 如果本赛道要求 CSV、多个文件或其他压缩格式，先实现对应格式校验及测试，不要绕过验证。
- 浏览器字段、阶段映射、提交/结果查询页面都需要按本人赛道验证；当前 `semi` 映射为“复赛”。
- 格式校验不检查模型合法性；本仓库 CLIP、单模型等要求仍以根目录 AGENTS.md 为准。
- 本仓库正式打榜目前只允许 `my_auto_kaggle` 的 `jinyinsai_submit` MCP 控制面。本目录用于共享及适配，不自动替换该控制面；需要切换时再明确更新项目政策与集成。

建议先测试工具发现、只读队列状态和格式校验，再完成赛道适配。真实提交必须绑定候选包及用户的明确提交要求。
优先使用带账本闸门的 `aic_auto_submit`；低层 `aic_submit_candidate` 不检查每日账本额度。
抓分必须匹配参赛编号、阶段、时间窗口及回下载附件 SHA-256，不能把“上传成功”当成得分成功。

## 4. 不打开浏览器的回归测试

```powershell
.\.venv\Scripts\python.exe -X utf8 -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -X utf8 -m unittest test_auto_submit -v
node --check tools/leaderboard_pipe.mjs
node --check tools/leaderboard_cdp.mjs
```

本次发布不携带比赛数据、迁移档案、原始会话、密钥、浏览器 profile、登录凭证或提交账本。
