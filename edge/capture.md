# 采集、曝光映射和重放

`capture` 为每台相机启动独立的 FFmpeg 进程。可从文件、RTSP 或 macOS AVFoundation 输入采集 1–8 路视频。默认以 H.264 编码，保留展示帧时序，以关键帧开始新的 fMP4 片段。对于关键帧间隔合适的 H.264 源可设置 `"videoMode": "copy"` 直接封装。采集需要 PATH 中的 `ffmpeg`，曝光核对还需要 `ffprobe`。默认编码使用 `libx264`。

现场相机暂定海康机器人 **MV-CS050-10GC-PRO**。Edge 已有直采通路：`camera-capture` 子进程取未压缩原图，用 VideoToolbox 编码 H.264，自行写出分段 fMP4 与逐帧曝光映射。支持模拟相机和链接 MVS SDK 的海康相机，MVS 取图尚待实机联调，见[相机直采](#相机直采mvs-与模拟相机)。首轮双机采集模式、带宽和对时验收见[相机开发基线](../docs/05-hardware-selection.md#相机开发基线)。

## 开始、预览与停止

从仓库根目录运行以下文件源样例。

```sh
trial_dir=$(mktemp -d)
cat > "$trial_dir/cameras.json" <<EOF
{
  "matchId": "training",
  "segmentSeconds": 2,
  "minFreeBytes": 1000000000,
  "cameras": {
    "A": {"type": "file", "source": "$PWD/edge/samples/preview-A.mp4", "videoMode": "copy", "mediaEpoch": "first"},
    "B": {"type": "file", "source": "$PWD/edge/samples/preview-B.mp4", "videoMode": "copy", "mediaEpoch": "first"}
  }
}
EOF
swift run --package-path edge pitchtrace-edge capture "$trial_dir/cameras.json" "$trial_dir/session"
```

文件和清单的相对路径均从配置文件所在目录解析。一个输出目录绑定一场比赛。省略 `mediaEpoch` 时程序生成新的媒体周期，明确指定时同名周期禁止覆盖。将机位替换为下面任一对象即可选择视频输入。

| 输入方式 | 单机位配置 |
| --- | --- |
| RTSP，经 TCP 连接 | `{"type":"rtsp","source":"rtsp://camera.local/stream","reconnectAttempts":3}` |
| Mac 摄像头，以视频设备序号取图 | `{"type":"avfoundation","source":"0:none","framerate":30,"videoSize":"1280x720"}` |

采集中可用支持 HLS 的播放器打开 `session/raw/<机位>/<周期>/preview.m3u8`。每个完成段由 FFmpeg 原子封口，其他机位的掉线、编解码失败和重连互相隔离。RTSP 遇到 5 秒网络超时会退出该路。实时输入意外结束时默认再尝试 3 次，每次使用独立新周期。可用 `reconnectAttempts` 调整。文件读至末尾即结束。Ctrl-C 或向采集进程组发送 SIGTERM 可通知各路封口、停止。也可以在另一个终端执行 `touch "$trial_dir/session/STOP"` 请求有序停止。下一次启动前移走这个标记；重新启动时使用新周期，已完成周期仍可复核。

`health.json` 给出正在录制的机位和累计段数、完成或失败的机位、磁盘空闲字节和更新时间。各活动机位均有已封口片段时，用最近一段实测码率估算剩余录像秒数 `remainingRecordingSeconds`。可用空间降到 `minFreeBytes` 时各路停止并以失败状态结束本次运行。每周期的 `status.json` 保留机位、开始／结束时刻、完成段数及失败原因。进程被强制终止时应以原片目录中已封口的 `.m4s` 为准，`.tmp` 是未提交的段。下一次在同一目录启动 `capture` 时，状态仍为 `recording` 且无进程持有的周期会被找回：临时文件移入该周期的 `.incomplete/`，`status.json` 改为 `interrupted` 并按已封口片段计数，直采 PTP 周期照常尝试登记，结果写入 `attached` 或 `attachError`。找回的周期列在 `health.json` 的 `recoveredPeriods` 中；仍有旧子进程在写的周期保持不动。

## 逐帧关联设备曝光

`raw/` 保存原片及预览列表。文件修改时间、RTSP 到达时刻和 AVFoundation 主机时钟均不足以证明某帧的实际曝光 UTC，只有设备侧或采集配套流水提供了可信的对应关系，才可把周期加入 `recordings/`。时钟未核验的录像可以单独播放。

准备 UTF-8 NDJSON 曝光映射文件。第一行指定同一比赛、机位、媒体周期、原片输出时基及曝光时钟来源。后续每一行对应一个展示帧，按片段号和 PTS 排列，`frameNumber` 可选。下面给出文件开头的格式，实际提交需覆盖整个周期。

```jsonl
{"matchId":"training","cameraId":"A","mediaEpoch":"first","timeBase":"1/15360","clockSource":"设备触发日志 trial-12，已映射到 UTC"}
{"segment":0,"pts":0,"captureUtcUs":"1795494597700000","frameNumber":42}
{"segment":0,"pts":512,"captureUtcUs":"1795494597733333","frameNumber":43}
```

`pts` 和 `timeBase` 对应**封装后的 fMP4 展示帧**。可将 `init.mp4` 和一段媒体连续输入 ffprobe 核对。下面的命令只产生媒体时序，UTC 仍须由设备侧逐帧提供。

```sh
period="$trial_dir/session/raw/A/first"
cat "$period/init.mp4" "$period/0.m4s" | ffprobe -v error -f mov -select_streams v:0 \
  -show_streams -show_frames -show_entries stream=time_base:frame=pts,duration -of json pipe:0
```

通过以下命令登记已经结束的周期。采集前已有完整设备映射时，可在该机位配置中同时填写 `mediaEpoch` 和 `exposures`（相对于配置文件的清单路径），程序在该机位录制结束后自动核对。重连后使用新的周期和映射。

```sh
swift run --package-path edge pitchtrace-edge attach-exposures "$trial_dir/session" A first A-exposures.ndjson
swift run --package-path edge pitchtrace-edge attach-exposures "$trial_dir/session" B first B-exposures.ndjson
swift run --package-path edge pitchtrace-edge replay "$trial_dir/session/recordings" "$trial_dir/media"
```

登记时逐段解码并核对时基、关键帧、PTS、帧数、曝光单调性及跨周期范围；核对通过后原子发布整个周期。重试相同映射会确认已有结果，不同映射会报告冲突。`recordings/<机位>/epochs/<周期>/clock.json` 保存该周期的曝光来源、首末 UTC 和映射摘要，媒体以硬链接复用同卷的原片字节。为后续核验请将已录制的原片视为只读。已有两个至四个成功登记的机位时可运行 `replay` 合并曝光序列。

## 相机直采（MVS 与模拟相机）

`"type": "mvs"` 与 `"type": "simulated"` 的机位不经 FFmpeg。`capture` 为每路启动 `pitchtrace-edge camera-capture <周期目录>`，读取设备送出的 Bayer RG 8 或 Mono8 原图，双线性去马赛克后用 AVAssetWriter（VideoToolbox H.264，无 B 帧，时基 1/90000）写出 `init.mp4`、`<n>.m4s` 和 `preview.m3u8`。每段封口后，按展示顺序把该段各帧的 `segment`、`pts`、`captureUtcUs` 和设备 `frameNumber` 追加到同目录的 `exposures.ndjson`。PTS 取曝光中心相对首帧的实际间隔，设备丢帧在 PTS、UTC 和帧号中都留下缺口。必须用 `pitchtrace-edge capture` 启动，独立运行 `capture.py` 找不到子进程程序。

```json
{
  "matchId": "training",
  "segmentSeconds": 2,
  "cameras": {
    "A": {"type": "mvs", "source": "相机序列号", "width": 1920, "height": 1080, "framerate": 30,
          "clock": {"mode": "ptp-utc", "timestampEvent": "exposureStart"},
          "mvs": {"offsetX": 264, "offsetY": 484, "exposureUs": 2000}},
    "B": {"type": "simulated", "source": "pattern", "width": 1920, "height": 1080, "framerate": 30,
          "clock": {"mode": "ptp-utc"}, "simulate": {"realtime": true}}
  }
}
```

| 字段 | 内容 |
| --- | --- |
| `width`、`height`、`framerate` | 编码尺寸与帧率，默认 1920 × 1080、30 fps。宽高须为偶数，原图尺寸在采集中改变即结束本周期。 |
| `pixelFormat` | `BayerRG8`（默认）或 `Mono8`。 |
| `bitrate` | H.264 平均码率，默认 8000000 bit/s。 |
| `clock.mode` | `ptp-utc`：设备时间戳为锁定后的 PTP 时间，减去 `taiUtcOffsetSeconds`（默认 37）得 UTC。开始时未锁定或采集中失锁都结束本周期。`host-fit`：用前 15 帧中主机收帧与曝光中心的最小差值对齐 UTC，整周期冻结该偏移，这 15 帧不录入。 |
| `clock.timestampEvent` | 设备时间戳对应的事件。`exposureStart`（默认）加半个曝光时长得曝光中心，`exposureCenter` 原样使用。海康机型待实机核对。 |
| `mvs` | `offsetX`、`offsetY`（省略时 ROI 居中）、`exposureUs`、`gainDb`、`packetSize`、`ptpLockTimeoutSeconds`。曝光与增益省略时保留相机当前设置。 |
| `simulate` | 仅模拟相机：`frames` 送完即正常结束，`realtime: false` 尽快送帧，`startUtcUs` 固定时钟起点，`dropFrames`、`clockResetAtFrame`、`ptpUnlockAtFrame`、`failAtFrame` 注入故障。 |

`ptp-utc` 周期结束后自动执行与 `attach-exposures` 相同的逐段核对并登记到 `recordings/`。`host-fit` 的清单头行以「低精度」开头，周期只留在 `raw/`，不进入精确三维融合；确需使用时由场务手工登记。时间戳回退（按时钟重置处理）、失锁、5 秒收不到图像或设备断开时，已封口的片段和曝光行保持有效并照常登记，`capture` 按 `reconnectAttempts` 以新周期重连。

每个周期目录另有 `capture-stats.json`，记录收到与录入的帧数、编码器繁忙丢帧 `encoderDrops`、设备帧号缺口 `deviceFrameGaps`、最大传输延迟、时钟状态与失败原因，`status.json` 的 `capture` 字段同步这份计数。在 M1 Pro 的 Release 构建上，单路模拟 1080p 去马赛克加编码约 80 fps，峰值内存约 52 MB。

### 接入 MVS SDK

在场边机安装海康 MVS V5.0.0 macOS 运行时（`MvCameraControl.framework`，位于 `/Library/Frameworks`，支持 arm64 与 x86_64）后照常执行 `swift build --package-path edge`。安装包、C 开发指南和示例从海康机器人官网下载中心获取，仓库不收录；本地参考副本可放在已加入 `.gitignore` 的 `docs/sdk/`。`Package.swift` 检测到 framework 头文件时编入 MVS 取图层，以弱链接方式链接 SDK；没装 SDK 的机器仍可启动，使用 `"type": "mvs"` 时报错。framework 装在别处时设置 `PITCHTRACE_MVS_SDK=<所在目录>`，设为 `off` 则不链接。GitHub Actions 发布的预览版不含 MVS，需在装有 SDK 的 Mac 上自行构建。SDK 安装包附带的 GigEVision Image Filter 网络扩展可降低 GigE 取图的 CPU 占用，安装方法见 SDK 开发手册。

`camera-capture` 打开相机后依次执行：

1. 按 `source` 中的序列号在 GigE 与 USB3 设备中查找相机，以独占方式打开，并登记断线回调。
2. GigE 设备设置包长：使用 `mvs.packetSize`，省略时取 SDK 探测的最佳包长。
3. 关闭触发模式，设置像素格式、宽高和偏移，启用帧率控制并设定帧率。给出 `exposureUs` 或 `gainDb` 时关闭相应的自动调节并写入该值。省略 `offsetX`、`offsetY` 时按节点步进把 ROI 居中。
4. 读取 `GevTimestampTickFrequency` 作为时间戳频率；USB3 设备按纳秒计时。
5. `ptp-utc` 模式开启 IEEE 1588（节点 `GevIEEE1588`，新固件为 `PtpEnable`），等待 `GevIEEE1588Status`（或 `PtpStatus`）成为 `Slave` 或 `Master`，最长 `mvs.ptpLockTimeoutSeconds` 秒（默认 30）。
6. 开始取图，每帧读取帧号、64 位设备时间戳和帧水印中的曝光时长；水印没有曝光时长时使用第 3 步读回的 `ExposureTime`。

有丢包的帧直接丢弃，计入 `deviceFrameGaps`。`ptp-utc` 换算出的曝光 UTC 与主机收帧时刻相差超过 5 秒时结束本周期，这通常说明相机自任主时钟而主时钟未授时到 UTC。清单头行记录型号、序列号、实际 ROI、设定帧率、相机报告的 `ResultingFrameRate` 和时间戳频率。

目前相机以自由运行方式出图，各机位的曝光相位不对齐，只靠 PTP 时间戳记录真实曝光时刻。按预定时刻触发（Action Command）尚未接入。以下几项须实机确认：

- 设备时间戳对应的曝光事件，确认后填 `timestampEvent`。
- `MV-CS050-10GC-PRO` 交付固件的 PTP 节点名与锁定角色。
- 帧水印是否带曝光时长。
- 两路 1080p30 的持续帧率与丢包。

已用 MVS V5.0.0 macOS SDK 编译，并在无相机的机器上验证了 SDK 初始化、设备枚举和「未找到相机」报错。

### 回归

```sh
swift build --package-path edge
python3 -B -m unittest edge/Tests/Integration/test_native_capture.py
```

用例以模拟相机覆盖双路 PTP 采集、自动登记与 `replay` 合并、`host-fit` 预热、时钟重置与失锁的残段登记、STOP 标记封口、配置拒绝，以及未链接 SDK 时的报错。

## 录制期间逐段分析

直播流水线可以边录边核对已封口的 `.m4s`。曝光清单可通过 `exposures` 指定，或由设备配套程序写入 `raw/<机位>/<周期>/exposures.ndjson`。使用与整周期登记相同的头行和逐帧格式，完整记录以换行符结束，只追加新记录。程序核对映射与展示帧后，依次提交媒体初始化文件、媒体段和 v2 索引；最后出现的 `segments/<n>.json` 表示该段已可解码、推理与上行。

每个机位在内存中最多保留一段待推理的原帧引用。曝光映射尚未到齐的机位可在等待窗口内补齐；窗口届满后，其余机位继续产出。迟到媒体仍按原始曝光存档，已经向前推进的直播时间轴保持单调。`stream-health.json` 记录本次运行已迟到的帧数、缺映射的周期和核验失败原因。`frames.ndjson` 记录实际选入直播的帧和顺序，每个发布索引均配有下一条曝光记录的字节位置和可用的设备帧号，重启时先复播日志，再从已提交段补上中断窗口所缺的行。录制原件暂时离线时，也能从已发布媒体和日志恢复原有顺序。磁盘写满会中止分析，腾出空间后可在原运行目录重试。采集停止时仍会处理已封口且有完整映射的剩余片段。

## 文件源回归

```sh
swift build --package-path edge
python3 -B edge/Tests/Integration/test_capture.py
```

回归使用 30 fps 的 H.264 样片，对两台虚拟机位分别录制两个媒体周期，核对 480 帧的重放结果。另一路文件缺失时检查健康机位的原片和预览，同时检查缺少曝光、同周期冲突、跨周期重叠、目录覆盖。流式回归还会在索引提交后立即终止进程，模拟下一段索引写入时磁盘满，并在缺少录制原件时核对恢复结果。RTSP 和 AVFoundation 的设备画质、传输稳定性与硬件曝光精度需要目标设备测量。
