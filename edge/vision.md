# Edge 单视角人球观测

## 在比赛中启用 YOLO11n

在 macOS 14+ 的比赛机上，从仓库根目录运行以下命令。假设比赛配置保存在 `$match_dir/match.json`，请先把 `match_dir` 设为实际的比赛目录。准备工具需要 Python 3，只在准备权重时联网。

```sh
match_dir=/path/to/match
python3 edge/models/prepare-yolo11n.py "$match_dir/models"
```

命令校验固定版本 ZIP 的 SHA-256，然后将 `yolo11n.mlpackage` 和 `yolo11n.json` 放入指定的新目录。现场无网时可先取得同一 ZIP，再加 `--archive /path/to/yolo11n.mlpackage.zip`。此基线是上游提供的 384×640（宽×高）、INT8 权重、带非极大值抑制（NMS）的 COCO 检测器，许可见模型清单。[上游权重发布页](https://github.com/ultralytics/yolo-ios-app/releases/tag/v8.3.0) · [随附清单](models/yolo11n.json)

在比赛配置中填写以下 `detector` 对象，然后照常执行 `run-match`、`stream-match` 或 `process-match`。

```json
{
  "detector": {
    "backend": "vision",
    "modelManifest": "models/yolo11n.json",
    "ballSearch": true
  }
}
```

`detector.workers` 可设为 1–8 的整数，默认 1。四路比赛可在上面的配置中加入 `"workers": 4`。Edge 将机位固定分配给工作进程，同一机位按曝光顺序解码和推理。收到当前曝光组的全部有效机位结果后，才按原顺序融合并提交场景。工作进程数受已配置机位数限制。

联合检测每帧执行一次全图 Core ML 推理，接受 `person` 和球类标签 `sports ball`、`soccer ball`、`football`、`ball`。保留置信度至少 0.35 的人框，在达到同一门槛的球框中保留最高分。系统 Vision 随后只在人框内估计姿态、从尺寸足够且与其他人框分开的躯干裁片识别号码。场上无人时输出空球员数组，球缺测时输出 `ball: null`。

`detector.ocrMode` 默认使用 `batch`。同一画面中符合读号条件、宽高均不超过 96 px 的躯干裁片，每批最多 6 人拼成一张图，送入 Vision 的高精度文字识别。文字框须完整落入一个人的裁片才能归给该人。大裁片及短边至少 32 px、中央灰度第 90 与第 10 百分位差至少 128 的高反差裁片保留逐人识别。改为 `individual` 可对全部符合条件的裁片逐人精读。复算比较时应为不同配置分配不同的 `streamEpoch`。

`detector.ocrRefreshMs` 可设为 100–10000 的整数毫秒，默认省略，表示每个入选帧都读号。设置后，某条轨迹的号码经多帧投票确认，Edge 会把它在上一曝光组各机位的人框作为免读号区域交给工作进程。本帧人框与该区域的交并比至少 0.5 时跳过读号，观测记录 `ocrSkipped: true`，不带 `number`。同一轨迹距上次实际读号满该间隔后重读一次。读到不同号码时撤销确认，恢复逐帧读号。上一曝光组超过 250 ms 或轨迹转为缺测时也全部重读。免读号区域只由已归档的观测和投票结果推出，恢复比赛时结果相同。

`detector.computeUnits` 选择 Core ML 检测器的计算单元，取值为 `all`（默认）、`cpuAndNeuralEngine`、`cpuAndGPU` 或 `cpuOnly`，需要已配置模型。它对应 `MLModelConfiguration.computeUnits`，只作用于 Core ML 检测，系统 Vision 的姿态和读号仍由 Vision 调度。在目标机上比较不同取值时，可直接调用工作进程并设置环境变量 `PITCHTRACE_VISION_TIMING=1`，每帧输出的 `timing` 含检测、姿态和读号耗时。整场复算的每种取值使用不同的 `streamEpoch`。[Apple 计算单元配置](https://developer.apple.com/documentation/coreml/mlcomputeunits)

`ballSearch` 默认关闭。开启后，每个机位用最近两次二维实测球心和曝光时刻预测原图搜索区域。只有一次观测时沿用该球心，最长沿用 0.3 秒。没有近期球心时，Edge 从当前原图寻找草地附近的小亮斑，并按曝光时隙轮换候选区域和画面分块。全图模型漏球时才在选中的原图裁片上额外运行一次模型。

固定机位还可以用连续三个曝光中的实际亮斑找回运动的球。每帧最多保留 48 个紧致亮斑。候选须有相符的尺寸与位移，并通过人框排除、整体平移和歧义检查。每个机位在 `ballSearch.candidates` 中单独保存候选，换标定、切换媒体周期或图像尺寸变化后重新开始。恢复比赛时从已归档的二维观测还原历史。暗色球、停住的球与镜头移动会影响这一方法。

## 接入已有模型

`detector.modelManifest` 路径相对于比赛配置，清单中的 `model` 路径相对于清单。必填字段如下。

| 字段 | 内容 |
| --- | --- |
| `format` | `pitchtrace-vision-model-v1` |
| `model` | 本地 `.mlpackage`、`.mlmodel` 或 `.mlmodelc`，图像输入，Vision 输出带类别的检测框 |
| `task` | `person-and-ball` 使用模型提供的人框和球框。`ball` 使用模型找球、系统 Vision 检测人体 |
| `preprocessing` | `vision-scale-fit`，按原图比例缩放并填边 |
| `version`、`source`、`license` | 分别记录权重版本、来源、许可，均为非空字符串 |

自有 YOLO11 检测器导出 Core ML 时需使用固定图像输入，启用 `nms=True`，保留类别名。直接填写已有的 `detector.model` 路径沿用足球专用模式。省略模型时，系统 Vision 仍可做人体、姿态和文字识别。[Core ML 导出选项](https://docs.ultralytics.com/integrations/coreml/)

## 复核原图与模型版本

`scene-evidence` 的 `observations` 保存单机位的二维观测。`imageSize` 为编码像素宽高，坐标原点在原图左上。人框与球框 `box` 均按 `[左, 上, 宽, 高]` 记录。球观测同时保存球心 `pixel`、置信度 `confidence` 和来源 `source`。`coreML` 表示模型全图命中，`coreML-ROI` 表示模型裁片命中，`motion-appearance` 表示用多帧外观与运动确认，`ballHSV` 表示显式配置的球色检测。精搜命中记录原图搜索范围 `searchBox`，`searchMode` 中的 `cold` 表示没有近期球心，`history` 表示根据近期球心追搜。

模型框的 `quality.personOverlapFraction` 是其他人框覆盖该框的面积占比，多框重叠区域只计一次。`quality.laplacianVariance` 是原图灰度裁片内的拉普拉斯方差，可在相同尺度、相似纹理下帮助复核模糊帧。裁片任一边小于 4 像素时省略该值。

人框中仅检出一个人体时记录可见关节 `joints`，用 `leftShoulder`、`rightAnkle` 等名称索引原图像素。脚踝可见且接近画面内的人框下沿时，`foot` 估计鞋底位置。躯干裁片中识别出唯一的一到两位数字时记录 `number`，`shirtRGB` 为同一区域的 sRGB 三通道采样。后续融合结合标定与多视角观测估计三维位置，进行时序号码确认和球队关联。

运行 `match-info` 可以读取本周期的 `modelManifest`、清单原始字节摘要 `modelManifestSha256` 和权重 `modelSha256`。模型包按包内相对路径记录各文件摘要。同一场景周期恢复时会核对这些信息。更换模型复算需选用新的 `streamEpoch`。实拍精度按比赛独立标注评估。[评估输入与指标](evaluation.md)

构建 Edge 后，在仓库根目录执行 `node --test edge/Tests/Integration/vision-worker.mjs` 可离线复验。用例将已知人框、球框的微型 Core ML 检测器送入实际工作进程，校验宽屏与竖屏坐标、置信度过滤、空场和裁片命中后还原的原图球框。

ISSIA 实拍基准用四个同步机位的录像对比精搜开关，并在 M1 Pro 上按原速投放四路推理任务。报告记录无历史球心时的命中、错位、三个视觉阶段耗时和正式四进程的积压。[冷启动精搜与四路复测](benchmarks/2026-11-27-issia-coldsearch.md) · [仅历史精搜的旧版基线](benchmarks/2026-11-27-issia.md)

## 直接调用视觉工作进程

`pitchtrace-edge vision-worker [模型路径 [--person-and-ball] [--ball-search]] [--individual-ocr] [--ocr-hints] [--frame-control] [--compute-units 计算单元]` 通过标准输入接收原尺寸、编码方向朝上的 JPEG。默认每帧由 4 字节大端无符号长度和 JPEG 数据组成。启用 `--ball-search` 后，每帧在长度之前增加 4 个大端 UInt32，表示原图搜索框 `[左, 上, 宽, 高]`，全零表示本帧只搜全图。启用 `--ocr-hints` 后，在搜索框之后、长度之前增加 1 个大端 UInt32 的区域数 N（0–64），再接 N 组 `[左, 上, 宽, 高]`，宽高须大于零。启用 `--frame-control` 后，在免读号区域之后、长度之前再加 4 个大端 UInt32 `[标志, 球心 x, 球心 y, 半径]`：标志第 0 位停读号，第 1 位只为人框中心距球心不超过半径的人估计姿态，其余位须为零。这是 `stream-match` 过载降级使用的逐帧控制，见 [过载降级](stream-match.md#过载降级)。`--compute-units` 需要模型路径。比赛编排负责生成搜索框和运动候选。每次发送一帧，读回一行 UTF-8 JSON，再发送下一帧。最后在帧边界关闭标准输入。单个 JPEG 上限为 32 MiB、48 百万像素。解码失败、包截断、裁片越界、模型错误和 Vision 执行失败均通过标准错误报告并退出。

## 模型转换前后对照

在 macOS 上使用 Python 3.12 创建独立环境并安装依赖。准备同一份原始 `.pt` 权重及其固定 640×640、FP16、带 NMS 的 Core ML 导出；使用自己的权重时须遵守相应许可。转换示例：

```sh
python3.12 -m venv edge/.venv-model-compare
edge/.venv-model-compare/bin/python -m pip install 'ultralytics==8.4.220' 'coremltools==9.0' 'numpy>=2,<2.4'
edge/.venv-model-compare/bin/python -c 'from ultralytics import YOLO; YOLO("/path/to/yolo11n.pt").export(format="coreml", imgsz=640, quantize=16, nms=True)'
edge/.venv-model-compare/bin/python edge/benchmarks/compare-models.py \
  /path/to/dataset.json /path/to/yolo11n.pt /path/to/yolo11n.mlpackage /path/to/report.json
```

数据集采用 [球精搜基准](benchmarks/ball-search-recall.py) 的 `clips` 与 `labels` 格式，也可用 [ISSIA 标注转换工具](benchmarks/prepare-issia-recall.py) 生成。报告新建 JSON，保存数据集、标签和模型摘要、参数、每帧两侧的人球框及同类别 IoU 贪心配对。相同画面依次推理，原始权重使用 CPU，Core ML 使用 Ultralytics 默认计算单元（CPU 与神经网络引擎，加载失败时回退 CPU）；默认 10 Hz、640 像素方形输入、置信度 0.35；与导出时的尺寸保持一致。需要更改采样、阈值或球心容差时查看 `--help`。Core ML 导出内置的 NMS 阈值也会影响结果，须和导出配置一起记录；对照的是 Ultralytics 推理通路，不能替代 Edge Vision 工作进程的实机评估。

球评分沿用精搜基准：只检查最高置信度球框，框标注按检测球心是否落入真值框评分，球心标注按距离容差评分。远端与远端小球分别统计，后者要求 `far=1` 且标注框长边不超过 16 像素；未标注、`ignore` 及没有远近信息的样本不会被当作负例。`absent` 表示确认无球，可统计误报；错位单独计数，召回分母包含所有可见球样本。人框目前只做两模型之间的配对比较，不表示相对人工真值的精度。报告中的逐帧结果用于复核，实拍样本不足时不能据此宣称精度达标。
