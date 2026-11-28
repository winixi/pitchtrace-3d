# 场景归档、检索与补传

Edge 将每个完整 v2 场景和对应的原片证据一起提交到本机 SQLite，写盘成功后即可离线查看。比赛 ID 和场景周期是复算的边界。每个周期注册生成它的输入、配置与软件信息。相同配置可重跑，同键快照或原帧证据冲突会保留已有结果并报错。改用新的输入、标定或算法进行复算时，另取 `streamEpoch`。

以下示例假定已有 `run.sqlite`，比赛 ID 为 `training`，运行周期为 `run-01`。可从仓库根目录执行。

```sh
swift run --package-path edge pitchtrace-edge match-info run.sqlite training run-01
swift run --package-path edge pitchtrace-edge scene-evidence run.sqlite training run-01 25
swift run --package-path edge pitchtrace-edge export-scenes run.sqlite training run-01 > scenes.ndjson
```

`match-info` 返回该周期的运行记录。证据按场景序号查询，供调用方用机位、媒体周期、片段和 PTS 找回原帧。已归档场景缺少证据时查询返回 JSON `null`，场景键不存在时报错。

`export-scenes` 按序号升序逐行输出完整快照。可组合使用 `--from-ms`、`--to-ms`（比赛时间，含两端）、`--track-id`、`--event-label`（精确匹配事件的 `label`）和 `--calibration-id`，无匹配时输出为空。

```sh
swift run --package-path edge pitchtrace-edge export-scenes run.sqlite training run-01 \
  --from-ms 20000 --to-ms 26000 --track-id player-1 > player-1.ndjson
```

启动本地 v2 网关后，发布程序会优先提交当前最大序号的快照，再按序补传历史。补传过程中有更新序号时会优先处理当前帧。网关根据场景周期、序号和比赛时刻推进直播游标，较早快照仍可加入历史。通过 `--backfill-bytes-per-second` 可限制历史快照间的平均 JSON 载荷发送速率，默认连续提交。当前帧优先发送。

```sh
swift run --package-path edge pitchtrace-edge publish-scenes run.sqlite http://127.0.0.1:8766 training run-01 \
  --backfill-bytes-per-second 50000
```

每个目标网关分别保存回执。收到 HTTP 202 后，该帧才标记为已送达。进程在确认前退出、网络断开或网关不可用时，重跑相同命令即可继续。HTTP 409 表示网关已有该键但内容冲突，422 表示快照内容不符合契约。命令会报告对应键并暂停，原数据和未确认帧都留在 Edge。网关已确认而本地尚未来得及记回执时，重试相同快照仍是幂等提交。发布命令处理完当时的待办后退出，下次调用会继续处理新增的场景。运行中可并发归档、查询和补传；复制整库时可在写入结束后关闭连接再复制数据库。

测试包含真实网关的历史查询和 WebSocket 首帧检查，需 Node.js 22.13+ 及可用的本地回环端口。

```sh
python3 -B edge/Tests/Integration/test_scene_archive.py
python3 -B edge/Tests/Integration/test_retention.py
```

## 比对两次复算

在同一 SQLite 文件内分别保存两个运行周期，从仓库根目录执行以下命令。

```sh
swift run --package-path edge pitchtrace-edge compare-scenes run.sqlite training run-01 run-02
```

命令在同一 SQLite 读快照内扫描两路场景，按原曝光 UTC 精确配对；同一周期内有相同 UTC 时按序号逐个匹配。不同曝光时刻各自记为单侧帧。报告包含配对数、仅出现在各侧的帧数、标定版本变化次数。每条轨迹按 `trackId` 汇总两侧出现次数、状态变化、球队／号码／名单变更、局部骨骼变化，以及都有有效位置时的位移均值与最大值（米）。单侧或丢失的位置不参与距离平均。足球采用相同的观测与距离统计。事件按完整内容比较，给出各标签的增加／减少次数及前 10 条差异对应的曝光时刻。结果随轨迹和事件类别数增长，场景逐帧比较。

## 到期预演与清理

比赛配置中可显式设置正整数 `retentionDays`。从 run-match 创建运行目录的 UTC 时刻起计，成功完成后登记该场导出的媒体文件和场景数据库。场务先查看预演列出的文件，执行时加上 `--apply`。

```sh
swift run --package-path edge pitchtrace-edge retention ./match-run
swift run --package-path edge pitchtrace-edge retention ./match-run --apply
```

程序取得该场运行锁和场景库使用锁，再核对归属、期限与文件身份。完整的场景数据库只属于该场该周期，场景和证据内容须与完成记录的摘要一致。媒体文件路径、文件身份、长度及修改时间均须与生成清单一致。输入录像、采集原片、额外添加的文件、替换后的文件和符号链接均保留。缺少明确留存配置、运行未完成或期限未到时，预演给出对应状态。清理开始前和完成后各向运行目录的 `run-retention.jsonl` 追加审计，保留运行记录和生成物归属清单；中断后再次执行可继续处理仍存在的到期文件。
