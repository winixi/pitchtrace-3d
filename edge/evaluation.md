# 离线独立真值评估

从仓库根目录运行。评估只读指定比赛、周期的 Edge 场景归档，结果 JSON 写入标准输出。

```sh
swift run --package-path edge pitchtrace-edge evaluate-match scenes.sqlite training annotated truth.ndjson
```

首行声明比赛和样本来源。针对相同的曝光计划，可用同一份参考分别评估不同重算周期。后续每一帧使用严格递增的曝光 UTC，列出该取样时刻覆盖区内的全部球员和可见球。坐标与场景同为 [X,Y,Z] 米，`personId` 表示独立参考中的同一个人。合成输入的真值可取自投影前的原始场景，实拍参考应来自独立装置或经复核的标注。下面给出两帧参考与一个动作的格式示例。

```jsonl
{"type":"evaluation","matchId":"training","sampleSource":"synthetic","sampleDurationUs":100000,"eventWindow":{"startUtcUs":"1000000","endUtcUs":"1100000"}}
{"captureUtcUs":"1000000","players":[{"personId":"p7","positionM":[0,0,0],"number":7,"numberSegmentId":"first","joints":{"leftAnkle":[0.2,0.1,0]}}],"ball":{"kind":"ground","positionM":[0.3,0.11,0]}}
{"captureUtcUs":"1100000","players":[{"personId":"p7","positionM":[0.1,0,0],"number":7,"numberSegmentId":"first","joints":{"leftAnkle":null}}],"ball":{"kind":"high"}}
{"type":"event","kind":"kick","personIds":["p7"],"startUtcUs":"1000000","endUtcUs":"1100000"}
```

- **人员与身份。** 按精确曝光匹配，一条轨迹在同帧最多匹配一个人。先最大化距离门限内的配对数，再最小化三维足点距离。默认门限 2 米，可在首行填写 `personGateM`。只有实测位置计入覆盖率。缺整帧、预测、丢失及门限外的参考人员均保留在分母，额外实测轨迹计入 `falsePositives`。输出匹配样本的平均、最近秩 P95 误差和样本数。同一参考人物两次有效匹配的轨迹 ID 改变计一次切换。可选 `sampleDurationUs` 声明每个参考曝光代表的非重叠取样时长，据此换算每 10 球员分钟的切换数。
- **球与关节。** 可见球按 `ground` 和 `high` 分组，只有具备独立坐标时才计入米制误差，缺少坐标的可见球仍计覆盖率。`ball:null` 表示本帧已检查、画面中无可见球，此时模型若给出实测球，计入误报。每个列出的关节表示可见目标，值为 `null` 时只评覆盖率。未标注的关节省略。评分取自原始三角化的 `measured` 关节。
- **号码。** 同一人物的可读时段使用一致的 `numberSegmentId` 和参考号码。一个时段出现自动确认号码即计为已确认，只有该段全部已确认值与真值一致才计正确。未知、全程无检测及只有人工修订的可读时段留在覆盖率分母。不可读时省略号码和段 ID。
- **动作。** `eventWindow` 为按候选开始时刻划定的完整标注区间，两端均含。评估检查该窗内出现的所有候选，逐类统计 TP、FP、FN、精确率、召回率和 F1。没有完整动作标注时省略窗口，此时动作评估为 `null`。候选需与参考类别、参与者顺序一致，起始时刻相差不超过 1 秒。优先匹配最多的事件，再最小化总起始时差。对有持续时间的已匹配参考事件另报时间区间交并比均值和样本数。按候选区间内参考曝光的人员配对还原轨迹身份，缺少区间内标注时，取前后各 1 秒内最近的参考曝光核对。有歧义或无可用配对时计作未命中。同一真值动作最多命中一次。类别为 `pass`、`kick`（含 `shot`）、`dribble`、`beat`、`tackle`。宏平均仅包含存在参考或候选的类别，同时报告 `macroClasses`。无样本的比例及误差均为 `null`。
