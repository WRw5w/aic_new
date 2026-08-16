# 当前可交付参考模型复现说明

## 交付口径

请区分三个数字：

| 口径 | 分数 | 说明 |
|---|---:|---|
| 历史最高合法单次榜分 | 78.9122 | `b512_aligned`；本地未发现一一绑定 checkpoint，不作为可复现交付承诺 |
| 当前可交付参考模型 | 78.8561 | `b448_aligned formal v2`；checkpoint、日志、预测和 provenance 完整 |
| 可靠重训基准 | 78.5477 | `champion_real`；用于提醒 seed/run variance，不能用单次 78.91 推断稳定复现水平 |

## 参考模型资产

- checkpoint：`runs/aic_b448_aligned_formal_v2_20260713/collected/full.pt`
- 预测包：`runs/aic_b448_aligned_formal_v2_20260713/collected/pred_b448_aligned_tta_balanced.zip`
- 训练日志：`runs/aic_b448_aligned_formal_v2_20260713/collected/train.log`
- 提交来源：`runs/aic_b448_aligned_formal_v2_20260713/collected/provenance.submit.json`

SHA-256 见 `ARTIFACTS.csv`。上传、下载、改名后都要重新校验，哈希不一致时停止使用。

## 模型配方

- 骨干：OpenAI CLIP ViT-B/32，冻结。
- PEFT：LoRA rank 32 / alpha 64，注入 attention + MLP，12 blocks。
- 分辨率：448。
- 位置编码：`aligned`，即 bilinear + `align_corners=True`。
- 去噪：cleanlab 为主，叠加保守共识伪标签。
- 训练：12 epochs、mixup 0.2、EMA 0.999、RandAugment、label smoothing 0.1。
- 权重：同一训练轨迹从 epoch 4 到 12 做 SWA；禁止跨 seed、跨配方平均。
- 推理：448/512/576 多尺度 + 翻转 TTA + 类别均衡 bias 校正。

## 新机器准备

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-cuda.txt
pip install -r requirements.txt
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
```

队友必须自行从比赛平台取得当前阶段数据，并放到：

```text
data/train/<四位类别编号>/*.jpg
data/test/*.jpg
```

不要把初赛特征缓存直接套到复赛或半决赛。官方规则明确每阶段数据不同，且禁止跨阶段混用旧数据。

## 先做离线检查

```powershell
python -m compileall -q robustft finetune_lora.py tools
python -m pytest -q tests/test_lora_contract.py tests/test_three_way_split.py tests/test_robust_utils.py
python check_submission.py --zip runs/aic_b448_aligned_formal_v2_20260713/collected/pred_b448_aligned_tta_balanced.zip
```

## 推理复核

将 `full.pt` 放到独立工作目录的 `lora/full.pt`。不要覆盖已有 checkpoint。

```powershell
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"
python tools/tta_predict.py `
  --work-dir outputs_handoff_b448_aligned `
  --out-prefix submissions/pred_b448_aligned_recheck `
  --scales 448,512,576 `
  --num-workers 2 `
  --no-pin
```

生成后只考虑 `*_tta_balanced.zip`。先运行 `check_submission.py`，再生成 provenance，最后才允许进入提交队列。

## 新阶段重训

复赛和半决赛应该重新构建特征、重新估计 cleanlab 阈值、重新跑 seed 控制。初赛的 `keep-ratio=0.90`、均衡校正和 SWA 窗口都只能作为先验，不能当固定真理。

建议最小实验矩阵：

1. `448 aligned` 与 `512 aligned` 各跑两个 seed，确定新阶段的分辨率平台。
2. cleanlab + mixup + EMA 作为基准。
3. 长尾阶段增加 class-balanced sampler / logit adjustment 对照，但要把训练期长尾处理与测试期均衡假设分开。
4. 同轨迹 SWA 只在已确认进入同一收敛盆地的连续 epoch 上做。
5. 每个候选保存 config、git commit、数据阶段标识、checkpoint hash、预测 hash 和榜分证据。

