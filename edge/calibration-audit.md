# 机位标定巡检与几何覆盖

`audit-calibration` 复用既有外参报告中的独立检查点。复核员重新在原片上标记这些静态点，把巡检像素和原标定的预期投影逐点比较。运行命令后，标准输出为 JSON。

```sh
swift run --package-path edge pitchtrace-edge audit-calibration calibration-report.json inspection.json > inspection-report.json
```

以下清单可配合仓库双机位合成外参报告复现 A 机位三点水平均偏移约 12 像素的情形。检查点 ID 对应报告的 `checkPoints`；省略某个点的某机位表示该路未标注。可选的 `frames` 将本次像点关联到原片，字段为片段号、PTS、可选的媒体周期及曝光 UTC。

```json
{
  "sampleSource": "synthetic",
  "captureUtcUs": "2000000",
  "maxReprojectionPx": 3,
  "calibrationId": "pitch-isolate-a",
  "disabledCameras": [],
  "checks": [
    {"id":"ground-1","pixels":{"A":[918.69428,536.61655],"B":[932.96726,563.88548]}},
    {"id":"ground-2","pixels":{"A":[1129.39628,548.40238],"B":[986.37129,499.22236]}},
    {"id":"pole-1","pixels":{"A":[890.91048,461.65973],"B":[915.11315,488.21902]}}
  ],
  "grid": {"lengthM":40,"widthM":30,"cellSizeM":2,"heightM":0.11,"minRayAngleDeg":1,"ballDiameterM":0.22}
}
```

默认单点允许残差为 3 像素，可按本次图像分辨率和人工点选误差调整。每机位少于三个不共线的已知点时给出 `insufficientEvidence`。证据充分、全部残差达标给出 `consistent`，严格多数超限给出 `suspectedMovement`，其余给出 `review`。对于按旧位姿落在机后的已标注点，记录缺失的预期像素并计一次超限。姿态差异、焦距变化、误标点位都可能造成残差，需要保留原片复查。

报告按机位列出原像点、预期像点、残差与可用的原帧引用。`suspectedCameras` 是本次发现需隔离复核的机位。若提供新的 `calibrationId` 和巡检时刻 `captureUtcUs`，且发现尚未隔离的异常机位，`proposedCalibration` 给出可追加到比赛配置 `calibrations` 的区间。清单的 `disabledCameras` 应填当前已隔离的机位，建议区间会一并保留。把建议加入配置前核对生效时间与全场版本唯一性，完成复标后通过新版本恢复。

`coverage.cells` 按 Z 从小到大逐行、同一行 X 从小到大排列。每格取固定高度 `heightM` 的格中心，排除已隔离和本次疑似移动的机位，逐机位检查正深度和带畸变的投影是否在原图内。`viewCount` 是可投影的视角数，`pairs` 列出交会锐角达到 `minRayAngleDeg` 的双路组合。`dualViewCells` 是至少有一组合格的格数，每格的 `expectedDiameterPx` 按针孔近似 `fx × ballDiameterM / 相机深度` 估算。剩余场地不足整格时取边缘小格的实际中心。一份报告可计算最多 10,000 格。

几何覆盖按原标定预测画面范围。远端画质、光照、球员遮挡、在画面边缘被裁掉的球体部分以及曝光是否同步均需用原片核对。缺少巡检点的机位会标注证据不足，其预测覆盖仍可用于规划。原片实测双视角可见率由 `audit-visibility` 统计。

```sh
python3 -B edge/Tests/Integration/test_audit_calibration.py
```
