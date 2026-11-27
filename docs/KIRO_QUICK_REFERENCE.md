# Kiro Configuration Quick Reference

Fast lookup for Kiro's `.kiro/` directory structure and usage patterns in this project.

## Directory Structure

```
.kiro/
├── steering/          # Always-loaded context (project knowledge)
│   ├── product.md     # What's implemented vs. in-design
│   ├── structure.md   # Repository layout & key files
│   ├── tech.md        # Build/test commands
│   ├── protocol.md    # Data contracts (v1/v2)
│   ├── testing.md     # Test requirements
│   └── commit.md      # Commit message format
├── agents/            # Scoped sub-agents with permissions
│   ├── edge-dev.md    # Swift/Edge work (edge/** only)
│   └── live-web-dev.md # Node/frontend work (live/** only)
├── hooks/             # Event-triggered automation
│   ├── test-on-protocol-change.json
│   ├── rebuild-on-server-source-change.json
│   └── test-on-edge-swift-change.json
└── skills/            # Multi-step reusable procedures
    └── release-checklist/
        └── SKILL.md   # Full build & verification checklist
```

## When to Use What

| Need | Use | Example |
|------|-----|---------|
| Kiro forgets project-specific commands | Add to `steering/tech.md` | `swift test --package-path edge --disable-xctest` |
| Kiro assumes unimplemented features exist | Update `steering/product.md` status table | Mark "⏳ 设计中" vs "✅ 已实现" |
| Repeatedly remind "run tests after change" | Create a hook | `test-on-protocol-change.json` |
| Keep work scoped to Edge vs. Live | Use specialized agent | `@edge-dev` for Swift work |
| Multi-step release procedure | Encode as skill | `/release-checklist` command |
| Prevent accidental `server.cjs` edits | Hook on `.mjs` save | `rebuild-on-server-source-change.json` |

## Common Commands

### Invoke Specialized Agents

```bash
# Work on Edge (Swift/Python)
@edge-dev <your request>

# Work on Live (Node/TypeScript/frontend)
@live-web-dev <your request>
```

### Activate Skills

```bash
# Run full release checklist
/release-checklist

# Or just mention it
准备发布，检查构建产物
```

### Build & Test (from steering/tech.md)

```bash
# Edge
swift build --package-path edge
swift test --package-path edge --disable-xctest

# Live Web
cd live/web && npm ci && npm test && npm run build

# Live Server
cd live/server && npm ci && npm test && npm run build
```

## Hook Triggers

| File Pattern | Hook | Action |
|-------------|------|--------|
| `live/web/src/(protocol\|live-frame\|match\|receiver\|archive)\.js` | test-on-protocol-change | Reminds to run `npm test` in `live/web` |
| `live/server/.*\.mjs` | rebuild-on-server-source-change | Reminds to `npm test && npm run build` |
| `edge/Sources/PitchTraceEdge/.*\.swift` | test-on-edge-swift-change | Reminds to run Swift tests |

## Agent Permissions Summary

### edge-dev
- ✅ Read: `**`
- ✅ Write: `edge/**`, `.kiro/**`
- ❓ Write: `**` (asks first)
- ❌ Write: `**/.env*`, `edge/.build/**`
- ✅ Shell: `swift`, `python3`, `ffprobe`, `git status/diff/log`
- ❌ Shell: `rm -rf`, `git push`, `git reset --hard`, `git clean`

### live-web-dev
- ✅ Read: `**`
- ✅ Write: `live/**`, `.kiro/**`
- ❓ Write: `**` (asks first)
- ✅ Shell: `node`, `npm`, `curl`, `git status/diff/log`
- ❌ Shell: `rm -rf`, `npm publish`, `git push`

## Steering File Purposes

| File | Contains | Why It Matters |
|------|----------|----------------|
| `product.md` | Implementation status table, user roles | Prevents false assumptions about what exists |
| `structure.md` | Directory tree, key file paths, module boundaries | Direct navigation without search |
| `tech.md` | Exact build commands with all flags | Avoids linker errors, missing flags |
| `protocol.md` | v1/v2 contract fields, versioning rules | Prevents breaking changes |
| `testing.md` | Per-module test requirements | Enforces verification before "done" |
| `commit.md` | Chinese format, scope prefixes | Consistent git history |

## Real-World Usage Patterns

### Pattern 1: Protocol Change
```
User: 给 v2 场景加球队字段
Kiro:
1. Reads protocol.md → understands v2 structure
2. Edits live/web/src/live-frame.js
3. Hook fires → runs npm test
4. Tests fail → fixes bugs
5. Re-runs tests → all pass
6. Edits live/server/scene-protocol.mjs
7. Hook fires → runs npm test && npm run build
8. Reports completion with both source & artifact updated
```

### Pattern 2: Scoped Work
```
User: @edge-dev 添加曝光审计
Kiro (as edge-dev agent):
1. Only loads edge/ context
2. Creates edge/Sources/PitchTraceEdge/ExposureAudit.swift
3. Saves file → hook fires
4. Runs swift test --package-path edge --disable-xctest
5. Cannot touch live/ files (permission denied)
```

### Pattern 3: Release Preparation
```
User: /release-checklist
Kiro (loads skill):
1. Builds Edge → swift build & test
2. Builds Live Web → npm test & build
3. Builds Live Server → npm test & build
4. Runs v2 integration tests with curl
5. Checks implementation status table
6. Reports changed artifacts
7. Suggests commit message
```

## Troubleshooting

| Problem | Cause | Solution |
|---------|-------|----------|
| Kiro uses `swift test` without flags | Not in steering | Add full command to `tech.md` |
| Kiro assumes feature X exists | Status table outdated | Update `product.md` table |
| Forgot to run tests after change | No hook | Create hook for file pattern |
| Kiro suggests out-of-scope changes | No agent boundary | Use `@edge-dev` or `@live-web-dev` |
| Multi-step task incomplete | No checklist | Encode as skill |

## Adding Your Own Configuration

### New Steering File

```markdown
---
inclusion: always
---
# Your Content

This file is loaded in every conversation.
```

### New Hook

```json
{
  "version": "v1",
  "hooks": [
    {
      "name": "your-hook-name",
      "trigger": "PostFileSave",
      "matcher": "path/pattern/.*\\.ext$",
      "action": {
        "type": "agent",
        "prompt": "Instruction to Kiro after this file saves"
      }
    }
  ]
}
```

### New Agent

```markdown
---
name: your-agent
description: What this agent does
tools: ["read", "write", "shell"]
permissions:
  rules:
    - capability: fs_write
      match: ["your/scope/**"]
      effect: allow
---

Agent instructions here
```

### New Skill

```markdown
---
name: your-skill
description: When to use this skill
---

# Your Skill Name

Step-by-step procedure
```

## Best Practices from This Project

1. **Steering = Knowledge**: Put project invariants here, not in conversation
2. **Hooks = Automation**: Automate routine checks (tests, builds, verifications)
3. **Agents = Boundaries**: Keep subsystems isolated with scoped permissions
4. **Skills = Procedures**: Encode complex multi-step tasks once

## References

- Full case study: `KIRO_DEVELOPMENT.md` (English) / `KIRO_DEVELOPMENT.zh-CN.md` (Chinese)
- Project context: `AGENTS.md` (agent-facing project guide)
- Configuration directory: `.kiro/` (steering, agents, hooks, skills)
