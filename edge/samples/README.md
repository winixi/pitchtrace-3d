# Edge 测试录像

直接打开两个 4 秒的 H.264 样片即可看画面。A、C 使用左上角有红色标记的 30 fps 测试图，B、D 使用带蓝色标记的 25 fps 镜像测试图。[A 路短片](preview-A.mp4) · [B 路短片](preview-B.mp4)

## 打开四路 30 分钟录像

在仓库根目录运行下面的命令。需要 Node.js 22.13+、带 `libx264` 的 FFmpeg 和 ffprobe，完整样例约占 0.5 GiB。

```sh
node edge/samples/prepare-videos.mjs
```

命令会打印四个 MP4 的绝对路径。用 QuickTime 或 VLC 打开其中一个即可播放、暂停和拖动。完整录像保存在本机，Git 忽略生成目录。换一份工作目录时可重新运行同一命令，已有样例会复用。

| 本地文件 | 内容 |
| --- | --- |
| `edge/samples/four-camera-30min/A.mp4`、`C.mp4` | 每路 30 分钟、54,000 帧，循环 A 路样片 |
| `edge/samples/four-camera-30min/B.mp4`、`D.mp4` | 每路 30 分钟、45,000 帧，循环 B 路样片 |
| `edge/samples/four-camera-30min/recordings/` | Edge 输入，包含 A–D 的原片分段、逐帧曝光 UTC 和机位帧号 |

A 的原始媒体时钟在 15:00 重置，前后两个周期分别保存在 `recordings/A/epochs/before/` 和 `after/`。供观看的 A.mp4 连续播放这两个周期。核对时钟重置、跨周期帧排序或曝光定位时应使用分段原片与索引。

## 用同一批录像运行 Edge

先运行上面的生成命令，再从仓库根目录执行。每次重放写入一个新的输出目录。

```sh
run_dir=$(mktemp -d)
swift run -c release --package-path edge pitchtrace-edge replay \
  edge/samples/four-camera-30min/recordings "$run_dir/media" --metrics
node edge/Tests/Integration/verify-long-replay.mjs "$run_dir/media"
```

四路预期共 198,000 帧，A 的两个周期各 27,000 帧。计量字段和已完成的基准测试见 [Edge 重放说明](../README.md#四路-30-分钟录像重放基准)。
