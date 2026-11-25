# PitchTrace Edge

场迹的场边计算子项目。第一阶段 MVP 计划在 M6 Mac mini 上接入四路单摄，每个机位一台相机，以同步画面重建人球轨迹。当前支持双机位离线录像输入、曝光时间重放和 v2 媒体导出。

在 macOS 14 或更新版本使用 Swift 6.0 或更高版本。在仓库根目录构建和测试。

```sh
swift build --package-path edge
swift test --package-path edge --disable-xctest
```

## 双机位离线录像输入

运行时需要 PATH 中的 `ffprobe`，用于读取 H.264/fMP4 中实际解出的展示帧、PTS、时基及帧时长。输入目录包含两个以机位 ID 命名的子目录，每个机位在一次运行中使用一个媒体周期。

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

结果按机位保存原始 `init.mp4`、`segments/{n}.m4s` 及符合 [v2 契约](../schemas/v2/media-index.schema.json)的 `segments/{n}.json`。`frames.ndjson` 按曝光 UTC、同曝光时按机位 ID 排序，每行保留机位、片段、PTS、时基、时长及可用的原始帧号。`gaps.txt` 记录片段号、展示时间或原始帧号的缺口。缺曝光映射和时间倒退会使命令失败，成功后才发布整个输出目录。

## 用生成样例联调

样例需要 Node.js 22.13+、FFmpeg（带 `libx264`）和 ffprobe。先在一个终端启动 [Live 网关](../live/server/README.md#启动本地直播接收服务)，再从仓库根目录执行以下命令。

```sh
trial_dir=$(mktemp -d)
node live/server/tools/review-sample.mjs --recordings "$trial_dir/recordings"
swift run --package-path edge pitchtrace-edge replay "$trial_dir/recordings" "$trial_dir/media"
node live/server/tools/review-sample.mjs http://127.0.0.1:8766 review-edge-01 --media-dir "$trial_dir/media"
```

打开 [review-edge-01](http://127.0.0.1:8766/?match=review-edge-01)，进入「归档回放」，在 `00:25.000` 暂停并切换两路同步原片。两路分别使用 30 fps 和 25 fps，预期命中同一预设曝光 UTC。`00:01.200` 可验证缺片提示。

设备采集与推理的设计见[边缘计算技术方案](../docs/02-technical-design.md#pitchtrace-edge)。
