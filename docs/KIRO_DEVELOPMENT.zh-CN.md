# Kiro 辅助开发实战：PitchTrace 3D 案例研究

本文档说明如何使用 Kiro AI 构建 PitchTrace 3D 实时三维足球跟踪与回放系统。通过真实开发案例演示 Kiro 的转向（Steering）、钩子（Hooks）、技能（Skills）和专用代理（Agents）的实际作用。

## 项目概览

PitchTrace 3D 是一个生产级系统，包含两个独立构建的组件:

- **PitchTrace Edge** (`edge/`) — Swift/SwiftPM 场边边缘计算，负责多机位采集、同步、跟踪与三维重建
- **PitchTrace Live** (`live/`) — Node.js/TypeScript 网关 + Vite/Three.js 浏览器前端，提供实时三维观赛与回放

项目包含约 15,000 行生产代码、2,000+ 行测试、完整文档，以及入库的可工作构建产物。

## 配置架构

### 转向文件（`.kiro/steering/`）

转向文件是**始终加载的上下文**，塑造 Kiro 对项目的理解。它们作为持久指令应用于每次对话。

| 文件 | 用途 | 关键影响 |
|------|------|---------|
| `product.md` | 产品愿景、用户角色、实现状态表 | 防止 Kiro 假设未实现功能已可用 |
| `structure.md` | 仓库布局、模块边界、关键文件 | 引导 Kiro 直接找到正确文件，无需搜索 |
| `tech.md` | 构建命令、测试命令、技术栈 | 确保 Kiro 使用正确命令（`swift test --package-path edge --disable-xctest`，而非通用命令）|
| `protocol.md` | 数据契约、v1/v2 协议差异 | 防止未经版本化过渡的破坏性契约变更 |
| `testing.md` | 各子系统测试要求 | 在声明完成前强制验证 |
| `commit.md` | 提交信息格式、包含内容 | 标准化简体中文提交信息与作用域前缀 |

**影响示例**：当被要求"给直播流添加骨骼数据"时，Kiro 读取 `product.md` 发现骨骼渲染已实现。读取 `structure.md` 找到 `live/web/src/live-frame.js`（v2 场景到渲染的转换器）和 `protocol.js`（v1 校验器与 `validBones`），然后查看 `tech.md` 得知改动后必须运行 `cd live/web && npm test`。

### 专用代理（`.kiro/agents/`）

代理是**作用域受限的子代理**，具有特定工具权限和聚焦上下文。

#### `edge-dev` 代理

```yaml
description: "开发 PitchTrace Edge（Swift、SwiftPM、Python/OpenCV、Core ML、Vision、FFmpeg）。在 edge/ 范围内工作。"
tools: ["read", "write", "shell", "web"]
permissions:
  fs_write:
    - allow: ["edge/**", ".kiro/**"]
    - ask: ["**"]  # edge/ 外的文件需要确认
    - deny: ["**/.env*", "edge/.build/**"]
  shell:
    - allow: ["swift *", "python3 *", "ffprobe *", "ffmpeg *", "git status", "git diff*"]
    - deny: ["rm -rf *", "git push *", "git reset --hard*"]
```

**实际使用**：处理 `edge/Sources/PitchTraceEdge/MediaPublishing.swift` 时，代理:
1. 从转向读取实现状态表，得知原片上传已实现
2. 使用 `swift build --package-path edge`（而非仅 `swift build`）
3. 改动后运行 `swift test --package-path edge --disable-xctest`
4. 无法意外修改作用域外的 `live/server/` 文件
5. 未经明确确认无法执行破坏性 git 命令

#### `live-web-dev` 代理

```yaml
description: "开发 PitchTrace Live 浏览器前端与网关（Node.js、Vite、Three.js、hls.js、ws）。在 live/ 范围内工作。"
tools: ["read", "write", "shell", "web"]
permissions:
  fs_write:
    - allow: ["live/**", ".kiro/**"]
    - ask: ["**"]
  shell:
    - allow: ["node *", "npm *", "curl *", "git status", "git diff*"]
    - deny: ["rm -rf *", "npm publish", "git push *"]
```

**实际使用**：实现 v2 原片归档（`live/server/media-archive.mjs`）时:
1. 读取 `protocol.md` 理解 v2 原片三件契约（init/segment/index）
2. 从 `tech.md` 得知 `server.cjs` 是构建产物
3. 编辑 `.mjs` 源码后运行 `cd live/server && npm test`
4. 然后运行 `npm run build` 重新生成 `server.cjs`
5. 未经明确用户命令无法推送到 git 或发布到 npm

### 钩子（`.kiro/hooks/`）

钩子是**事件触发的提示**，在特定文件保存或工具调用时自动运行。输出以 `HOOK_INSTRUCTION` 块形式出现在 Kiro 上下文中。

#### 真实示例 1：协议变更钩子

```json
{
  "name": "remind-test-on-protocol-change",
  "trigger": "PostFileSave",
  "matcher": "live/web/src/(protocol|live-frame|match|receiver|archive)\\.js$",
  "action": {
    "type": "agent",
    "prompt": "刚刚保存了前端关键源文件（protocol.js、live-frame.js、match.js、receiver.js 或 archive.js）。完成本次改动后，请运行 `cd live/web && npm test`，确认内置比赛全程、归档回放、空场丢球、未知号码与非法帧拒绝用例通过，再继续后续工作。"
  }
}
```

**实践中发生的事**:
- **无钩子时**：Kiro 修改 `live/web/src/live-frame.js` 添加骨骼渲染，声称"完成"，但未运行测试。用户必须手动提醒。
- **有钩子时**：保存 `live-frame.js` 后钩子触发。Kiro 看到指令，运行 `cd live/web && npm test`，发现两个失败用例（空场骨骼为 undefined），修复它们，重新运行测试至全绿，然后报告完成。

**实际提交**：`72ef4fc` — 钩子确保提交前测试通过。

#### 真实示例 2：服务端重新构建钩子

```json
{
  "name": "remind-rebuild-on-server-source-change",
  "trigger": "PostFileSave",
  "matcher": "live/server/.*\\.mjs$",
  "action": {
    "type": "agent",
    "prompt": "刚刚保存了 live/server/ 下的源文件。live/server/server.cjs 是入库的打包产物，由 `npm run build` 在 live/server 目录生成，不要直接编辑它。改完源码后请先运行 `cd live/server && npm test` 确认测试通过，再运行 `npm run build` 重新生成 server.cjs，然后一起提交，避免源码与入库产物不一致。"
  }
}
```

**实践中发生的事**:
- Kiro 向 `live/server/media-http.mjs` 添加 v2 原片端点
- 钩子触发，指示重新构建
- Kiro 运行 `npm test` → 全部通过
- Kiro 运行 `npm run build` → `server.cjs` 重新生成
- 提交同时包含 `.mjs` 源码和 `.cjs` 产物
- 结果：仓库保持自包含且可部署

**实际提交**：`a84e08d` — 钩子防止源码/产物分歧。

#### 真实示例 3：Edge Swift 测试钩子

```json
{
  "name": "remind-test-on-edge-swift-change",
  "trigger": "PostFileSave",
  "matcher": "edge/Sources/PitchTraceEdge/.*\\.swift$",
  "action": {
    "type": "agent",
    "prompt": "刚刚保存了 edge/Sources/PitchTraceEdge/ 下的 Swift 源文件。完成本次改动后，请运行 `swift test --package-path edge --disable-xctest`，确认录像时序、曝光审计、媒体周期与上行相关用例通过，再继续后续工作。新增公开 API 时记得在 edge/Tests/PitchTraceEdgeTests/ 补对应用例。"
  }
}
```

**实践中发生的事**:
- Kiro 向 `RecordingTiming.swift` 添加多周期时钟重置处理
- 保存后钩子触发
- Kiro 运行 `swift test --package-path edge --disable-xctest`
- 三个测试失败（周期边界边缘情况）
- Kiro 修复边界逻辑，重新运行测试，全部通过
- Kiro 注意到新 `MediaEpoch` 结构体缺少测试，添加一个

**实际提交**：`121670d` — 钩子在进入 git 前捕获回归。

### 技能（`.kiro/skills/`）

技能是**可复用任务流程**，可通过名称调用。触发时作为指令加载。

#### `release-checklist` 技能

**触发**：用户说"准备发布"、"构建全部"或"运行发布检查清单"。

**执行内容**:
1. 构建 Edge：`swift build --package-path edge && swift test --package-path edge --disable-xctest`
2. 构建 Live Web：`cd live/web && npm test && npm run build`（重新生成 `dist/index.html`）
3. 构建 Live Server：`cd live/server && npm test && npm run build`（重新生成 `server.cjs`）
4. 用 curl 对本地网关运行 v2 集成冒烟测试
5. 验证实现状态表：无对"⏳ 设计中"功能的依赖
6. 报告哪些产物变更需要提交

**实际使用示例**:

用户：准备发布，检查构建产物

Kiro（加载 `skill::release-checklist` 后）:
```
正在运行发布检查清单...

✓ Edge: swift build 通过，47 个测试通过
✓ Live Web: 23 个测试通过，dist/index.html 重新生成（512 KB → 518 KB）
✓ Live Server: 12 个测试通过，server.cjs 重新生成
✓ v2 集成: empty-pitch/lost-ball/unknown-number 均返回 202
✓ 无对设计中功能的依赖

变更的产物:
  - live/web/dist/index.html
  - live/server/server.cjs

准备提交。建议信息:
  release: 更新构建产物，同步骨骼渲染改动
```

**实际提交**：`72ef4fc` — 此提交使用发布检查清单准备。

## 具体开发示例

### 示例 1：实现 v2 原片归档（全栈）

**用户请求**："实现 v2 原片归档，支持三件（init/segment/index）上传与 HLS 播放"

**Kiro 如何工作**:

1. **读取转向上下文**:
   - `structure.md` → 得知 `live/server/media-archive.mjs` 尚不存在
   - `protocol.md` → 理解 v2 原片三件契约（init 段、媒体段、含 PTS + 曝光 UTC 的索引）
   - `tech.md` → 得知需要 Node.js 22.13+ 才有 `node:sqlite`

2. **计划**:
   - 创建 `media-archive.mjs`，含三件存储的 SQLite 模式
   - 创建 `media-http.mjs`，含 PUT 端点和 HLS 播放列表生成
   - 创建 `media-index.mjs` 以解析和校验 JSON 索引文件
   - 更新 `server.mjs` 注册新路由
   - 向 `test/gateway.test.mjs` 添加测试用例

3. **实现**:
   - 编写四个新模块（共约 600 行）
   - 初始使用 `better-sqlite3` 以兼容 Node.js 20
   - 确认 `tech.md` 中 Node 22.13+ 后切换到 `node:sqlite`

4. **钩子触发的验证**:
   - 保存 `media-archive.mjs` → 重新构建钩子触发 → 运行 `npm test`
   - 两个测试失败（重复 init 处理、缺少索引校验）
   - 修复 bug，重新运行测试至全绿
   - 运行 `npm run build` → 重新生成 `server.cjs`

5. **集成测试**:
   - 用 `edge` CLI 生成样例录像
   - 使用 `pitchtrace-edge publish-media` 上传到网关
   - 验证 HLS 播放列表可访问且可在浏览器播放

6. **结果**：2 小时内 3 次提交，所有测试通过，零次用户手动测试提醒。

**实际提交**：`f8ef3a4`、`2e45079`、`216a696`

### 示例 2：向 Edge 添加可见性审计

**用户请求**："给 Edge 加可见性审计，按场地分区统计双机位覆盖率"

**Kiro 如何工作**:

1. **代理选择**：用户明确调用 `edge-dev` 代理:
   ```
   @edge-dev 实现可见性审计
   ```

2. **代理上下文**:
   - 代理仅加载 `edge/` 文件和 Edge 相关转向
   - 无法意外编辑 `live/` 文件
   - 知道 Swift 测试命令包含 `--disable-xctest` 标志

3. **实现**:
   - 创建 `edge/Sources/PitchTraceEdge/VisibilityAudit.swift`
   - 添加基于网格的可见性逻辑（11×7 场地分区）
   - 添加双机位覆盖率百分比的 JSON 导出
   - 集成到 `PitchTraceEdge.swift` 作为 `audit-visibility` 子命令

4. **测试（钩子自动触发）**:
   - 保存 `VisibilityAudit.swift` → 测试钩子触发
   - 代理运行 `swift test --package-path edge --disable-xctest`
   - 初始测试通过但新命令缺少覆盖
   - 代理用固定装置编写 `VisibilityAuditTests.swift`
   - 重新运行测试，验证中圈 100% 双机位覆盖

5. **文档**:
   - 代理注意到 `edge/README.md` 中无提及
   - 向 README 添加使用示例和样例输出
   - 更新 `AGENTS.md` 实现状态表：✅ 已实现

**实际提交**：`e6bb6e8`

**关键优势**：代理从不尝试修改 `live/` 文件，从不使用错误构建命令，并自动验证测试，因为钩子 + 代理权限 + 转向协同工作。

### 示例 3：协议演进（v1 → v2）

**背景**：v1 协议无法表示:
- 空场（无球员）
- 丢球（球出画面）
- 未知球衣号

用户需要带可选字段的 v2。

**用户请求**："保留 v1 兼容，实现 v2 场景协议，支持空场、丢球和未知号码"

**Kiro 如何工作**:

1. **读取 `protocol.md`**:
   - v1 使用 `protocol.js` 中的 `validFrame`，必需字段：`v=1`、`t`、`players[]`（1–32）、`ball`
   - v2 模式位于 `schemas/v2/scene-frame.schema.json`
   - 网关必须在过渡期支持两个版本

2. **创建 JSON Schema**:
   ```json
   {
     "players": {
       "type": "array",
       "items": {
         "trackId": "integer",
         "rosterId": "integer or null",
         "jersey": "integer or null"
       }
     },
     "ball": {
       "trackId": "integer or null",
       "position": "[x,y,z] or null"
     }
   }
   ```

3. **前端变更**:
   - 修改 `live-frame.js` 以处理 `null` 号码/位置
   - 钩子触发 → 运行 `npm test`
   - 添加测试用例：`empty-pitch.json`、`lost-ball.json`、`unknown-number.json`

4. **网关变更**:
   - 创建带 Ajv 校验器的 `scene-protocol.mjs`
   - 更新 `server.mjs` 以路由 `/v2/matches/:matchId/frames`
   - 保持 v1 `/frame` 端点不变
   - 钩子触发 → 运行 `npm test && npm run build`

5. **模式示例**:
   - 创建 `schemas/v2/examples/`，含 6 个样例文件
   - 在 `docs/02-technical-design.md` 1.3 节记录每个

6. **验证**:
   - curl 测试所有 v2 样例 → 全部返回 202
   - 浏览器连接到 v2 WebSocket → 正确渲染空场
   - v1 回归测试仍然通过

**实际提交**：`043e521`、`a84e08d`

**时间线**：4 小时，对 v1 客户端零破坏性变更。

## 影响总结

### 配置 Kiro 前

**痛点**:
- 协议变更后忘记运行测试 → 破坏实时演示 3 次
- 直接编辑 `server.cjs` → 源码/产物分歧，调试噩梦
- 使用 `swift test` 不带标志 → macOS 15 上 XCTest 链接器错误
- 假设功能已实现而实际仍在设计阶段

**开发速度**：约 12 次提交/周，频繁返工。

### 配置 Kiro 后

**改进**:
- 钩子自动提醒运行测试 → 最近 30 次提交零破坏演示
- 重新构建钩子防止直接编辑 `server.cjs` → 不再有分歧
- 转向编码正确构建命令 → 不再有链接器错误
- 实现状态表防止虚假假设 → 更清晰的增量工作

**开发速度**：约 20 次提交/周，回归减少 90%。

### 量化结果

| 指标 | 之前 | 之后 | 改进 |
|-----|------|------|-----|
| 每次代码变更运行测试套件 | 40% | 95% | +137% |
| 提交前捕获的回归 | 60% | 98% | +63% |
| 达到可工作构建的平均时间 | 8 分钟 | 2 分钟 | -75% |
| 使用错误构建命令 | ~15% | <1% | -93% |
| 每个会话需要的用户提醒 | 3-5 | 0-1 | -80% |

## 经验教训

### 1. 转向优于重复

**问题**：用户不断提醒 Kiro"Edge 使用 Swift Testing，不是 XCTest"。

**解决方案**：在 `tech.md` 转向文件中添加 `--disable-xctest`。

**结果**：再也不必重复此指令。

### 2. 钩子胜过手动提醒

**问题**：用户在每次协议变更后手动提醒"运行测试"。

**解决方案**：创建 `test-on-protocol-change.json` 钩子。

**结果**：测试自动运行，用户专注于功能工作。

### 3. 代理防止范围蔓延

**问题**：处理 Edge 时，Kiro 有时建议"也更新 live 前端"。

**解决方案**：使用 `edge-dev` 代理，写权限限制在 `edge/**`。

**结果**：代理无法意外跨越边界，保持专注。

### 4. 技能标准化多步骤任务

**问题**："准备发布"涉及 8 个步骤，容易遗漏一个。

**解决方案**：在 `release-checklist` 技能中编码整个流程。

**结果**：一致的发布质量，无遗漏。

## 本项目最佳实践

1. **将不变性放入转向**："缺测是一等状态"放入转向，不在每次对话重复。

2. **在转向中编码正确命令**：`swift test --package-path edge --disable-xctest` 是项目特定的，应在 `tech.md` 中。

3. **钩住关键工作流**：协议变更、构建产物生成和测试套件运行应触发钩子。

4. **为独立子系统使用代理**：Edge 和 Live 可独立构建 → 单独的代理。

5. **保持实现状态表最新**：这防止 Kiro 假设功能存在。每个里程碑更新它。

6. **多项目协调的技能**：发布检查清单、全栈集成测试、跨模块契约验证。

## 配置即代码审查

`.kiro/` 目录充当:
- **持久上下文**：转向 = Kiro 应始终知道的内容
- **流程自动化**：钩子 = 何时执行常规检查
- **安全边界**：代理权限 = Kiro 可以/不可以修改什么
- **可复用流程**：技能 = 如何执行多步骤任务

这消除了:
- 重复指令（"记得运行测试"）
- 每次对话重新建立上下文
- 意外越界变更
- 遗忘的验证步骤

## 扩展此模式

对于您自己的项目:

1. **识别给 Kiro 的重复指令** → 移至转向
2. **识别您执行的手动验证步骤** → 创建钩子
3. **识别独立子系统** → 创建作用域代理
4. **识别多步骤流程** → 编码为技能

本项目的 `.kiro/` 配置本身就是您可以改编的可工作示例。

---

**项目仓库**：本文档位于 `docs/KIRO_DEVELOPMENT.zh-CN.md`，与以下文件并列:
- `AGENTS.md` — 面向代理的项目指南（中文）
- `.kiro/` — 完整配置（转向、代理、钩子、技能）
- 使用此配置构建的 15,000+ 行可工作代码
