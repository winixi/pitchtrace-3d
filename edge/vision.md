# Edge 单视角观测

`pitchtrace-edge vision-worker [模型路径]` 在 macOS 14+ 上接收连续 JPEG 帧。调用方在每张原尺寸、编码方向朝上的 JPEG 前写入 4 字节大端无符号长度，读回一行 UTF-8 JSON，再发送下一帧。最后在两帧之间关闭标准输入。解码失败、包截断、模型错误和 Vision 执行失败会结束进程并在标准错误说明原因。单个 JPEG 上限 32 MiB、48 百万像素。

`imageSize` 为编码像素宽高，像素原点在左上。`players[].box` 按左、上、宽、高记录系统 Vision 全身人框。在该框内单独检测姿态，只有检测到一个人体时才取其可见关节。`joints` 按人体左右侧用 `leftShoulder`、`leftElbow`、`leftWrist`、`leftHip`、`leftKnee`、`leftAnkle` 及对应右侧名称记录二维像素，另可包含 `nose`、`neck`、`root`。当脚踝可见、靠近人框下沿且框底在画面内时，`foot` 估计鞋底位置，脚被遮挡或出画时省略。人框和脚点对远端小目标的误差应由回放样本评估。

躯干区域与其他人框分开时，对其裁片执行系统文字识别。候选限定为一至两位数字，无法确定唯一候选时省略 `number`。`shirtRGB` 为相同区域采样的 sRGB 三通道颜色值（0–255），可供队色比对。球队、名单身份与号码的时序确认由后续关联模块处理。

提供本地 Core ML 带非极大值抑制的对象检测模型（`.mlmodelc`、`.mlpackage` 或 `.mlmodel`）可启用球检测。模型须接受图像输入并经 Vision 输出带类别的检测框。标签支持 `sports ball`、`soccer ball`、`football`、`ball`，取置信度达到 0.35 的最高候选框中心。未提供模型、无球或置信度不足时，`ball` 为 `null`。模型来自运行者指定的本地文件。单图观测描述球的二维像素，后续融合结合标定、曝光和多视角证据决定三维位置。

已用 Apple 提供的 YOLOv3TinyInt8LUT 权重验证原生模型编译和推理。权重独立提供，可在比赛配置的 `detector.model` 中指定本地路径。[Apple 模型目录](https://developer.apple.com/machine-learning/models/)
