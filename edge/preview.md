# PitchTrace Edge 离线 CLI 预览版

此版本供 Apple Silicon Mac 离线处理多机位录像，最低系统要求为 macOS 14。发布包内含已编译的 `pitchtrace-edge`、SwiftPM 资源包和双机位样例生成脚本。

## 下载与校验

将本次发布的 `macos-arm64.zip` 压缩包（文件名含版本号）和对应的 `.zip.sha256` 文件下载到同一个空目录，在该目录执行以下命令。

```sh
shasum -a 256 -c pitchtrace-edge-v*-macos-arm64.zip.sha256
unzip -q pitchtrace-edge-v*-macos-arm64.zip
cd pitchtrace-edge-v*-macos-arm64
```

移动安装目录时，请将 `pitchtrace-edge` 与 `PitchTraceEdge_PitchTraceEdge.bundle` 放在一起。预览包未经 Apple 公证，首次打开时可能受到 Gatekeeper 限制。

## 运行双机位样例

终端中的 `python3` 需要是 Python 3.10 或更高版本，并能导入 NumPy 和带 FFmpeg 后端的 OpenCV。`PATH` 中还需有 `ffmpeg`（含 `libx264` 编码器）和 `ffprobe`。以下命令在解压后的目录中运行。

```sh
trial_dir=$(mktemp -d)
python3 -B samples/generate-match.py "$trial_dir/input"
./pitchtrace-edge run-match "$trial_dir/input/match.json" "$trial_dir/run"
./pitchtrace-edge scene-evidence "$trial_dir/run/scenes.sqlite" training video 5
```

样例生成两秒合成录像。预期两路合计导出 40 帧、录像缺口为 0，SQLite 中保存 20 个场景快照。最后一条命令会列出场景序号 5 对应的两路原始帧。样例使用颜色检测，如需启用 Core ML 人球联合检测，请另行准备模型权重及清单。

## 已知限制

- 目标机的四路 90 分钟实时吞吐和现场曝光对时仍待验收。
- 远端小球和遮挡可能造成三维轨迹长时间缺测，现场三维位置精度仍需独立验证。
- Web UI 显示 `capture` 目录的通道状态和预览，可开始或结束采集，默认只在 127.0.0.1 监听。推理积压、帧率和曝光偏差尚未接入，控制接口无认证，`--bind 0.0.0.0` 时只应在可信局域网使用。
