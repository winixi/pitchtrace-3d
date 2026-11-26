---
name: release-checklist
description: 发布或联调前构建并核对全部子项目产物。当要求发布、发版、构建全部、联调前检查或核对构建产物一致性时使用。
metadata:
  author: pitchtrace
  version: "1.1"
---

# 发布前构建核对清单

场迹有三个独立构建产物。发布或联调前按顺序构建并核对，确保源码与入库产物一致。

## 1. Edge（场边计算）

```sh
swift build --package-path edge
swift test  --package-path edge --disable-xctest
swift run   --package-path edge pitchtrace-edge --help
```

确认构建通过、所有 Swift Testing 用例全绿。若修改了 Python 脚本（`Geometry/`），在样例目录手动验证对应子命令（如 `calibrate`、`process-match`、`scene_archive`）。

## 2. Live Web（浏览器前端）

```sh
cd live/web
npm ci
npm test          # 必须全绿（live-frame / archive / receiver / rig / i18n）
npm run build     # 重新生成 dist/index.html
```

确认 `live/web/dist/index.html` 已更新且可离线打开。

## 3. Live Server（试播网关）

```sh
cd live/server
npm ci
npm test          # 必须全绿（需 Node.js 22.13+）
npm run build     # 重新生成 server.cjs
node server.cjs   # 启动后访问 http://127.0.0.1:8766/
```

确认能打开页面、`/demo` 有数据流、`POST /frame` 返回 202、v2 样例提交返回 202。

## 4. v2 本地联调（可选，发布有媒体变更时执行）

```sh
# 在已启动的网关旁，另开终端提交场景样例
for sample in empty-pitch lost-ball unknown-number; do
  curl --fail-with-body -X POST http://127.0.0.1:8766/v2/matches/trial-01/frames \
    -H 'Content-Type: application/json' \
    --data-binary "@schemas/v2/examples/${sample}.json"
  printf '\n'; sleep 1
done
# 浏览器打开 http://127.0.0.1:8766/?match=trial-01 核查实时推送与归档回放
```

## 5. 提交

- 入库的构建产物：`live/web/dist/index.html`、`live/server/server.cjs`。
- 提交信息用简体中文，格式 `<scope>: <描述>`，说明本次改动范围。
- 跨 Edge/Live 的契约变更同步更新 `docs/02-technical-design.md` 与 `schemas/v2/`，并用新协议版本过渡。

## 6. 核对

- 阅读 [AGENTS.md](../../../AGENTS.md) 实现状态表，确认本次发布不依赖设计中（⏳）的能力。
- 改了协议的发布必须附带 `npm test` 和 `swift test` 全绿记录。
- 若任一子项目构建或测试失败，先修复再发布；不要只发布部分产物。
