# 离线比赛配置和标定时间线

每场比赛用一份 JSON 配置绑定曝光时钟、发布周期、名单以及依次生效的标定报告。以下例子以两机位 A、B 为例。保存各次 `calibrate` 命令实际输出的报告，再将 `report` 填为相对比赛配置文件的路径。

```json
{
  "matchId": "training-01",
  "streamEpoch": "replay-v1",
  "sampleSource": "synthetic",
  "startUtcUs": "1795494597700000",
  "frameIntervalUs": 100000,
  "maxSkewUs": 5000,
  "roster": [
    {"rosterId": "home-7", "teamId": "home", "number": 7},
    {"rosterId": "away-7", "teamId": "away", "number": 7}
  ],
  "calibrations": [
    {"fromUtcUs": "1795494597700000", "calibrationId": "opening", "report": "opening-report.json"},
    {"fromUtcUs": "1795494657700000", "calibrationId": "a-moved", "report": "opening-report.json", "disabledCameras": ["A"]},
    {"fromUtcUs": "1795494717700000", "calibrationId": "a-restored", "report": "new-report.json"}
  ]
}
```

`startUtcUs` 是比赛零时刻。所有 UTC 字段都用 Unix 微秒十进制字符串。比赛和流周期沿用 v2 标识，修改输入、模型或参数后再次发布不同场景时，另取 `streamEpoch` 以便保留原计算结果。名单可以是空数组。每条名单记录使用唯一 `rosterId`，同一号码可以属于不同球队，未知号码可填 `null`。

## 生效与隔离

`calibrations` 按 `fromUtcUs` 严格递增。每个条目从该曝光时刻开始生效，至下一条目生效前，最后一版持续生效。首版覆盖比赛开始时刻。每次换版或变更隔离机位，都分配唯一 `calibrationId`。每个场景的版本可经配置条目追溯到原报告内的 `calibrationId`、相机内外参以及镜头版本。同一配置中，同一原报告版本对应的机位参数必须一致。报告需来自与比赛相同的 `sampleSource`（`synthetic` 或 `field`）。

示例中，第 60 秒起 A 改为隔离，B 继续沿用原报告。第 120 秒起 A、B 使用新报告。隔离保留原帧，融合只取得有效机位的参数。所有机位都在 `disabledCameras` 中时仍可产生含曝光时间和标定版本的组。有效机位的原图分辨率必须与当时的报告相同，选自重放清单的原片在解码后核对尺寸。

## 取样与缺帧

`frameIntervalUs` 是目标采样间隔，默认 100,000 微秒。`maxSkewUs` 是同组最晚与最早曝光允许相差的最大值，默认 5,000 微秒。以原片曝光顺序扫描，每组在每个机位最多取一帧。组内最早曝光决定其采样时段和输出时间。两路若分别落在时段分界前后一微秒，仍可组成同一组。标定版本变化时立即另起一组。

同一个时段有多个候选时，先选有效机位最多的一组，然后依次比较总机位数和曝光跨度，跨度较小者优先。都相同时取先形成的一组。相邻输出的原帧不会复用。某路缺片时其余机位继续；有原帧但画面空场的观测行保留。赛前预录画面略过。所有机位均无曝光的时段由原片及录像缺口记录追溯。

从仓库根目录运行以下回归，可复验临界时刻、移机隔离与复标、时段两侧的同步曝光、分辨率校验以及单路缺帧。

```sh
python3 -B edge/Tests/Integration/test_match_config.py
```
