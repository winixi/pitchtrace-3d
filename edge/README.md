# PitchTrace Edge

场迹的场边计算子项目。第一阶段 MVP 计划在 M6 Mac mini 上接入四路单摄，每个机位一台相机，以同步画面重建人球轨迹。当前可用 2–4 个机位的离线录像按曝光时刻重放、核对共同闪光，并按机位将原片和索引提交给本地 v2 网关。

从双机位实拍到四路试点的模块分工、逐周交付物和验收门槛见 [Edge 需求与开发计划](../docs/07-edge-development-plan.md)。

在 macOS 14 或更新版本使用 Swift 6.0 或更高版本。在仓库根目录构建和测试。

```sh
swift build --package-path edge
swift test --package-path edge --disable-xctest
```

## 离线录像输入

运行时需要 PATH 中的 `ffprobe`，用于读取 H.264/fMP4 中实际解出的展示帧、PTS、时基及帧时长。输入目录包含 2–4 个以机位 ID 命名的子目录，每个机位在一次运行中使用一个媒体周期。以下为双机位样例布局。

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

曝光文件每帧记录和录像同一时基下的 `pts`、UTC Unix 微秒十进制字符串 `captureUtcUs`，以及可选的相机原始帧号 `frameNumber`。例如 `{"frames":[{"pts":0,"captureUtcUs":"1795494597700000","frameNumber":42}]}`。实际输入须覆盖对应媒体段的每个展示帧。媒体时钟重置时按媒体周期拆分录像。

在已有父目录下指定一个新的输出目录。

```sh
swift run --package-path edge pitchtrace-edge replay "./录像目录" "./新输出目录"
```

结果按机位保存原始 `init.mp4`、`segments/{n}.m4s` 及符合 [v2 契约](../schemas/v2/media-index.schema.json)的 `segments/{n}.json`。导出时每路只保留当前段的帧记录，按曝光 UTC 归并成 `frames.ndjson`，同曝光时按机位 ID 排序。每行保留机位、片段、PTS、时基、时长及可用的原始帧号。`gaps.txt` 按机位记录片段号、展示时间或原始帧号的缺口。缺曝光映射和时间倒退会使命令失败，成功后才发布整个输出目录。

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

## G1 双机位曝光对时

先用 `replay` 导出两路录像的 `frames.ndjson`。现场拍摄两路都能看到的共同闪光，由复核人员按同一选帧口径标注每次闪光对应的机位、片段号和 PTS。记录所有计划测量的闪光。某机位没拍到或无法确认时省略该机位，两路均缺测时保留空的 `frames`。报告使用帧清单里的曝光 UTC，现场采集需要记录时间戳来源和统一时钟的方式。

将下例保存为 `flashes.json`。样例只需要执行上一节的录像生成和 `replay` 命令。现场实拍标注的 `sampleSource` 填 `field`，生成样例填 `synthetic`。

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

设备采集与推理的设计见[边缘计算技术方案](../docs/02-technical-design.md#pitchtrace-edge)。
