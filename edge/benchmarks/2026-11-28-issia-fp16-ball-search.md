# ISSIA FP16 模型远端小球漏检与精搜复测

## 原因分析

上一轮 Ultralytics 全图推理中，同一 YOLO11n 权重在原始 PyTorch 与 FP16 Core ML 两侧均为 0/241，不能将零召回归因于 Core ML 转换。这些球的独立标注直径为 8.96–11.92 px，中位数 10.43 px；1920 px 宽的原图等比缩放到 640 px 后，对应中位直径仅约 3.48 px。小尺寸是合理的瓶颈假设，但本次数据不能单独量化像素数、图像模糊、训练数据和其他因素的贡献。

为诊断放大和上下文的影响，使用人工真值球框确定裁片中心，再用同一 FP16 模型分别推理 180×180 和 360×360 原图裁片。前者命中 1/241，后者命中 72/241（29.88%），但后者另有 21 帧选出的球位置错误。裁片变化同时改变球在输入中的尺寸、周围画面和缩放插值，不能单独归因于其中某一项。这些裁片利用了评估标注定位球，是**不可部署的诊断**，不能作为实际检测召回率。

## 可运行方案：启用原图精搜

沿用 Edge 现有 `detector.ballSearch=true`，以原始图像亮斑或近期实测球心选择裁片，全图漏球时额外推理裁片，并允许通过连续曝光的运动外观确认候选。两组均使用同一 FP16 Core ML 模型和同一 Edge Vision 工作进程，按机位独立从空历史开始，配对处理所有帧；标注只在推理后评分。

| 分组 | 精搜关闭 | 精搜开启 | 新增命中 / 丢失命中 |
| --- | ---: | ---: | ---: |
| 远端小球 | 3/241（1.24%） | 45/241（18.67%） | 42 / 0 |
| 全场直径 ≤16 px 的球 | 57/439（12.98%） | 168/439（38.27%） | 111 / 0 |
| 全部可见球 | 74/553（13.38%） | 223/553（40.33%） | 149 / 0 |

远端的 45 次命中中，3 次来自全图、31 次来自模型裁片、11 次来自运动外观。cam3、cam4、cam5 分别为 16/120、18/40、11/81；cam6 没有远端标注。此前 0/241 是 Ultralytics 的推理通路；此次关闭精搜的 3/241 是 Vision 工作进程的配对基线，两者的图像编码和预处理不同，因此改进幅度应以此次同通路的 3/241 对 45/241 为准。

收益伴随明显错位：精搜开启组在远端 241 个有球帧中有 14 帧输出错误位置，关闭组为 0；全部 553 个有球帧分别错位 27 帧和 0 帧。远端精搜结果中 13 次错位发生在已有历史球心的搜索阶段，提示错误球心可能引导后续搜索；这里只报告关联，未单独验证传播机制。1,847 个未标注帧中，关闭和开启组分别有 19 和 266 帧输出球，正误未知。数据没有确认无球的负例，不能计算误报率，不能仅凭召回率把这项配置当作精度达标。

远端 164 个冷启动帧中，搜索裁片在 48 帧覆盖真值球心，最终冷启动直接找回 9 帧；这提示有限次裁片选择仍会漏掉目标。剩余漏检需要更可靠的候选选择和独立负例验证。该场比赛既用于诊断也用于复测，连续帧不独立；上述结果是同场探索结果，需要其他比赛和负例复验。

## 口径与证据

- 与[转换对照](2026-11-28-issia-model-conversion.md)使用同一个数据集和标签摘要、相同 2,400 个机位帧，四机位各取第 400–1899 源帧、10 Hz。241 个远端小球为对侧半场且标注框长边 ≤16 px。
- 远端球以最高置信度检测球心落入标注框为命中；只有球心的其他标注使用 8 px 容差。无标注不当作负例。检测器门槛 0.35；这次配对复测包含 Vision 的 JPEG 编解码、模型、姿态及 OCR，但没有进行吞吐或延迟测量。
- M1 Pro、macOS 27.2.1；FP16 模型来自同一个 `yolo11n.pt`，模型摘要见运行目录的 `summary.json`。原始模型 SHA-256 为 `0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`。Vision 可执行文件 SHA-256 为 `67075331889f8aadd44214130a5d472af5a83666c1cadaac2d9cc2e8c7d2032d`。数据集及标签 SHA-256 分别为 `cc1493614c97e790fc0f722f6b0def622b81cb68ac91641fbb778558058c3332`、`d9b73973e45bcd9a37565168f6b7b5e7e5388839a71c36c8772c7b2a189bbd3a`。
- 本机逐帧证据和汇总位于 `edge/benchmarks/runs/2026-11-28-fp16-ball-search/`。真值居中诊断的逐帧结果位于 `edge/benchmarks/runs/2026-11-28-model-conversion/oracle-scale.json` 与 `oracle-scale-360.json`。运行目录不入库。

## 复测命令

在仓库根目录运行；模型、录像和标签使用转换对照保留的本机副本，输出路径须不存在。Vision 工作进程应与报告记录的二进制摘要一致。

```sh
inputs=edge/benchmarks/runs/2026-11-27-issia-inputs/issia-recall/dataset.json
model=edge/benchmarks/runs/2026-11-28-model-conversion/yolo11n.json
python3 -B edge/scripts/benchmarks/ball-search-recall.py "$inputs" /path/to/new-recall \
  --model "$model" --parallel-clips 2 --radius-px 8
edge/.venv-model-compare/bin/python -B edge/scripts/benchmarks/oracle-ball-scale.py \
  "$inputs" edge/benchmarks/runs/2026-11-28-model-conversion/yolo11n.mlpackage \
  /path/to/new-oracle-180.json --side 180
edge/.venv-model-compare/bin/python -B edge/scripts/benchmarks/oracle-ball-scale.py \
  "$inputs" edge/benchmarks/runs/2026-11-28-model-conversion/yolo11n.mlpackage \
  /path/to/new-oracle-360.json --side 360
```

部署比赛时在 `detector` 配置中使用 `"ballSearch": true`。应先在独立比赛中复核误检、错位和处理能力，再考虑开启；该实测不是目标机性能验收。
