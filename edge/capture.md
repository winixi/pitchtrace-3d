# 采集、曝光映射和重放

`capture` 为每台相机启动独立的 FFmpeg 进程。可从文件、RTSP 或 macOS AVFoundation 输入采集 1–4 路视频。默认以 H.264 编码，保留展示帧时序，以关键帧开始新的 fMP4 片段。对于关键帧间隔合适的 H.264 源可设置 `"videoMode": "copy"` 直接封装。采集需要 PATH 中的 `ffmpeg`，曝光核对还需要 `ffprobe`。默认编码使用 `libx264`。

现场相机暂定海康机器人 **MV-CS050-10GC-PRO**。计划新增 MVS 采集适配，将设备帧号、曝光 UTC 和编码后的 PTS 写入下述逐帧映射。当前 `capture` 尚未接入 MVS。首轮双机采集模式、带宽和对时验收见[相机开发基线](../docs/05-hardware-selection.md#相机开发基线)。

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

`health.json` 给出正在录制的机位和累计段数、完成或失败的机位、磁盘空闲字节和更新时间。各活动机位均有已封口片段时，用最近一段实测码率估算剩余录像秒数 `remainingRecordingSeconds`。可用空间降到 `minFreeBytes` 时各路停止并以失败状态结束本次运行。每周期的 `status.json` 保留机位、开始／结束时刻、完成段数及失败原因。进程被强制终止时应以原片目录中已封口的 `.m4s` 为准，`.tmp` 是未提交的段。

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

## 录制期间逐段分析

直播流水线可以边录边核对已封口的 `.m4s`。曝光清单可通过 `exposures` 指定，或由设备配套程序写入 `raw/<机位>/<周期>/exposures.ndjson`。使用与整周期登记相同的头行和逐帧格式，完整记录以换行符结束，只追加新记录。程序核对映射与展示帧后，依次提交媒体初始化文件、媒体段和 v2 索引；最后出现的 `segments/<n>.json` 表示该段已可解码、推理与上行。

每个机位在内存中最多保留一段待推理的原帧引用。曝光映射尚未到齐的机位可在等待窗口内补齐；窗口届满后，其余机位继续产出。迟到媒体仍按原始曝光存档，已经向前推进的直播时间轴保持单调。`stream-health.json` 记录本次运行已迟到的帧数、缺映射的周期和核验失败原因。`frames.ndjson` 记录实际选入直播的帧和顺序，每个发布索引均配有下一条曝光记录的字节位置和可用的设备帧号，重启时先复播日志，再从已提交段补上中断窗口所缺的行。录制原件暂时离线时，也能从已发布媒体和日志恢复原有顺序。磁盘写满会中止分析，腾出空间后可在原运行目录重试。采集停止时仍会处理已封口且有完整映射的剩余片段。

## 文件源回归

```sh
swift build --package-path edge
python3 -B edge/Tests/Integration/test_capture.py
```

回归使用 30 fps 的 H.264 样片，对两台虚拟机位分别录制两个媒体周期，核对 480 帧的重放结果。另一路文件缺失时检查健康机位的原片和预览，同时检查缺少曝光、同周期冲突、跨周期重叠、目录覆盖。流式回归还会在索引提交后立即终止进程，模拟下一段索引写入时磁盘满，并在缺少录制原件时核对恢复结果。RTSP 和 AVFoundation 的设备画质、传输稳定性与硬件曝光精度需要目标设备测量。
