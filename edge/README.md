# PitchTrace Edge

场迹的场边计算子项目。第一阶段 MVP 计划在 M6 Mac mini 上接入四路单摄，每个机位一台相机，以同步画面重建人球轨迹。软件支持分机位采集、按曝光标定与重建、动作候选复核、SQLite 归档和 v2 场景与原片上行。

从双机位实拍到四路试点的模块分工、逐周交付物和验收门槛见 [Edge 需求与开发计划](../docs/07-edge-development-plan.md)。

在 macOS 14 或更新版本使用 Swift 6.0 或更高版本。在仓库根目录构建和测试。

```sh
swift build --package-path edge
swift test --package-path edge --disable-xctest
```

在仅安装 Command Line Tools 的 Swift 6.4.2 环境，若测试提示 `plugin for module 'TestingMacros' not found`，可在上面的 `swift test` 命令末尾追加 `-Xswiftc -plugin-path -Xswiftc /Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing`。

## 不接相机跑完一场样例

比赛处理需要 Python 3.10+、带 FFmpeg 后端的 OpenCV、NumPy、FFmpeg 和 ffprobe。以下命令生成 2 秒双机位 H.264/fMP4 录像及独立坐标真值。球色检测读取原片像素，样例还提供一份带球员和球的二维标注，供几何与身份模块独立复算。

```sh
swift build --package-path edge
trial_dir=$(mktemp -d)
python3 -B edge/samples/generate-match.py "$trial_dir/input"
edge/.build/debug/pitchtrace-edge run-match "$trial_dir/input/match.json" "$trial_dir/run"
edge/.build/debug/pitchtrace-edge reconstruct-match "$trial_dir/input/annotated.json" "$trial_dir/input/observations.ndjson" "$trial_dir/run/scenes.sqlite"
edge/.build/debug/pitchtrace-edge scene-evidence "$trial_dir/run/scenes.sqlite" training video 5
edge/.build/debug/pitchtrace-edge compare-scenes "$trial_dir/run/scenes.sqlite" training video annotated
edge/.build/debug/pitchtrace-edge evaluate-match "$trial_dir/run/scenes.sqlite" training annotated "$trial_dir/input/truth.ndjson"
```

每个场景周期各有 20 帧。第 5 帧起隔离 A 机位，此时只有 B 看到了球，球心三维位置缺测。第 9 帧恢复双路标定。第 12 帧开始球被遮挡，短时预测到期后转为丢失。标注复算中，7 号球员在机位切换前后沿用同一轨迹 ID。首个周期使用只找指定颜色球的 `color-ball` 检测器，球员来自第二个周期的独立二维标注。

| 环节 | 命令与输入 |
| --- | --- |
| 采集和对时 | `capture` 独立运行各机位，`attach-exposures` 核验设备逐帧时钟，`replay` 导出原片。见[采集与健康状态](capture.md)。 |
| 配置与标定 | `calibrate-intrinsics`、`calibrate` 估计参数，`audit-calibration` 复查移机和几何覆盖。比赛按曝光选取有效机位。见[巡检](calibration-audit.md)和[比赛配置](match-config.md)。 |
| 视觉与重建 | `process-match` 从录像解码并观测；`reconstruct-match` 接受按曝光排序的二维 NDJSON。见[检测与模型](vision.md)、[轨迹融合](tracking.md)和[身份、姿态与动作](analysis.md)。 |
| 编排、上行与恢复 | `run-match` 处理已有录像或启动采集，可分别提交场景及原片。`stream-match` 跟随正在采集的媒体段。见[离线运行](run-match.md)、[连续处理](stream-match.md)和[场景归档](scene-archive.md)。 |
| 复核与留存 | `scene-evidence` 取原帧和关节证据，`match-info` 取运行版本，`export-scenes` 过滤导出，`compare-scenes` 比对复算结果，`evaluate-match` 计量独立真值，`retention` 预览到期清理。见[真值评估](evaluation.md)和[场景归档](scene-archive.md)。 |

比赛配置的 `detector.backend` 默认为 `vision`，在 macOS 上调用系统人体检测、姿态和 OCR。可用 `detector.model` 指定能输出带类别框的 Core ML 足球模型，路径相对于比赛配置。受控球色的训练录像可用 `detector.ballHSV` 设置 OpenCV 的 HSV 上下界，作为无模型或模型漏检时的球候选。`detector.teamColors` 将球队 ID 映射到样衣 sRGB 三元组（0–255），色差大或两队难以区分时保留球队未知。

整套回归从仓库根目录执行。Node 集成用例复用 Live 网关的 v2 校验器，首次运行需安装其锁定依赖。部分用例会在本机回环地址启动临时网关。

```sh
npm ci --prefix live/server
swift build --package-path edge
swift test --package-path edge --disable-xctest
python3 -B -m unittest discover -s edge/Tests/Integration -p 'test_*.py'
node --test edge/Tests/Integration/{calibration,intrinsics,match-pipeline,media-epoch-replay,run-match}.mjs
```

## 离线录像输入

运行时需要 PATH 中的 `ffprobe`，用于读取 H.264/fMP4 中实际解出的展示帧、PTS、时基及帧时长。输入目录包含 2–4 个以机位 ID 命名的子目录。每路可独立使用多个媒体周期，只有一个周期的旧录像可沿用以下平铺布局。

```text
recordings/
├── A/
│   ├── init.mp4
│   └── segments/
│       ├── 0.m4s
│       └── 0.exposure.json
└── B/
    ├── init.mp4
    └── segments/
        ├── 0.m4s
        └── 0.exposure.json
```

曝光文件每帧记录和录像同一时基下的 `pts`、UTC Unix 微秒十进制字符串 `captureUtcUs`，以及可选的相机原始帧号 `frameNumber`。例如 `{"frames":[{"pts":0,"captureUtcUs":"1795494597700000","frameNumber":42}]}`。实际输入须覆盖对应媒体段的每个展示帧。

当 A 的媒体时钟重置时，将它的原片分别放入 `A/epochs/before/` 和 `A/epochs/after/`，各含自己的 `init.mp4` 和 `segments/`。B 可以继续使用平铺布局。同一机位选择一种布局。周期目录名使用 v2 标识，先后顺序取决于曝光 UTC。切换周期后，PTS、片段号和原始帧号可重新计数，时基也可改变。同一机位各周期的曝光时间范围必须互不重叠，曝光 UTC 应已映射到共同物理时钟。

在已有父目录下指定一个新的输出目录。

```sh
swift run --package-path edge pitchtrace-edge replay "./录像目录" "./新输出目录"
```

结果按机位保存原始 `init.mp4`、`segments/{n}.m4s` 及符合 [v2 契约](../schemas/v2/media-index.schema.json)的 `segments/{n}.json`。导出时每路只保留当前段的帧记录，按曝光 UTC 归并成 `frames.ndjson`，同曝光时按机位 ID 排序。每行保留机位、片段、PTS、时基、时长及可用的原始帧号。`gaps.txt` 按机位记录片段号、展示时间或原始帧号的缺口。缺曝光映射和时间倒退会使命令失败，成功后才发布整个输出目录。

导出保留各机位的输入布局。使用 `epochs/` 的机位在每条帧记录中增加 `mediaEpoch`，通过机位、周期、片段号、PTS 可取回唯一原帧。平铺录像继续使用原帧记录格式。

## 离线几何标定

两个标定命令均需要 PATH 中的 `python3` 能导入 OpenCV 4.5 或更新版本（`cv2`）和 NumPy（`numpy`）。

### 用棋盘格照片标定单机位镜头

保持比赛时使用的镜头、焦距和原始分辨率，让平整的棋盘格向不同方向倾斜，分别拍到画面中央和靠近边缘的位置。每台相机至少需要 3 张能检全内角点且姿态提供独立约束的照片。连续拍同一姿态会使求解退化。

每台相机单独准备一份清单。下面以 A 机位的实拍清单 `lens.json` 为例，`columns`、`rows` 填内角点的列数和行数，均至少为 3。10 列、7 行方格对应 9 列、6 行内角点。`squareSizeM` 填实测的单格边长，单位为米。`images` 内的相对路径从清单所在目录解析。全部照片使用同一原始宽高，程序按编码像素方向读图。

```json
{
  "cameraId": "A",
  "intrinsicsId": "a-lens-v1",
  "sampleSource": "field",
  "board": {"columns": 9, "rows": 6, "squareSizeM": 0.025},
  "images": ["A/01.png", "A/02.png", "A/03.png", "A/04.png", "A/05.png", "A/06.png"]
}
```

从仓库根目录运行，保存该机位的内参报告。

```sh
swift run --package-path edge pitchtrace-edge calibrate-intrinsics lens.json > a-lens.json
```

报告保留 `cameraId`、`intrinsicsId` 和 `sampleSource`。`imageSize` 给出原图宽高，`intrinsics` 给出像素单位的焦距 `fx, fy` 和主点 `cx, cy`，`distortion` 按 OpenCV 顺序保存五个系数 `[k1, k2, p1, p2, k3]`。`fitRmsPx` 是参与拟合的全部角点的重投影均方根误差（像素）。`views` 按照片路径 `image` 列出检测结果。`status` 为 `used` 的照片另有本张的 `rmsPx`，角点未检全的照片记为 `notDetected`。场地定位精度另用独立检查点按米验收。

也可在临时目录生成合成样例并复跑内参标定。样例分辨率为 960×540，包含 8 张棋盘格和 1 张无棋盘格图像。可将报告与生成器的镜头真值 `fx=620, fy=660, cx=451, cy=293` 对照。

```sh
trial_dir=$(mktemp -d)
python3 edge/Tests/Integration/generate-chessboards.py "$trial_dir"
swift run --package-path edge pitchtrace-edge calibrate-intrinsics "$trial_dir/lens.json" > "$trial_dir/a-lens.json"
```

### 用场地点标定多机位外参

`calibrate` 使用各机位的镜头参数和场地点的像素标注，为 2–4 台相机估计世界坐标到相机坐标的外参，再从多个视角重建独立检查点。从仓库根目录运行双机位合成样例。[标定样例](samples/calibration.json)

```sh
swift run --package-path edge pitchtrace-edge calibrate edge/samples/calibration.json > calibration-report.json
```

准备自己的输入时，使用以下字段。

| 字段 | 填写方法 |
| --- | --- |
| `calibrationId`、`sampleSource` | 为这组机位、镜头和分辨率指定标定版本。合成数据填 `synthetic`，实测数据填 `field`。 |
| `cameras` | 每个机位填写 `imageSize: [宽, 高]`，并提供 `intrinsicsId` 和 `intrinsicsFile` 以引用报告，也可像合成样例那样直接填写 `intrinsics` 和 `distortion`。标注使用这组内参对应的原始分辨率，像素原点在左上角，`u` 向右、`v` 向下。 |
| `fitPoints` | 每点填写唯一 `id`、场地坐标 `worldM: [X,Y,Z]` 和看得到该点的各机位 `pixels: {机位: [u,v]}`。以中圈地面为原点，X 沿长边，Y 向上，Z 沿短边，单位米。每机位至少需要 4 个分散的共面点，或 6 个非共面的点。 |
| `checkPoints` | 用相同格式填写预留地面点和已知高度的点，每个点使用独立测量的坐标。某机位缺测时省略该机位。 |

例如，A 机位实拍分辨率为 1920×1080 时，可在外参输入的 `cameras.A` 填写以下对象。`intrinsicsFile` 相对于外参输入文件解析。命令会核对报告中的机位、内参版本、图像尺寸和数据来源，任一不符都会报错。更换镜头、焦距或分辨率后，重新标定并引用新的 `intrinsicsId`。

```json
{"imageSize": [1920, 1080], "intrinsicsId": "a-lens-v1", "intrinsicsFile": "a-lens.json"}
```

报告的 `cameras` 给出各机位的旋转矩阵、平移向量和拟合点重投影均方根误差（`fitRmsPx`）。通过文件引用内参的机位另有 `intrinsicsId`，便于追溯镜头版本。将场地坐标列向量乘以 `rotationWorldToCamera` 再加 `translationM`，得到 OpenCV 相机坐标（X 向右、Y 向下、Z 向前）。`checkPoints` 保留每个检查点的状态，成功时给出重建坐标、三维误差（米）和重投影均方根误差（像素）。单视角或交会角不足 1° 的点保留为缺测。

样例的两台虚拟相机分别位于 `[-20,10,-22]` 米和 `[19,9,-20]` 米，都朝向中圈。每路使用 6 个地面拟合点。预期两个独立地面点和第一个高点均可重建，第二个高点只有 A 路观测。所有可重建点的三维误差应小于 1 毫米。查看 `ground` 和 `elevated` 中的计划点数、可测点数、P95 和最大误差。至少有一个地面检查点且全部可测时，`ground.withinThreshold` 给出 P95 是否满足 0.30 米门槛。

执行 `swift build --package-path edge` 后，运行 `node --test edge/Tests/Integration/intrinsics.mjs edge/Tests/Integration/calibration.mjs` 可复验合成棋盘格的内参恢复、双路和四路外参标定、检查点误差超限及退化机位。

## 用生成样例联调

样例需要 Node.js 22.13+、FFmpeg（带 `libx264`）和 ffprobe。先在一个终端启动 [Live 网关](../live/server/README.md#启动本地直播接收服务)，再从仓库根目录执行以下命令。

```sh
trial_dir=$(mktemp -d)
node live/server/tools/review-sample.mjs --recordings "$trial_dir/recordings"
swift run --package-path edge pitchtrace-edge replay "$trial_dir/recordings" "$trial_dir/media"
for camera in A B; do
  swift run --package-path edge pitchtrace-edge publish-media \
    http://127.0.0.1:8766 review-edge-01 sample "$trial_dir/media/$camera"
done
```

`publish-media` 取导出的机位目录名作为机位 ID。参数 `sample` 是该机位本次录像的媒体周期。媒体时钟重置后使用新的周期。命令逐段核对字节数及 SHA-256，经网关确认后继续。中断可用相同参数重跑，已经归档的同内容原片会得到幂等确认。遇到 409 时按报错中的机位、周期和路径检查内容冲突。

两路样例共 220 帧、4 段。在网关用以下请求查询同一次曝光。预期 A、B 的 `skewUs` 均为 0，PTS 分别为 35328、32256。将查询值改为 `1795494576200000` 后，两路均应返回 404。

```sh
for camera in A B; do
  curl --fail-with-body "http://127.0.0.1:8766/v2/matches/review-edge-01/cameras/$camera/locate?captureUtcUs=1795494600000000"
done
```

如需在浏览器复核，再用样例脚本提交配套的场景快照。脚本也会以相同路径重传两路原片。

```sh
node live/server/tools/review-sample.mjs http://127.0.0.1:8766 review-edge-01 --media-dir "$trial_dir/media"
```

打开 [review-edge-01](http://127.0.0.1:8766/?match=review-edge-01)，进入「归档回放」，在 `00:25.000` 暂停并切换两路同步原片。两路分别使用 30 fps 和 25 fps，预期命中同一预设曝光 UTC。`00:01.200` 可验证缺片提示。

### 模拟 A 路时钟重置

以下命令生成一场新的双路样例。A 的第二个媒体周期从片段 0、PTS 0 开始，B 的媒体时钟连续。对有 `epochs/` 的导出目录，`publish-media` 从目录读取各周期名，对平铺的 B 仍指定周期名。

```sh
reset_dir=$(mktemp -d)
node live/server/tools/review-sample.mjs --recordings "$reset_dir/recordings" --reset-camera A
swift run --package-path edge pitchtrace-edge replay "$reset_dir/recordings" "$reset_dir/media"
swift run --package-path edge pitchtrace-edge publish-media http://127.0.0.1:8766 review-reset "$reset_dir/media/A"
swift run --package-path edge pitchtrace-edge publish-media http://127.0.0.1:8766 review-reset sample "$reset_dir/media/B"
```

按 `captureUtcUs` 查询 A，重置前后可各命中一次片段 0 的 PTS 0。单段播放列表指向各自的初始化文件和原片。样例时间可直接用于网关的 `/v2/matches/review-reset/cameras/A/locate?captureUtcUs=…`。

| 曝光 UTC（µs） | A 路预期命中 |
| --- | --- |
| `1795494597700000` | `before`，片段 0，PTS 0 |
| `1795494599700000` | `after`，片段 0，PTS 0 |
| `1795494600000000` | 比赛 `00:25.000`，`after`，片段 0，PTS 4608 |

在仓库根目录运行以下回归，可自动生成样例、启动独立的本地网关并核对帧序、重复上传、曝光定位和原片内容。

```sh
swift build --package-path edge
node --test edge/Tests/Integration/media-epoch-replay.mjs
```

## 四路 30 分钟录像重放基准

两段 4 秒样片可直接打开。要看完整录像，从仓库根目录运行 `node edge/samples/prepare-videos.mjs`，再打开 `edge/samples/four-camera-30min/` 下的四个 MP4。同目录的 `recordings/` 可直接交给 Edge 重放。[测试录像与使用方法](samples/README.md)

以下命令从仓库根目录运行。生成器循环现有双机位样例的压缩画面，保留其 320×180 分辨率和两路曝光起点，重新封装为每段 2 秒的 H.264/fMP4。A、C 为 30 fps，B、D 为 25 fps，每路各 900 段。A 在本机位第 15 分钟重置媒体时钟，前后各 450 段，B、C、D 连续。

```sh
swift build --package-path edge -c release
mkdir -p edge/benchmarks/runs
run_dir=$(mktemp -d "$PWD/edge/benchmarks/runs/long-replay.XXXXXXXX")
node edge/Tests/Integration/generate-long-recordings.mjs "$run_dir/recordings"
edge/.build/release/pitchtrace-edge replay "$run_dir/recordings" "$run_dir/media" --metrics > "$run_dir/replay-metrics.json"
node edge/Tests/Integration/verify-long-replay.mjs "$run_dir/media" > "$run_dir/verification.json"
```

`--metrics` 将本次重放的计量结果以 JSON 写到标准输出。单调时钟覆盖读取、ffprobe 逐段解析、曝光校验、归并、原片与索引写盘，直到输出目录就绪。帧率按四路实际导出总帧数除以耗时计算。内存采用 macOS 内核记录的最高驻留集大小（RSS，单位字节），分别记录 Edge 主进程和各次 ffprobe 调用中的最大值。样例生成与导出后的逐帧核验分别运行。

2026-11-26 在 M1 Pro（32 GiB）上单次运行耗时 411.16 秒，四路合计 481.57 帧/秒，相当于实时的 4.38 倍。Edge 峰值 RSS 为 58.67 MiB，单个 ffprobe 子进程最高为 14.59 MiB。逐帧核对清单与 3,600 个片段索引，均为 198,000 帧，录像缺口为零。A 重置前后两帧的曝光时间相隔 33,333 µs；四路全程按曝光 UTC 排序，同一 UTC 按机位 ID 排序。[完整实测记录](benchmarks/2026-11-26-four-camera-30min.json)

## G1 双机位曝光对时

先用 `replay` 导出两路录像的 `frames.ndjson`。现场拍摄两路都能看到的共同闪光，由复核人员按同一选帧口径标注每次闪光对应的机位、片段号和 PTS。显式分周期的机位也要填写帧记录中的 `mediaEpoch`，例如 `"A": {"mediaEpoch":"after","segment":0,"pts":0}`。记录所有计划测量的闪光。某机位没拍到或无法确认时省略该机位，两路均缺测时保留空的 `frames`。报告使用帧清单里的曝光 UTC，现场采集需要记录时间戳来源和统一时钟的方式。

将下例保存为 `flashes.json`。样例只需要执行[用生成样例联调](#用生成样例联调)中的双路录像生成和 `replay` 命令。现场实拍标注的 `sampleSource` 填 `field`，生成样例填 `synthetic`。

```json
{
  "sampleSource": "synthetic",
  "cameras": ["A", "B"],
  "events": [
    {
      "id": "sample-pair",
      "frames": {
        "A": { "segment": 1, "pts": 35328 },
        "B": { "segment": 1, "pts": 32256 }
      }
    },
    {
      "id": "sample-missing-b",
      "frames": { "A": { "segment": 0, "pts": 0 } }
    }
  ]
}
```

```sh
swift run --package-path edge pitchtrace-edge audit-sync "$trial_dir/media/frames.ndjson" flashes.json > sync-report.json
```

同一命令支持 2–4 路，`cameras` 列出本次重放的所有机位。各事件保留原帧引用、曝光 UTC 和 `missingCameras`。全部机位都拍到时，`spanUs` 为最晚与最早曝光之差。双机位完整事件还保留有符号的 `signedSkewUs`（第二机位减第一机位）。报告用完整组的跨度计算最大值和最近秩 P95，`pairedCount` 计完整组，`unpairedCount` 计缺测组。没有完整组时省略偏差统计与阈值判定。P95 超过 5,000 µs 时仍正常输出报告。无效帧引用或重复使用同一帧会报错。

生成样例预期为 2 次标注，其中 1 次配对、1 次未配对，P95 和最大值均为 0 µs。现场评审还要核对实拍证据、样本量、未配对数、双视角可见率和独立检查点。[G1 放行条件](../docs/03-development-plan.md#3-按周实施与放行条件)

## G1 分区双视角可见率

`audit-visibility` 根据人工标注，统计各场区有多少次能从至少两个机位同时看清同一目标。先按「用生成样例联调」生成双路平铺录像并执行 `replay`，在同一终端从仓库根目录运行以下命令。测试图案的可见性和区域均为预设值。[样例标注](samples/visibility.json)

```sh
swift run --package-path edge pitchtrace-edge audit-visibility \
  "$trial_dir/media/frames.ndjson" edge/samples/visibility.json > "$trial_dir/visibility-report.json"
```

样例的 4 个计划样本应有 1 个满足双视角条件。`groups` 按区域与目标给出分子 `dualViewCount`、分母 `sampleCount` 和二者之比 `dualViewRate`。

| 场区 | 目标 | 双视角可见 / 计划样本 | 对应原帧 |
| --- | --- | --- | --- |
| 中圈 | 地滚球 | 1/2 | 一次两路同曝光，另一次两路均缺帧。 |
| 中圈 | 脚踝 | 0/1 | 两路同曝光，仅 A 能看清左脚踝。 |
| 远端禁区 | 高球 | 0/1 | 两路都能看清球，曝光相差 6,667 µs，超出样例的 5,000 µs 上限。 |

制作现场标注时，先约定场区名称、取样时刻和允许的最大曝光差 `maxSkewUs`（微秒），列出所有计划样本。`sampleSource` 填 `field`，`cameras` 填重放清单内全部 2–4 个机位。每个 `id` 唯一标识一次取样的一个目标，`region` 填场区，`target` 取 `groundBall`（地滚球）、`highBall`（高球）或 `ankle`（脚踝）。标脚踝时，各机位应看同一球员的同侧脚踝，可在 ID 中记下球员和左右侧。球与脚踝可以分别引用同一原帧。需单列拼抢或特定光照时，可按场景分别准备标注文件、生成报告。

在 `views` 内为有对应原帧的机位填写片段号、PTS，以及显式周期录像的 `mediaEpoch`。能看清指定目标填 `visible: true`，有原帧但目标被挡或无法辨认填 `false`。缺帧时省略该机位，所有机位均缺帧时保留 `"views": {}`。每个计划样本都进入该组的分母。任意两路可见帧的曝光 UTC 相差不超过 `maxSkewUs` 时，该样本计入分子一次。

报告的 `samples` 保留各机位原帧引用、人工判读、缺帧机位 `missingCameras` 和双视角判定 `dualView`。至少两路看清时，`closestVisibleSkewUs` 给出可见机位之间最小的曝光差，便于复核超窗样本。

设备采集与推理的设计见[边缘计算技术方案](../docs/02-technical-design.md#pitchtrace-edge)。
