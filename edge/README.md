# PitchTrace Edge

场迹的场边计算子项目。第一阶段 MVP 计划在 M6 Mac mini 上接入四路单摄，每个机位一台相机，以同步画面重建人球轨迹。当前可用 2–4 个机位的离线录像按曝光时刻重放、核对共同闪光、统计球和脚踝的双视角可见率，并按机位将原片和索引提交给本地 v2 网关。

从双机位实拍到四路试点的模块分工、逐周交付物和验收门槛见 [Edge 需求与开发计划](../docs/07-edge-development-plan.md)。

在 macOS 14 或更新版本使用 Swift 6.0 或更高版本。在仓库根目录构建和测试。

```sh
swift build --package-path edge
swift test --package-path edge --disable-xctest
```

在仅安装 Command Line Tools 的 Swift 6.4.2 环境，若测试提示 `plugin for module 'TestingMacros' not found`，可在上面的 `swift test` 命令末尾追加 `-Xswiftc -plugin-path -Xswiftc /Library/Developer/CommandLineTools/usr/lib/swift/host/plugins/testing`。

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

报告的 `samples` 保留各机位原帧、人工判读、缺帧机位 `missingCameras` 和双视角判定 `dualView`。有两路以上（含两路）看清时，`closestVisibleSkewUs` 给出其中最接近的一对曝光差，便于复核超窗样本。

设备采集与推理的设计见[边缘计算技术方案](../docs/02-technical-design.md#pitchtrace-edge)。
