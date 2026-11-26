# 场迹 · 本地试播网关

网关提供观赛前端、模拟比赛数据流、v1 内存转发及 v2 场景归档和订阅。内置比赛也可以直接离线观看。[观赛前端](../web/) · [离线页面](../web/dist/index.html)

## 启动本地直播接收服务

v2 归档使用 Node.js 22.13+ 的内置 SQLite。Node.js 20.19+ 可运行 v1 试播。服务程序已打包运行依赖。[Node.js SQLite 文档](https://nodejs.org/download/release/v22.13.0/docs/api/sqlite.html)

```sh
git clone https://github.com/winixi/pitchtrace-3d.git
cd pitchtrace-3d
node live/server/server.cjs
```

用浏览器打开 <http://127.0.0.1:8766/>，点击「连接直播」。

- **本机试播** 从 `/demo` 接收每秒 20 帧循环播放的模拟比赛，可验证 WebSocket 通路。
- **连接直播 → 连接数据** 订阅 `/live`。边缘端向同一服务的 `POST /frame` 发送每帧完整的场景 JSON。服务把校验通过的帧广播给订阅者。第一帧到来后画面才会显示。网页用约 100 毫秒的小缓冲插值，停帧会显示等待提示。断线后自动重连。
- 默认监听 `127.0.0.1:8766`。在可信局域网中，可通过 `PITCHTRACE_HOST` 指定 Mac 的局域网 IP、通过 `PITCHTRACE_PORT` 指定端口，观众用相应的 IP 和端口打开页面。服务采用本地开发配置，没有身份验证。
- **内置演示** 回到内置的一分钟时间轴。WebSocket 模式持续显示传入的时间戳，时长由数据源决定。

## 发布 Live

提交 Live Server 和 Live Web 的版本更新后，给该提交打一个 `live-v*` 标签并推送。下方命令以 `0.1.1` 为例。

```sh
git tag -a live-v0.1.1 -m 'PitchTrace Live 0.1.1'
git push origin live-v0.1.1
```

GitHub Actions 会测试和构建两个模块，并将网关与离线观赛页打包为 `pitchtrace-live-v0.1.1.zip`，上传到同名版本。带 `-rc.1` 等后缀的版本会标记为预发布。[Live 发布工作流](../../.github/workflows/live-release.yml)

## 边缘节点帧格式

下面的命令会发送一帧蓝队 7 号球员及足球的位置。发帧时页面应已订阅「连接数据」。

```sh
curl --request POST http://127.0.0.1:8766/frame \
  --header 'Content-Type: application/json' \
  --data '{"v":1,"t":12.4,"focus":"blue7","event":"右脚传球","ball":{"x":8.4,"y":0.12,"z":-1.8},"players":[{"id":"blue7","team":"blue","number":7,"x":8,"z":-2,"yaw":1.2,"action":"pass","phase":0.52}]}'
```

| 字段              | 约定                                                                                                                                                                                          |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `v`, `t`          | 协议版本固定为 1。时间以秒计，使用采集或重建端的同一时钟；减小的时间戳代表新片段。                                                                                                            |
| `ball`            | 足球球心的 `x,y,z` 坐标，单位米。世界原点在中圈地面，X 沿球场长边，Z 沿短边，Y 向上。地滚球的球心高度约 0.11 米。                                                                             |
| `players`         | 每帧 1–32 名球员的完整快照。每个球员有稳定 `id`、`team`、整数球衣号 `number`（0–99）、足底世界坐标 `x,z`、可选高度 `y` 和朝向 `yaw`。朝向单位弧度，0 朝 +Z，正角转向 +X。离开列表即退出场景。 |
| `team`            | 蓝队 `blue`、红队 `red`、红队门将 `gold`、蓝队门将 `green`。                                                                                                                                  |
| `action`, `phase` | 可选。动作枚举为 `idle run carry pass trap cut slide shot dive celebrate`，进度为 0 至 1。驱动动画时逐帧提供相位。                                                                            |
| `bones`           | 每位球员可选的局部关节旋转对象，键为下列骨骼名，值为归一化四元数 `[x,y,z,w]`。旋转使用该 GLB 骨架的父节点局部坐标，作为绝对局部旋转覆盖动画对应骨骼。                                         |
| `focus`, `event`  | 可选。自动跟随的球员 ID；当前动作的标识或描述（自由文本，≤64 字符）。前端按界面语言显示已知标识，未知值原样显示。内置 `/demo` 流发送动作标识。                                                                                                                  |

骨骼名为 `body`、`middle`、`neck`，以及分别带 `left_` 或 `right_` 前缀的 `shoulder`、`elbow`、`thigh`、`knee`、`ankle`。若上游只有三维关键点，请先用各骨段方向、父子关系和绑定姿态完成重定向，再发送局部四元数。场景渲染器使用传入的轨迹、球衣号和骨骼；第一阶段四路单摄的标定、识别、OCR、跨机位融合及比赛规则判断属于上游系统。

端点对合法帧返回 HTTP 202，对字段格式错误返回 422，对无法解析的 JSON 返回 400。第一阶段实拍 MVP 从现场曝光到浏览器首次绘制的期限为 5 分钟。上游排队、推理和发送后的传输、绘制共享这段时间。v1 试播网关收到合法帧即转发；端到端验收见[需求说明](../../docs/01-requirements.md)。

## v2 本地场景联调

从仓库根目录启动网关，用浏览器打开 [trial-01](http://127.0.0.1:8766/?match=trial-01)。网页会订阅 `WS /v2/matches/trial-01/live`。在另一个终端逐帧提交，依次查看空场、足球缺测和未知号码。

```sh
for sample in empty-pitch lost-ball unknown-number; do
  curl --fail-with-body --request POST http://127.0.0.1:8766/v2/matches/trial-01/frames \
    --header 'Content-Type: application/json' \
    --data-binary "@schemas/v2/examples/${sample}.json"
  printf '\n'
  sleep 2
done
```

SQLite 默认写入网关目录下的 `data/scenes.sqlite`，可用 `PITCHTRACE_DB` 指定文件。重复试播可在启动时设置 `PITCHTRACE_DB=:memory:`，每次启动从空库开始。验证重启恢复时使用磁盘文件。

| HTTP 状态 | 含义 |
| --- | --- |
| 202 | 新帧已提交归档，或同键同内容已归档。 |
| 400 | 请求体无法解析为 JSON。 |
| 409 | `(matchId, streamEpoch, seq)` 相同且内容相异。 |
| 413 | 请求体或转发快照超过 256,000 字节。 |
| 422 | 场景字段无效、轨迹 ID 重复，或帧内比赛 ID 与路径不一致。 |
| 500 | 写入归档失败。 |
| 503 | 当前运行时缺少内置 SQLite。 |

新订阅收到该场已提交的最新直播帧。重复写入和历史补传只确认归档，新周期的比赛时间需继续前进。[v2 场景规范](../../docs/02-technical-design.md#6-数据契约分发和故障恢复)

## v2 历史查询与回放

以同一个网关打开 [trial-01](http://127.0.0.1:8766/?match=trial-01)，写入上一节的样例后，点击「归档回放」。使用时间轴拖动、暂停、调整倍速或换机位。「返回直播」跳回最近的直播帧。可选时段以比赛秒数填写，单次最多 60 秒。

要加入时钟重置与迟到帧，再从仓库根目录执行以下命令。

```sh
node -e 'for (const f of require("./schemas/v2/examples/clock-reset.json")) console.log(JSON.stringify(f))' |
  while IFS= read -r frame; do
    curl --fail-with-body --request POST http://127.0.0.1:8766/v2/matches/trial-01/frames \
      --header 'Content-Type: application/json' --data-binary "$frame"
    printf '\n'
  done
```

在回放里拖到 `00:25.050` 可查看迟到的旧周期帧，`00:25.100` 为新周期的首帧。直接查询以毫秒指定闭区间。

```sh
curl --fail-with-body 'http://127.0.0.1:8766/v2/matches/trial-01/frames?fromMs=1200&toMs=25100&limit=2'
```

省略起止时间时，首个请求读取截至最新归档的 30 秒。响应中的 `bounds` 是整场归档范围，`range` 是本次查询范围，`frames` 是完整快照。把 `nextCursor` 作为 `cursor` 发送可继续下一页，到末页为 `null`。默认每页最多 100 帧，可指定 1–200 帧。单页快照载荷上限约 1 MB，无匹配帧时返回空数组，参数无效返回 400。[排序与分页语义](../../docs/02-technical-design.md#6-数据契约分发和故障恢复)

## v2 原片归档与同步复核

需要 Node.js 22.13+。两路联调样例由 FFmpeg 和 ffprobe 生成，FFmpeg 需提供 `libx264`。从仓库根目录启动网关，在另一个终端生成并导入样例。

```sh
node live/server/server.cjs
```

```sh
node live/server/tools/review-sample.mjs http://127.0.0.1:8766
```

打开 [review-01](http://127.0.0.1:8766/?match=review-01)，点击「归档回放」，拖到 `00:25.000` 后展开「同步原片复核」。A 路是红色标记、30 fps，B 路是蓝色标记、25 fps。两路使用不同的媒体 PTS，同一三维时刻均命中曝光差 0 ms。切换 A、B 后三维仍停在 `00:25.000`。拖到 `00:01.200` 可检查原片缺口提示。再次导入不同编码版本时，可给样例命令追加新的比赛 ID。

样例脚本也可用 `--recordings` 导出录像与曝光映射，并用 `--media-dir` 导入 Edge 生成的两路样例媒体和索引。[Edge 联调步骤](../../edge/README.md#用生成样例联调)

媒体接口以 `/v2/matches/{matchId}/cameras` 为前缀。

| 方法与相对路径 | 行为 |
| --- | --- |
| `PUT /{cameraId}/epochs/{mediaEpoch}/init.mp4` | 以请求体上传 fMP4 初始化文件。 |
| `PUT /{cameraId}/epochs/{mediaEpoch}/segments/{n}.m4s` | 以请求体上传关键帧起始的 H.264/fMP4 原片段。 |
| `PUT /{cameraId}/epochs/{mediaEpoch}/segments/{n}/index` | 上传曝光索引 JSON，含两份 SHA-256、片段字节数、时基、逐帧 PTS、时长和曝光 UTC。 |
| `GET /` | 返回具有完整片段的 `cameraIds`，实际路径以 `/cameras` 结尾。 |
| `GET /{cameraId}/index.m3u8` | 返回当次查询时已归档的片段列表。 |
| `GET /{cameraId}/locate?captureUtcUs=…` | 返回匹配曝光、差值、媒体 epoch、片段、PTS、时基及单段复核播放列表。缺口返回 404。 |

二进制上传需要 `X-Content-SHA256`，值为 64 位小写十六进制摘要。三件齐备且校验匹配后才对浏览器可见。同路径重传相同内容返回 202，内容或交叉校验冲突返回 409，摘要或索引格式错误返回 422。二进制单件上限 128 MiB，索引上限 1 MB。SQLite 存储媒体元数据和曝光索引，原始字节默认放在数据库同目录的 `media/`，可用 `PITCHTRACE_MEDIA_DIR` 指定位置。使用磁盘数据库和媒体目录验证重启恢复。[曝光索引格式](../../schemas/v2/media-index.schema.json)

## 重新构建试播网关

在本目录使用 Node.js 22.13+ 运行以下命令。网关直接提供 `../web/dist/index.html`。[前端构建说明](../web/README.md)

```sh
npm ci
npm test
npm run build
node server.cjs
```

网页源码位于 [live/web](../web/)。本网关从同一前端源码中复用内置比赛生成器及 v1 校验器，`server.cjs` 为已打包的 Node.js 服务。

## 许可证

场迹的原创代码采用 [MIT 许可证](../../LICENSE)。第三方代码、动作和模型沿用各自许可，详见 [素材署名](../web/licenses/CREDITS.txt) 和 [许可目录](../web/licenses/)。
