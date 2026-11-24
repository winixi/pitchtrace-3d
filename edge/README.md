# PitchTrace Edge

场迹的场边计算子项目。第一阶段 MVP 计划在 M6 Mac mini 上接入四路单摄，每个机位一台相机，以同步画面重建人球轨迹。当前为 SwiftPM 可执行项目骨架，运行后立即正常退出。

在仓库根目录使用 Swift 6.0 或更高版本构建并运行。

```sh
swift build --package-path edge
swift run --package-path edge pitchtrace-edge
```

设备采集与推理的设计见[边缘计算技术方案](../docs/02-technical-design.md#pitchtrace-edge)。
