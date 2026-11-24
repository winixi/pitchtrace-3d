# PitchTrace Edge

场迹在 Mac mini 上的边缘计算子项目。当前为 SwiftPM 可执行项目骨架，运行后立即正常退出。

在仓库根目录使用 Swift 6.0 或更高版本构建并运行。

```sh
swift build --package-path edge
swift run --package-path edge pitchtrace-edge
```

设备采集与推理的设计见[边缘计算技术方案](../docs/02-technical-design.md#pitchtrace-edge)。
