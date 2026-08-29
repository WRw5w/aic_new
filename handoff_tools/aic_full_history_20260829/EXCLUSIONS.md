# 交接包排除边界

## 不进入公开记录包

- `data/` 和任何 train/test 原始图片。
- 数据集压缩包、特征缓存和 NumPy 中间数据。
- 批量模型文件：`*.pt`、`*.pth`、`*.ckpt`、`*.safetensors`、`*.bin`。
- 依赖缓存、第三方可执行文件、编译产物、pytest/tmp/cache。
- token、cookie、密码、浏览器 profile、SSH 私钥。

## 单独作为 Release 资产

- 正式参考模型 `b448_aligned formal v2/full.pt`。
- 92 分诊断路线的 `pe_core_g14_lora ep04.pt`。

## 净化 Git 历史删除的路径

- `.codex/direct_upload_secret.txt`
- `.codex/local_archive_token.txt`
- `.codex/xgpu_upload_ed25519`
- `.codex/xgpu_upload_ed25519.pub`

删除只发生在临时镜像和交接 bundle 中，不修改原仓库。
