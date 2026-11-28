# 采集中的场景重建与原片补传

在采集命令持有比赛目录、已创建 `match.json` 后，可启动另一进程跟随各机位。比赛配置沿用离线重建所用的标定、曝光窗口、检测器与名单，另指定 `captureConfig` 指向这次采集的配置文件。运行目录与采集目录分别存储。

```sh
swift run --package-path edge pitchtrace-edge stream-match match.json ./capture-run ./stream-run
```

| 比赛配置 | 作用 |
| --- | --- |
| `captureConfig` | 相对于比赛配置文件的采集清单路径，比赛 ID 须与采集目录一致。 |
| `streamWaitSeconds` | 有路缺段时等待其曝光映射的秒数，默认 2 秒。超窗后先处理已到视角，迟到原片仍可提交网关。 |
| `gateway` | 可选的 HTTP(S) 网关基址。省略时执行本地归档，随后可填入地址并重跑完成补传。 |
| `backfillBytesPerSecond` | 历史场景补传速率，默认 0 表示不限速。媒体交付由独立线程完成。 |
| `deviceTokenFile` | Live 签发的设备凭据文件，相对比赛配置目录，须为 0600。也可用环境变量 `PITCHTRACE_DEVICE_TOKEN`，后者优先。凭据附在场景与原片的全部写入请求上，不进入运行记录。 |
| `gatewayAllowHttp` | 默认 false：凭据只经 HTTPS 或回环地址发送。隔离的现场局域网用明文 HTTP 时设为 true。 |
| `mediaBytesPerSecond` | 原片上传速率（字节/秒），计入 init、片段和索引三件，默认 0 表示不限速。按令牌桶在每次请求前等待，单次突发不超过一个请求的大小。与场景补传各自限速，合计带宽需分别留出。 |

## 启动、积压和恢复

跟随线程核对封口 H.264/fMP4 段和同周期设备曝光清单，将原片与 v2 索引保存到 `media/<机位>/epochs/<周期>/`。每个机位的索引出现后即可登记上传，原始行复制到 `media/frames.ndjson` 后才进入推理。分析线程按最初记下的曝光顺序读取日志。推理较慢时积压保存在磁盘，各机位继续录制。时钟映射缺失、部分写入或机位迟到的具体记录见 `media/stream-health.json`。

`scenes.sqlite` 保存逐帧场景、证据、运行来源和按网关分开的提交回执。场景上传线程优先处理最新快照，媒体上传线程逐段提交初始化文件、带摘要的媒体段和曝光索引。临时网络错误按 0.5–15 秒间隔退避。任一线程遇到 409、422 等永久性拒绝时保留待办和诊断，另一线程与本地归档继续。采集结束后排空已封口且有曝光映射的段。网关可用时等到待办全部确认，仍不可用时以非零状态退出。

原命令或机器中断后，在相同的比赛、采集和运行目录重新执行。程序根据持久化的帧顺序和已保存的二维观测恢复跟踪状态，并核对归档中的既有场景与证据。重复的网关回执按目标地址隔离。相同运行目录必须对应原先的采集根目录、比赛周期、曝光配置及推理版本。另行复算时使用新的场景周期和目录。

`stream-state.json` 在运行中约每半秒更新，结束或失败时写入最后一份状态。`pitchtrace-edge web-ui --run-dir <运行目录>` 会读取它，显示积压与待上传数，并记录最近 5 分钟的队列年龄。

| 字段 | 含义 |
| --- | --- |
| `committedRows`、`consumedRows`、`backlogRows` | 本次打开日志后已登记的曝光行数、进入分析分组的行数及二者之差。各机位分别计数。 |
| `backlogExposureSeconds` | 最老待消费行与最新已登记行之间的曝光时间差。无待消费行时为零。 |
| `frames`、`lastSceneUtcUs` | 已处理的场景帧数及最近一帧的原始曝光时刻。 |
| `processedFramesPerSecond` | 本次启动以来处理的场景数除以单调时钟耗时，包含恢复时核对的已有帧。与发布目标频率、积压变化一起观察。 |
| `status`、`pendingDelivery` | 本地运行状态和退出时仍待处理的上行错误。 |
| `pendingUploads` | 配置网关时给出，`scenes` 为该网关尚未确认的场景帧数，`mediaSegments` 为尚未三件全部确认的媒体段数。归档读取失败时为 `null`。 |
| `paused`、`pausedSinceUnix` | 运行目录存在 `PAUSE` 标记时 `paused` 为 true，后者为线程开始等待的 Unix 秒数，未暂停时为 `null`。 |
| `degradation` | 配置 `detector.degradation` 时给出：当前降级级别 `level`、最近一组的曝光积压 `backlogSeconds` 和各级已处理的组数 `framesByLevel`。 |

## 暂停与恢复

在运行目录放置 `PAUSE` 文件（`touch <运行目录>/PAUSE`，或 Web UI 的 Pause 按钮）即暂停推理和上行；删除该文件即恢复。暂停期间采集与原片跟随照常，新行继续登记，积压保存在磁盘，恢复后按原顺序处理。分析与上行线程在当前帧或当前请求完成后才停下，`stream-state.json` 的 `paused` 表示已请求暂停，`pausedSinceUnix` 为线程实际开始等待的时刻。采集结束时若仍处于暂停，`stream-match` 会一直等待恢复，不会自行退出。可在首次启动前放置 `PAUSE`，以暂停状态开始。

## 过载降级

推理长期跟不上输入时，可在比赛配置的 `detector` 中加入 `degradation`，按「当前时刻减本组曝光时刻」的积压秒数逐级减负。只在 `stream-match` 中生效；`process-match`、`run-match` 复算总是完整观测，便于与直播结果对照。

```json
{
  "detector": {
    "backend": "vision",
    "modelManifest": "models/yolo11n.json",
    "degradation": {"ocrAfterSeconds": 30, "poseAfterSeconds": 90, "secondaryAfterSeconds": 180,
                    "poseRadiusFraction": 0.25, "secondaryCameras": ["C", "D"]}
  }
}
```

| 级别 | 触发条件 | 处理 |
| --- | --- | --- |
| 1 | 积压 ≥ `ocrAfterSeconds` | 全部机位停读号，观测记 `ocrSkipped: true`。 |
| 2 | 积压 ≥ `poseAfterSeconds` | 只为离本机位最近 0.3 秒内二维球心不超过 `poseRadiusFraction` × 原图宽的人估计姿态。其余人记 `poseSkipped: true`，不带关节；人框完整落在画面内时用框下沿中点近似鞋底，记 `footSource: "box"`。没有近期球心时本帧不估计姿态。 |
| 3 | 积压 ≥ `secondaryAfterSeconds` | 不再解码和推理 `secondaryCameras` 列出的机位，按缺机位融合，至少保留一路。 |

三个门槛须递增，可省略任一级；省略的级别与下一级同时触发。积压降到当前级别门槛的一半以下才回退一级，避免在门槛附近反复切换。降级组的复核证据记录 `degraded`（级别、积压秒数、被跳过的机位），原片照常录制和上行，赛后可用 `process-match` 以新的 `streamEpoch` 完整复算。降级状态目前只写入本地证据和 `stream-state.json`，场景快照契约未增加字段。

原片待办按段登记的先后发送，各机位交替推进。同一网关、机位和媒体周期的 `init.mp4` 获 202 后记入本地回执，后续片段不再重传；重启后沿用回执。网关归档被清空时，请换用新的网关地址或新的运行目录重新登记。

运行 `swift build --package-path edge` 后，验证命令为 `python3 -B edge/Tests/Integration/test_stream_match.py`；原片顺序、init 去重与限速另见 `test_media_queue.py`。回归在两个机位分段到达时先让网关返回 503，检查原片和场景仍然落盘，再恢复真实本地网关，核对历史快照、按曝光 UTC 定位的原片，以及第三次运行的幂等回执。
