# Documentation Index

Complete documentation for PitchTrace 3D, covering requirements, design, development process, and AI-assisted development workflows.

## Core Documentation

### Product & Requirements
- [01-requirements.md](01-requirements.md) — Detailed requirements (R01–R12) and acceptance criteria (Chinese)
- [04-performance-requirements.md](04-performance-requirements.md) — Performance targets and measurement methods (Chinese)
- [05-hardware-selection.md](05-hardware-selection.md) — Camera and compute hardware selection criteria (Chinese)
- [09-eight-camera-hardware-architecture.md](09-eight-camera-hardware-architecture.md) — 8-camera network & distributed edge hardware architecture options (Chinese)

### Technical Design
- [02-technical-design.md](02-technical-design.md) — System architecture, module boundaries, data contracts (Chinese)
- [08-edge-web-ui-design.md](08-edge-web-ui-design.md) — Edge Web UI technical design for monitoring and control (Chinese)

### Development Planning
- [03-development-plan.md](03-development-plan.md) — Implementation roadmap and release conditions (Chinese)
- [06-real-time-reconstruction-assessment.md](06-real-time-reconstruction-assessment.md) — Real-time 3D reconstruction feasibility analysis (Chinese)
- [07-edge-development-plan.md](07-edge-development-plan.md) — Edge module requirements and development plan (Chinese)

## AI-Assisted Development

This project was built using Kiro AI with custom configuration for steering, hooks, agents, and skills.

### Kiro Documentation
- [**KIRO_DEVELOPMENT.md**](KIRO_DEVELOPMENT.md) ⭐ — Full case study: how Kiro's configuration enabled efficient development of this ~15,000-line codebase
- [**KIRO_DEVELOPMENT.zh-CN.md**](KIRO_DEVELOPMENT.zh-CN.md) — Chinese version of the case study
- [**KIRO_QUICK_REFERENCE.md**](KIRO_QUICK_REFERENCE.md) — Quick reference for Kiro configuration patterns used in this project

### What You'll Learn

The Kiro documentation explains:
- **Steering files** (`.kiro/steering/`) — Always-loaded context that shapes Kiro's understanding
- **Hooks** (`.kiro/hooks/`) — Event-triggered automation (e.g., "run tests after protocol changes")
- **Agents** (`.kiro/agents/`) — Scoped sub-agents with file/command permissions (e.g., `edge-dev` for Swift work)
- **Skills** (`.kiro/skills/`) — Reusable multi-step procedures (e.g., release checklist)

### Real Examples from This Project

The case study includes concrete examples:
- How hooks prevented broken demos by automatically running tests after protocol changes
- How agent permissions kept Edge (Swift) and Live (Node.js) work properly scoped
- How the release checklist skill standardized the multi-step build/verify/commit procedure
- Quantified improvements: 95% test coverage per change (up from 40%), 98% regressions caught before commit (up from 60%)

## Repository Structure

See [AGENTS.md](../AGENTS.md) for agent-facing project guide (Chinese), including:
- Repository layout and module boundaries
- Build and test commands for all subsystems
- Implementation status table (what's implemented vs. in-design)
- Data contracts and coordinate system conventions

## Getting Started

1. **For users**: Start with [README.md](../README.md) or [README.zh-CN.md](../README.zh-CN.md)
2. **For developers**: Read [AGENTS.md](../AGENTS.md), then dive into subsystem README files:
   - [edge/README.md](../edge/README.md) — Edge CLI usage and Python toolchain
   - [live/web/README.md](../live/web/README.md) — Browser frontend development
   - [live/server/README.md](../live/server/README.md) — Local gateway setup
3. **For Kiro users**: Start with [KIRO_QUICK_REFERENCE.md](KIRO_QUICK_REFERENCE.md), then read the full [KIRO_DEVELOPMENT.md](KIRO_DEVELOPMENT.md) case study

## Configuration Directory

`.kiro/` — Kiro configuration (steering, agents, hooks, skills)
- `steering/` — Product vision, repository structure, build commands, protocols, testing requirements
- `agents/` — edge-dev (Swift/Edge), live-web-dev (Node/Live)
- `hooks/` — test-on-protocol-change, rebuild-on-server-source-change, test-on-edge-swift-change
- `skills/release-checklist/` — Full build verification and release procedure

Explore the configuration to see reusable patterns for multi-language projects with independent subsystems.
